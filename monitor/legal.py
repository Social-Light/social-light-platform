"""Reading legal documents and recording consent.

Everything that touches consent goes through here rather than writing
``ConsentRecord`` rows directly, so the audit fields (IP, user agent, version
snapshot) are captured the same way from every entry point — the onboarding
wizard today, a re-consent prompt or an admin action tomorrow.

The one rule worth restating: recording a decision **always writes a new row**.
Nothing here ever updates an existing consent record, and ``ConsentRecord.save()``
refuses to let it.
"""
from .onboarding_models import LEGAL_DOCUMENT_TYPES, ConsentRecord, LegalDocument


# The documents a user must accept before onboarding can complete, in the order
# they are presented. Which of these actually block is decided per document by
# `requires_acceptance`, so a document can be published for reference without
# gating anyone.
REQUIRED_DOC_TYPES = ('terms', 'privacy', 'disclaimer')

# Everything with a public page in the footer. Wider than REQUIRED_DOC_TYPES: the
# refund policy has to be readable by anyone — the card schemes and DPO require a
# published one — but it is a statement of what we do, not a consent, so it is not
# part of the onboarding gate.
PUBLIC_DOC_TYPES = REQUIRED_DOC_TYPES + ('refund', 'cookies')

DOC_TYPE_LABELS = dict(LEGAL_DOCUMENT_TYPES)


def client_ip(request):
    """The caller's IP, honouring X-Forwarded-For because this app runs behind
    nginx. Only the first hop is taken; the rest of the header is attacker-
    controlled and is not evidence of anything."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        candidate = forwarded.split(',')[0].strip()
        if candidate:
            return candidate
    return request.META.get('REMOTE_ADDR') or None


def current_document(doc_type):
    """The version of `doc_type` in force right now, or None if nothing has been
    published for it."""
    return LegalDocument.current(doc_type)


def current_documents():
    """``{doc_type: LegalDocument}`` for every required document that has a
    published version — in one query, because this runs on every page render for
    every signed-in user via the context processor.
    """
    docs = {}
    published = (LegalDocument.objects.in_force()
                 .filter(doc_type__in=REQUIRED_DOC_TYPES)
                 .order_by('doc_type', '-effective_from', '-id'))
    for doc in published:
        docs.setdefault(doc.doc_type, doc)
    return docs


def latest_decision(user, doc_type, document=None):
    """The user's most recent decision about `doc_type` — restricted to one
    specific document version when `document` is given. None if never asked."""
    qs = ConsentRecord.objects.filter(user=user, doc_type=doc_type)
    if document is not None:
        qs = qs.filter(version=document.version)
    return qs.order_by('-occurred_at', '-id').first()


def has_accepted(user, doc_type, document=None):
    """True when the user's latest decision about the document (by default the
    version currently in force) was to accept it.

    Checking the *latest* decision rather than "any acceptance exists" is what
    makes withdrawal work: a withdrawal is a newer record, so it wins, without
    the earlier acceptance being deleted.
    """
    if user is None or not user.is_authenticated:
        return False
    if document is None:
        document = current_document(doc_type)
    if document is None:
        # Nothing published to accept. Not a blocker — see `outstanding_documents`.
        return True
    decision = latest_decision(user, doc_type, document)
    return bool(decision and decision.accepted)


def outstanding_documents(user):
    """Documents in force that this user still has to accept.

    A document that has no published version cannot be outstanding, and one with
    ``requires_acceptance=False`` is informational only — neither blocks
    onboarding, so a deployment that has not published a disclaimer yet is not a
    deployment where nobody can finish signing up.
    """
    if user is None or not user.is_authenticated:
        return []

    required = {t: d for t, d in current_documents().items() if d.requires_acceptance}
    if not required:
        return []

    # The user's latest decision per document type, in one query — this is on the
    # hot path (every page render, via the context processor), so it must not be
    # a query per document.
    latest = {}
    decisions = (ConsentRecord.objects
                 .filter(user=user, doc_type__in=list(required))
                 .order_by('doc_type', '-occurred_at', '-id')
                 .only('doc_type', 'version', 'decision', 'occurred_at'))
    for record in decisions:
        latest.setdefault(record.doc_type, record)

    return [
        doc for doc_type, doc in required.items()
        if not (doc_type in latest
                and latest[doc_type].accepted
                and latest[doc_type].version == doc.version)
    ]


def record_consent(user, document, request=None, decision='accepted', source='onboarding',
                   organization=None):
    """Write one immutable consent record.

    The document's type and version are snapshotted onto the record, so the audit
    trail stays truthful even if the document row is later edited.
    """
    if organization is None:
        organization = getattr(user, 'organization', None)

    return ConsentRecord.objects.create(
        user=user,
        organization=organization,
        document=document,
        doc_type=document.doc_type,
        version=document.version,
        decision=decision,
        source=source,
        ip_address=client_ip(request) if request is not None else None,
        user_agent=(request.META.get('HTTP_USER_AGENT', '')[:400] if request is not None else ''),
    )


def consent_summary(user):
    """One row per required document type for the admin and account pages: the
    document in force, the user's latest decision about it, and whether that
    decision is current.
    """
    summary = []
    for doc_type in REQUIRED_DOC_TYPES:
        doc = current_document(doc_type)
        decision = latest_decision(user, doc_type)
        summary.append({
            'doc_type': doc_type,
            'label': DOC_TYPE_LABELS.get(doc_type, doc_type),
            'document': doc,
            'current_version': doc.version if doc else None,
            'accepted_version': decision.version if decision else None,
            'decision': decision.decision if decision else None,
            'accepted_at': decision.occurred_at if decision else None,
            'ip_address': decision.ip_address if decision else None,
            'is_current': bool(doc and decision and decision.accepted and decision.version == doc.version),
        })
    return summary
