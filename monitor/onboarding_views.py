"""The onboarding wizard.

One view per step, all thin: each validates its own form, records what it learned,
calls ``onboarding.advance()`` with the state it completes, and hands control back
to the flow. None of them decides what comes next — ``onboarding.next_url()`` does
that from the state, which is what makes the wizard resumable and what stops the
"next step" logic drifting apart across nine views.

Every step is guarded by ``onboarding.can_access``. Going back to review or change
an earlier answer is allowed; jumping forward past an incomplete step is not, so
the plan step cannot be reached without the terms having been accepted.
"""
import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import legal, onboarding
from .onboarding import COUNTRY_CHOICES, DEFAULT_COUNTRY
from .onboarding_models import AgencyDeclaration
from .payments import PaymentError, PaymentsDisabled, get_provider, payments_enabled
from .verification import consume_token, send_verification_email



# ── Shared plumbing ──────────────────────────────────────────────────────────

def _guard(request, step_key):
    """Redirect away from a step the user may not be on yet. Returns a response
    to return, or None to carry on."""
    if onboarding.get_progress(request.user) is None:
        # Account predates onboarding — nothing to do here.
        return redirect('monitor:organizations')
    if not onboarding.can_access(request.user, step_key):
        return redirect(onboarding.next_url(request.user))
    return None


def _render(request, step_key, template, extra=None):
    ctx = onboarding.context_for(request.user, step_key)
    ctx.update(extra or {})
    return render(request, template, ctx)


def _advance(request, state):
    onboarding.advance(request.user, state)
    return redirect(onboarding.next_url(request.user))


# ── Step 2 — Verify ──────────────────────────────────────────────────────────

@login_required
def verify(request):
    """"Check your inbox", with a resend. The one page an unverified account can
    reach, so it must not itself require verification."""
    guard = _guard(request, 'verify')
    if guard:
        return guard

    if request.user.email_verified:
        return _advance(request, 'email_verified')

    sent = False
    # A failure recorded during signup, shown once and then cleared.
    mail_error = request.session.pop('verification_email_error', None)

    if request.method == 'POST':
        _token, mail_error = send_verification_email(request, request.user)
        sent = mail_error is None

    return _render(request, 'verify', 'monitor/onboarding/verify.html', {
        'email': request.user.email,
        'resent': sent,
        'mail_error': mail_error,
        # Passed explicitly - this project does not enable the `debug` context
        # processor, so the template cannot read it on its own.
        'debug': settings.DEBUG,
        'verification_url': (
            # In DEBUG, show the link on the page. Without it a developer whose
            # mail provider is not configured cannot get past this step at all.
            _debug_verification_url(request) if mail_error and settings.DEBUG else None
        ),
    })


def _debug_verification_url(request):
    from .onboarding_models import EmailVerificationToken
    from .verification import verification_url

    token = (EmailVerificationToken.objects
             .filter(user=request.user, used_at__isnull=True)
             .order_by('-created_at').first())
    return verification_url(request, token) if token else None


def verify_confirm(request, token):
    """The target of the emailed link.

    Deliberately not behind ``@login_required``. People open confirmation emails
    on a phone, in a browser with no session, and the token itself is the proof of
    control — requiring a sign-in first would send them to a login page instead of
    confirming, which is how verification emails end up abandoned. A signed-out
    visitor is verified and then invited to sign in.
    """
    user, error = consume_token(token)

    if not request.user.is_authenticated:
        if error is None and user is not None:
            return redirect(f"{reverse('monitor:login')}?verified=1")
        return redirect(f"{reverse('monitor:login')}?next={request.path}")

    if error is None and user is not None and user.pk == request.user.pk:
        return redirect(onboarding.next_url(request.user))

    if error is None and user is not None:
        # Verified, but the link was opened while signed in as somebody else.
        error = 'wrong_account'

    if onboarding.get_progress(request.user) is None:
        return redirect('monitor:organizations')

    return _render(request, 'verify', 'monitor/onboarding/verify.html', {
        'email': request.user.email,
        'error': error,
    })


# ── Step 3 — Profile ─────────────────────────────────────────────────────────

@login_required
def profile(request):
    guard = _guard(request, 'profile')
    if guard:
        return guard

    user = request.user
    values = {
        'first_name': user.first_name,
        'last_name': user.last_name,
        'phone': user.phone,
        'job_title': user.job_title,
        'country': user.country or DEFAULT_COUNTRY,
    }
    errors = {}

    if request.method == 'POST':
        values = {k: request.POST.get(k, '').strip() for k in values}
        if not values['first_name']:
            errors['first_name'] = 'Enter your first name.'
        if not values['last_name']:
            errors['last_name'] = 'Enter your last name.'
        if not values['job_title']:
            errors['job_title'] = 'Enter your job title or role.'
        if not values['country']:
            errors['country'] = 'Select your country.'

        if not errors:
            for field, value in values.items():
                setattr(user, field, value)
            user.save(update_fields=list(values))
            return _advance(request, 'profile_completed')

    return _render(request, 'profile', 'monitor/onboarding/profile.html', {
        'values': values,
        'errors': errors,
        'countries': COUNTRY_CHOICES,
    })


# ── Step 3 — Agency declaration ──────────────────────────────────────────────

@login_required
def agency(request):
    guard = _guard(request, 'agency')
    if guard:
        return guard

    user = request.user
    org = user.organization
    declaration = getattr(user, 'agency_declaration', None)

    values = {
        'account_type': declaration.account_type if declaration else 'organisation',
        'organisation_name': (declaration.organisation_name if declaration else '') or (org.name if org else ''),
        'position': (declaration.position if declaration else '') or user.job_title,
        'organisation_email': (declaration.organisation_email if declaration else '') or (org.email if org else ''),
        'organisation_phone': declaration.organisation_phone if declaration else '',
        'registration_number': declaration.registration_number if declaration else '',
        'is_authorised': declaration.is_authorised_representative if declaration else False,
    }
    errors = {}

    if request.method == 'POST':
        values = {
            'account_type': request.POST.get('account_type', 'individual').strip(),
            'organisation_name': request.POST.get('organisation_name', '').strip(),
            'position': request.POST.get('position', '').strip(),
            'organisation_email': request.POST.get('organisation_email', '').strip(),
            'organisation_phone': request.POST.get('organisation_phone', '').strip(),
            'registration_number': request.POST.get('registration_number', '').strip(),
            'is_authorised': request.POST.get('is_authorised') == 'on',
        }
        if values['account_type'] not in ('individual', 'organisation'):
            errors['account_type'] = 'Choose one of the two options.'

        if values['account_type'] == 'organisation':
            # Everything below is required only for an organisation declaration —
            # an individual is not asked to invent a company.
            if not values['organisation_name']:
                errors['organisation_name'] = 'Enter the name of the organisation you represent.'
            if not values['position']:
                errors['position'] = 'Enter your position within the organisation.'
            if not values['is_authorised']:
                errors['is_authorised'] = ('You must confirm that you are authorised to represent '
                                           'this organisation before you can continue.')

        if not errors:
            with transaction.atomic():
                declaration = _save_declaration(request, user, org, values)
                _sync_organization(org, values, user)
            return _advance(request, 'agency_declared')

    return _render(request, 'agency', 'monitor/onboarding/agency.html', {
        'values': values,
        'errors': errors,
        'declaration': declaration,
    })


def _save_declaration(request, user, org, values):
    from django.utils import timezone

    declaration, _ = AgencyDeclaration.objects.get_or_create(user=user)
    declaration.organization = org
    declaration.account_type = values['account_type']
    declaration.organisation_name = values['organisation_name']
    declaration.position = values['position']
    declaration.organisation_email = values['organisation_email']
    declaration.organisation_phone = values['organisation_phone']
    declaration.registration_number = values['registration_number']
    declaration.is_authorised_representative = bool(values['is_authorised'])
    if declaration.is_authorised_representative and declaration.authorised_at is None:
        declaration.authorised_at = timezone.now()
        declaration.authorisation_ip = legal.client_ip(request)
    declaration.save()
    return declaration


def _sync_organization(org, values, user):
    """Carry the declaration onto the tenant organisation record, so the rest of
    the product shows the name the user actually gave. Only fills what the
    declaration covers — nothing else about the organisation is touched."""
    if org is None:
        return
    fields = []
    if values['account_type'] == 'organisation' and values['organisation_name'] and \
            org.name != values['organisation_name']:
        org.name = values['organisation_name']
        fields.append('name')
    if values['organisation_email'] and not org.email:
        org.email = values['organisation_email']
        fields.append('email')
    if values['organisation_phone'] and not org.phone:
        org.phone = values['organisation_phone']
        fields.append('phone')
    if user.country and not org.country:
        org.country = user.country
        fields.append('country')
    if fields:
        org.save(update_fields=fields)


# ── Step 4 — Legal & consent ─────────────────────────────────────────────────

def _consent_step(request, step_key, doc_type, completes):
    """One consent page. The three documents are separate steps with separate
    records — never one combined "I agree to everything" box."""
    guard = _guard(request, step_key)
    if guard:
        return guard

    document = legal.current_document(doc_type)
    if document is None or not document.requires_acceptance:
        # Nothing published to accept, or published for reference only. Not a
        # reason to trap the user — a deployment that has not published a
        # disclaimer yet is still a deployment people can finish signing up on.
        return _advance(request, completes)

    error = None
    if request.method == 'POST':
        if request.POST.get('accept') == 'on':
            legal.record_consent(request.user, document, request=request,
                                 decision='accepted', source='onboarding')
            return _advance(request, completes)
        error = 'You need to accept this to continue.'

    return _render(request, step_key, 'monitor/onboarding/consent.html', {
        'document': document,
        'error': error,
        'already_accepted': legal.has_accepted(request.user, doc_type, document),
    })


@login_required
def terms(request):
    return _consent_step(request, 'terms', 'terms', 'terms_accepted')


@login_required
def privacy(request):
    return _consent_step(request, 'privacy', 'privacy', 'privacy_accepted')


@login_required
def disclaimer(request):
    return _consent_step(request, 'disclaimer', 'disclaimer', 'disclaimer_accepted')


# ── Step 5 — Payment method ──────────────────────────────────────────────────

@login_required
def payment(request):
    """Add a card, or continue without one.

    The page renders in both modes. With a gateway configured it asks the
    provider for the browser configuration it needs and accepts the *token* the
    provider's widget produces — never card details, which never reach this
    server. With no gateway it explains that billing is arranged off-platform and
    offers a continue button, because onboarding completing must not depend on a
    payment integration existing.
    """
    guard = _guard(request, 'payment')
    if guard:
        return guard

    org = request.user.organization
    provider = get_provider()
    enabled = payments_enabled() and provider.is_enabled
    error = None
    client_config = {}

    if request.method == 'POST':
        if request.POST.get('action') == 'skip' or not enabled:
            onboarding.skip_payment(request.user)
            return redirect(onboarding.next_url(request.user))

        token = request.POST.get('payment_token', '').strip()
        if not token:
            error = 'We did not receive a card token from the payment provider. Please try again.'
        elif org is None:
            error = 'Your account is not attached to an organisation yet.'
        else:
            try:
                provider.attach_payment_method(
                    org, request.user, token,
                    customer_id=request.POST.get('customer_id', '').strip() or None,
                )
            except PaymentsDisabled:
                onboarding.skip_payment(request.user)
                return redirect(onboarding.next_url(request.user))
            except PaymentError as exc:
                error = str(exc)
            else:
                return _advance(request, 'payment_method_added')

    if enabled and org is not None and request.method == 'GET':
        try:
            client_config = provider.client_config(request, org)
        except PaymentError as exc:
            # A gateway that is switched on but broken must not block onboarding.
            error = str(exc)
            enabled = False

    return _render(request, 'payment', 'monitor/onboarding/payment.html', {
        'payments_enabled': enabled,
        'provider': provider,
        'client_config': client_config,
        'client_config_json': json.dumps(client_config),
        'existing_method': org.payment_methods.filter(is_active=True).first() if org else None,
        'error': error,
    })


# ── Step 6 — Plan ────────────────────────────────────────────────────────────

@login_required
def plan(request):
    """Pick a plan.

    Self-service assignment is limited to tiers that are actually self-service:
    a ``contact_only`` tier records a subscription request for sales instead, and
    a paid tier with no gateway configured does the same, because nothing here
    can take the money. The free tier and the running trial are assigned
    immediately.
    """
    guard = _guard(request, 'plan')
    if guard:
        return guard

    from .subscription_views import selectable_packages

    org = request.user.organization
    packages = list(selectable_packages())
    error = None

    if request.method == 'POST':
        slug = request.POST.get('package', '').strip()
        package = next((p for p in packages if p.slug == slug), None)
        if package is None:
            error = 'Choose one of the plans below.'
        elif org is None:
            error = 'Your account is not attached to an organisation yet.'
        else:
            _assign_plan(request, org, package)
            return _advance(request, 'plan_assigned')

    return _render(request, 'plan', 'monitor/onboarding/plan.html', {
        'packages': packages,
        'org': org,
        'error': error,
        'payments_enabled': payments_enabled() and get_provider().is_enabled,
    })


def _assign_plan(request, org, package):
    """Put the organisation on `package`, or raise a request for it.

    A free tier is activated outright. Anything paid needs money to change hands
    first: with a gateway that is a charge, without one it is an invoice, and
    either way access continues on the existing trial until it is settled — which
    is exactly how the platform behaved before onboarding existed.
    """
    from .models import SubscriptionRequest

    is_free = not package.price and not package.contact_only

    if is_free:
        org.activate_package(package)
        return

    org.package = package
    org.save(update_fields=['package'])
    SubscriptionRequest.objects.create(
        organization=org,
        package=package,
        requested_by=request.user,
        contact_name=request.user.get_full_name() or request.user.username,
        contact_email=request.user.email,
        contact_phone=request.user.phone,
        note='Selected during onboarding.',
    )


# ── Step 7 — Done ────────────────────────────────────────────────────────────

@login_required
def done(request):
    progress = onboarding.get_progress(request.user)
    if progress is None:
        return redirect('monitor:organizations')

    # Reaching this page with everything else finished is what completes
    # onboarding — there is no separate "finish" button to forget to press.
    if onboarding.next_step(request.user) is None:
        onboarding.advance(request.user, 'complete')
    elif not progress.is_complete:
        return redirect(onboarding.next_url(request.user))

    org = request.user.organization
    return _render(request, 'done', 'monitor/onboarding/done.html', {
        'org': org,
        'consents': legal.consent_summary(request.user),
        'app_url': (org and org.id) and f'/app/dashboard/{org.id}/' or '/app/organizations/',
    })


# ── Re-consent to a new version ──────────────────────────────────────────────

@login_required
def reconsent(request):
    """Accept a document version published after the user finished onboarding.

    Deliberately *not* a lockout. Publishing v1.1 of the Terms shows a banner and
    brings the user here; it does not put an existing client back through the
    wizard or cut off access to work they have already paid for. Whether a
    particular change is significant enough to require blocking re-acceptance is
    a legal and business decision, and this is where that would be added.

    Acceptance writes a new ConsentRecord with ``source='reconsent'``. The earlier
    acceptance of v1.0 is untouched and stays on record.
    """
    outstanding = legal.outstanding_documents(request.user)
    if not outstanding:
        return render(request, 'monitor/onboarding/reconsent.html', {
            'documents': [],
            'consents': legal.consent_summary(request.user),
            'org': request.user.organization,
            'page': 'legal',
        })

    document = outstanding[0]
    error = None
    if request.method == 'POST':
        if request.POST.get('accept') == 'on':
            legal.record_consent(request.user, document, request=request,
                                 decision='accepted', source='reconsent')
            return redirect('monitor:reconsent')
        error = 'You need to accept this to continue.'

    return render(request, 'monitor/onboarding/reconsent.html', {
        'documents': outstanding,
        'document': document,
        'remaining': len(outstanding),
        'error': error,
        'consents': legal.consent_summary(request.user),
        'org': request.user.organization,
        'page': 'legal',
    })


# ── Status endpoint ──────────────────────────────────────────────────────────

@login_required
@require_http_methods(['GET'])
def status(request):
    """Machine-readable onboarding state, for the front end and for support."""
    progress = onboarding.get_progress(request.user)
    step = onboarding.next_step(request.user)
    return JsonResponse({
        'state': progress.state if progress else 'complete',
        'is_complete': onboarding.is_complete(request.user),
        'is_legacy': bool(progress and progress.is_legacy),
        'email_verified': request.user.email_verified,
        'next_step': step.key if step else None,
        'next_url': onboarding.next_url(request.user),
        'outstanding_documents': [
            {'doc_type': d.doc_type, 'version': d.version, 'title': d.title}
            for d in legal.outstanding_documents(request.user)
        ],
    })
