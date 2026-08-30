"""Onboarding, identity verification and legal-consent models.

These live in their own module rather than in the already-long ``models.py``;
``models.py`` imports them at the bottom so Django registers them against the
``monitor`` app exactly as if they were declared inline. Every foreign key here
is given as a *string* reference so this module never has to import ``models``,
which would be circular.

Three ideas carry most of the weight:

**Legal documents are versioned records, not booleans.** ``LegalDocument`` holds
the wording of one version of one document; ``ConsentRecord`` holds one immutable
statement of what a named user agreed to, when, from which IP, and — crucially —
*which version*. Publishing v1.1 of the Terms leaves every v1.0 acceptance intact
and simply means users have a new document outstanding.

**Consent records are append-only.** ``ConsentRecord.save()`` refuses to modify a
row that already exists. Withdrawing or re-accepting writes a *new* record. There
is no code path anywhere in the application that can rewrite consent history.

**Onboarding is resumable.** ``OnboardingProgress`` stores the furthest state a
user has reached plus a timestamped history, so a user who abandons at the
payment step returns to the payment step rather than starting again.
"""
import secrets
from datetime import timedelta

from django.conf import settings as django_settings
from django.db import models
from django.utils import timezone


# ═══════════════════════════════════════════════════════════════════════════
#  Legal documents and consent
# ═══════════════════════════════════════════════════════════════════════════

LEGAL_DOCUMENT_TYPES = [
    ('terms', 'Terms & Conditions'),
    ('privacy', 'Privacy & Personal Data Consent'),
    ('disclaimer', 'Disclaimer'),
]

# Deliberately explicit. Nothing drafted in-product may be presented to a user as
# having been settled by a lawyer, so the status is stored on the document itself
# and surfaced in the admin and (for drafts) on the consent page.
REVIEW_STATUS_CHOICES = [
    ('draft', 'Draft — not yet reviewed'),
    ('in_review', 'With legal for review'),
    ('approved', 'Approved by legal counsel'),
]


class LegalDocumentQuerySet(models.QuerySet):
    def in_force(self, at=None):
        """Published versions whose effective date has arrived."""
        at = at or timezone.now()
        return self.filter(is_published=True, effective_from__lte=at)


class LegalDocument(models.Model):
    """One version of one legal document.

    Wording lives in the database, so the drafts shipped with the product can be
    replaced by legally approved text from the Django admin without touching the
    application code — which is the whole point of storing it here rather than in
    a template.

    A new version is a *new row*: create ``terms`` v1.1, give it an
    ``effective_from``, publish it. The v1.0 row stays exactly as it was, and so
    does every ConsentRecord pointing at it.
    """
    doc_type = models.CharField(max_length=30, choices=LEGAL_DOCUMENT_TYPES)
    version = models.CharField(max_length=20, help_text='e.g. "1.0", "1.1". Sorted by effective date, not by this string.')
    title = models.CharField(max_length=200)

    summary = models.TextField(
        blank=True,
        help_text='Short plain-language explanation shown above the document — why this is '
                  'being asked for and what it covers.')
    body = models.TextField(help_text='The document itself. Blank lines separate paragraphs.')
    consent_label = models.CharField(
        max_length=300,
        help_text='The wording next to the tick box, e.g. "I have read and accept the Terms & Conditions".')

    jurisdiction = models.CharField(
        max_length=100, default='Botswana',
        help_text='The law this version is drafted for review under.')
    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS_CHOICES, default='draft')
    review_note = models.TextField(
        blank=True,
        help_text='Internal note for whoever owns legal review — what still needs checking.')

    requires_acceptance = models.BooleanField(
        default=True,
        help_text='Untick for a document that is published for reference but not blocking onboarding.')
    is_published = models.BooleanField(
        default=False,
        help_text='Only published versions are ever shown to a user or accepted.')
    effective_from = models.DateTimeField(default=timezone.now)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LegalDocumentQuerySet.as_manager()

    class Meta:
        ordering = ['doc_type', '-effective_from', '-version']
        unique_together = [('doc_type', 'version')]

    def __str__(self):
        return f'{self.get_doc_type_display()} v{self.version}'

    @property
    def is_draft(self):
        return self.review_status != 'approved'

    @property
    def paragraphs(self):
        """The body split into paragraphs for rendering, so templates never have
        to trust stored HTML."""
        return [p.strip() for p in self.body.split('\n\n') if p.strip()]

    @classmethod
    def current(cls, doc_type, at=None):
        """The version of `doc_type` in force right now, or None."""
        return cls.objects.in_force(at).filter(doc_type=doc_type).order_by('-effective_from', '-id').first()


CONSENT_DECISIONS = [
    ('accepted', 'Accepted'),
    ('declined', 'Declined'),
    ('withdrawn', 'Withdrawn'),
]

CONSENT_SOURCES = [
    ('onboarding', 'Onboarding'),
    ('reconsent', 'Re-consent to a new version'),
    ('settings', 'Account settings'),
    ('admin', 'Recorded by an administrator'),
]


class ConsentRecord(models.Model):
    """An immutable statement that a named user made a decision about a named
    version of a legal document, at a point in time.

    ``doc_type`` and ``version`` are denormalised on purpose. The FK is there for
    convenience, but the audit record must remain readable and correct even if
    the document row is later edited — the *snapshot* of what was agreed to is
    what has legal meaning.

    Deleting a user cascades these away. That is intentional: erasing an account
    at the person's request should not leave their name and IP address behind. It
    does mean consent history is not a permanent ledger independent of the user,
    which is a point for legal review if a longer retention period is required.
    """
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='consent_records')
    organization = models.ForeignKey('monitor.Organization', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='consent_records',
                                     help_text='The organisation the user was acting for at the time.')
    document = models.ForeignKey(LegalDocument, on_delete=models.PROTECT, related_name='consents')

    # Snapshot of what was agreed to — never updated.
    doc_type = models.CharField(max_length=30, choices=LEGAL_DOCUMENT_TYPES)
    version = models.CharField(max_length=20)

    decision = models.CharField(max_length=20, choices=CONSENT_DECISIONS, default='accepted')
    source = models.CharField(max_length=20, choices=CONSENT_SOURCES, default='onboarding')

    # Audit trail.
    occurred_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ['-occurred_at', '-id']
        indexes = [models.Index(fields=['user', 'doc_type', '-occurred_at'])]

    def __str__(self):
        return f'{self.user} {self.decision} {self.get_doc_type_display()} v{self.version}'

    @property
    def accepted(self):
        return self.decision == 'accepted'

    def save(self, *args, **kwargs):
        """Append-only. Consent history is evidence; re-deciding writes a new
        record rather than editing the old one."""
        if self.pk is not None and ConsentRecord.objects.filter(pk=self.pk).exists():
            raise ValueError(
                'ConsentRecord is append-only — record a new decision instead of '
                'modifying an existing consent record.')
        if self.document_id and not self.version:
            self.doc_type = self.document.doc_type
            self.version = self.document.version
        super().save(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
#  Email verification
# ═══════════════════════════════════════════════════════════════════════════

def _default_verification_expiry():
    hours = int(getattr(django_settings, 'EMAIL_VERIFICATION_TTL_HOURS', 48))
    return timezone.now() + timedelta(hours=hours)


class EmailVerificationToken(models.Model):
    """A single-use link proving the person controls the mailbox they signed up
    with.

    The token is a URL-safe random string stored as-is. It is short-lived,
    single-use and grants nothing beyond flipping ``User.email_verified`` — it is
    not a login credential, so hashing it at rest would buy little against the
    complexity of not being able to re-send the same link.
    """
    user = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name='email_verification_tokens')
    token = models.CharField(max_length=64, unique=True, db_index=True)
    email = models.EmailField(help_text='The address this token was issued for.')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_verification_expiry)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'verification for {self.email}'

    @classmethod
    def issue(cls, user):
        """Issue a fresh token, invalidating any outstanding ones so an older
        link in an older email cannot still be used."""
        cls.objects.filter(user=user, used_at__isnull=True).delete()
        return cls.objects.create(user=user, token=secrets.token_urlsafe(32), email=user.email)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self):
        return self.used_at is None and not self.is_expired

    def consume(self):
        self.used_at = timezone.now()
        self.save(update_fields=['used_at'])


# ═══════════════════════════════════════════════════════════════════════════
#  Agency / organisation declaration
# ═══════════════════════════════════════════════════════════════════════════

ACCOUNT_TYPE_CHOICES = [
    ('individual', 'Individual user'),
    ('organisation', 'Representing an organisation or agency'),
]

DECLARATION_VERIFICATION_CHOICES = [
    ('not_required', 'No further verification required'),
    ('pending', 'Awaiting verification'),
    ('verified', 'Verified'),
    ('rejected', 'Rejected'),
]


class AgencyDeclaration(models.Model):
    """What the user declared about their relationship to the organisation whose
    workspace they are creating.

    Every account gets a tenant ``Organization`` because all content in the
    platform is organisation-scoped — an "individual user" is not organisation-less,
    they simply declare that the workspace is personal rather than corporate. It
    is *that declaration* that carries the legal meaning, and it is what an
    administrator reviews.

    ``verification_status`` and ``supporting_document`` exist so a document
    requirement can be switched on later for some or all declarations without a
    schema change. Nothing requires a document today.
    """
    user = models.OneToOneField(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='agency_declaration')
    organization = models.ForeignKey('monitor.Organization', on_delete=models.CASCADE,
                                     null=True, blank=True, related_name='agency_declarations')

    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES, default='individual')

    # Only meaningful when account_type == 'organisation'.
    organisation_name = models.CharField(max_length=200, blank=True)
    position = models.CharField(max_length=150, blank=True,
                                help_text="The user's position or title within the organisation.")
    organisation_email = models.EmailField(blank=True,
                                           help_text='Official organisation address, where different from the account address.')
    organisation_phone = models.CharField(max_length=50, blank=True)
    registration_number = models.CharField(max_length=100, blank=True,
                                           help_text='Company or entity registration number, if provided.')

    is_authorised_representative = models.BooleanField(
        default=False,
        help_text='The user explicitly confirmed they are authorised to act for the organisation.')
    authorised_at = models.DateTimeField(null=True, blank=True)
    authorisation_ip = models.GenericIPAddressField(null=True, blank=True)

    # Extension point — not required by the current business rules.
    verification_status = models.CharField(max_length=20, choices=DECLARATION_VERIFICATION_CHOICES,
                                           default='not_required')
    supporting_document = models.FileField(upload_to='agency_declarations/', blank=True, null=True,
                                           help_text='Optional. Not required by the current onboarding rules.')
    verification_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} — {self.get_account_type_display()}'

    @property
    def represents_organisation(self):
        return self.account_type == 'organisation'

    @property
    def is_complete(self):
        """Whether this declaration satisfies the current onboarding rules. An
        individual needs only to have declared; an organisation representative
        must name the organisation, give their position and confirm authority."""
        if not self.represents_organisation:
            return True
        return bool(self.organisation_name and self.position and self.is_authorised_representative)


# ═══════════════════════════════════════════════════════════════════════════
#  Onboarding state
# ═══════════════════════════════════════════════════════════════════════════

ONBOARDING_STATES = [
    ('registered', 'Registered'),
    ('email_verified', 'Email verified'),
    ('profile_completed', 'Profile completed'),
    ('agency_declared', 'Agency declared'),
    ('terms_accepted', 'Terms accepted'),
    ('privacy_accepted', 'Privacy consent given'),
    ('disclaimer_accepted', 'Disclaimer accepted'),
    ('payment_method_added', 'Payment method added'),
    ('plan_assigned', 'Plan assigned'),
    ('complete', 'Onboarding complete'),
]

# Rank of each state, so "have we got at least this far?" is a comparison rather
# than a chain of ifs. Order here is the order of the flow.
STATE_ORDER = {name: i for i, (name, _label) in enumerate(ONBOARDING_STATES)}


class OnboardingProgress(models.Model):
    """Where one user is in onboarding, and how they got there.

    ``state`` is the furthest point reached — it only ever moves forward, so a
    user who navigates back to an earlier step to change an answer does not lose
    the steps they had already completed. ``history`` timestamps each state as it
    is first reached, which is what the admin shows.

    Users who predate this system are backfilled with ``state='complete'`` and
    ``is_legacy=True``: they were never asked for any of this and must not be
    locked out of the product to provide it retrospectively.
    """
    user = models.OneToOneField(django_settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='onboarding')
    state = models.CharField(max_length=30, choices=ONBOARDING_STATES, default='registered')
    history = models.JSONField(default=dict, blank=True,
                               help_text='{state: ISO timestamp} — when each state was first reached.')

    is_legacy = models.BooleanField(
        default=False,
        help_text='Account predates onboarding and was marked complete on migration.')
    payment_skipped = models.BooleanField(
        default=False,
        help_text='The payment step was skipped because no payment gateway was enabled.')

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name_plural = 'Onboarding progress'

    def __str__(self):
        return f'{self.user} — {self.get_state_display()}'

    # ── State helpers ────────────────────────────────────────────────────────
    @property
    def rank(self):
        return STATE_ORDER.get(self.state, 0)

    @property
    def is_complete(self):
        return self.state == 'complete'

    def has_reached(self, state):
        return self.rank >= STATE_ORDER.get(state, 0)

    def mark(self, state, save=True):
        """Record that `state` has been reached. Never moves backwards, and never
        rewrites the timestamp of a state already reached."""
        if state not in STATE_ORDER:
            raise ValueError(f'unknown onboarding state: {state!r}')
        history = dict(self.history or {})
        history.setdefault(state, timezone.now().isoformat())
        self.history = history
        if STATE_ORDER[state] > self.rank:
            self.state = state
        if state == 'complete' and self.completed_at is None:
            self.completed_at = timezone.now()
        if save:
            self.save(update_fields=['state', 'history', 'completed_at', 'updated_at'])
        return self

    def reached_at(self, state):
        return (self.history or {}).get(state)
