"""Public trial signup, the price list, and the paywall an organisation lands on
when its free trial ends.

Flow:
    landing page  →  signup (creates org + org admin, starts a 14-day trial)
                  →  full access, with a countdown banner in the app
    trial ends    →  OrganizationAccessMiddleware redirects every /app/ and
                     /api/ request to `billing`
    billing       →  the user picks a package.

What happens at that last step depends on whether a gateway is switched on.

With no gateway — the platform's long-standing behaviour — picking a package
raises a SubscriptionRequest and emails sales; a platform admin activates the
package from the Django admin, which restores access.

With a hosted-redirect gateway (DPO Pay), picking a package instead opens a
checkout at the gateway and sends the customer there to pay. They come back to
`checkout_return`, which asks the gateway server-side what happened and, if the
money arrived, activates the package immediately. `checkout_callback` is the same
verification reached from the gateway's own webhook, so a customer who closes the
tab on the payment page still gets activated.

Both paths end at the same place — an active package — and the manual path
remains available on every deployment for purchase orders and EFT.
"""
import logging
import re

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import onboarding
from .models import Organization, Package, SubscriptionRequest, User, trial_period_days
from .onboarding import COUNTRY_CHOICES, DEFAULT_COUNTRY
from .payment_models import Payment
from .payments import PaymentError, PaymentRejected, get_provider, payments_enabled
from .verification import send_verification_email

logger = logging.getLogger(__name__)


MIN_PASSWORD_LENGTH = 10


def active_packages():
    """The published price list. `is_public` keeps assignable-but-unadvertised
    tiers (Free) off the marketing cards without making them unassignable."""
    return Package.objects.filter(is_active=True, is_public=True)


def selectable_packages():
    """Every tier a user or admin may actually be put on — the price list plus
    the unadvertised ones. Used by the onboarding plan step and admin assignment."""
    return Package.objects.filter(is_active=True)


def _split_name(full_name):
    parts = full_name.split()
    if not parts:
        return '', ''
    return parts[0], ' '.join(parts[1:])


def _validate_signup(data):
    """Returns (cleaned, errors). Kept separate from the view so the rules are
    readable in one place and testable without a request.

    Registration deliberately enforces only what is needed to create the account:
    a name, a working email, an organisation and a password. Phone, job title and
    country are accepted here but not required, because the onboarding profile
    step asks for them and *does* require them — asking twice and blocking twice
    would make signup heavier for no gain.

    ``contact_name`` is still accepted as an alternative to first/last name so
    that anything still posting the older form keeps working.
    """
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    contact_name = data.get('contact_name', '').strip()
    if not (first_name or last_name) and contact_name:
        first_name, last_name = _split_name(contact_name)

    cleaned = {
        'first_name': first_name,
        'last_name': last_name,
        'contact_name': contact_name or f'{first_name} {last_name}'.strip(),
        'email': data.get('email', '').strip(),
        'phone': data.get('phone', '').strip(),
        'job_title': data.get('job_title', '').strip(),
        'country': data.get('country', '').strip(),
        'org_name': data.get('org_name', '').strip(),
        'password': data.get('password', ''),
        'confirm_password': data.get('confirm_password', ''),
    }
    errors = {}

    if not cleaned['first_name']:
        errors['first_name'] = 'Enter your first name.'
    if not cleaned['last_name']:
        errors['last_name'] = 'Enter your last name.'
    if not cleaned['contact_name']:
        errors['contact_name'] = 'Enter your name.'

    if not cleaned['email']:
        errors['email'] = 'Enter your work email address.'
    else:
        try:
            validate_email(cleaned['email'])
        except ValidationError:
            errors['email'] = 'Enter a valid email address.'
        else:
            if User.objects.filter(email__iexact=cleaned['email']).exists() or \
                    User.objects.filter(username__iexact=cleaned['email'].lower()).exists():
                errors['email'] = 'An account already exists for this email. Sign in instead.'

    if not cleaned['org_name']:
        errors['org_name'] = 'Enter your organisation name.'
    elif Organization.objects.filter(name__iexact=cleaned['org_name']).exists():
        errors['org_name'] = ('An organisation with this name is already on Social Light. '
                              'Ask a colleague to invite you, or use a different name.')

    password = cleaned['password']
    if len(password) < MIN_PASSWORD_LENGTH:
        errors['password'] = f'Use at least {MIN_PASSWORD_LENGTH} characters.'
    elif not re.search(r'\d', password):
        errors['password'] = 'Include at least one number.'
    else:
        try:
            validate_password(password)
        except ValidationError as exc:
            errors['password'] = ' '.join(exc.messages)

    # Confirmation is checked separately from the password rules, and only once
    # the password itself is valid — telling someone their passwords do not match
    # *and* that the password is too short at the same time is noise. A typo here
    # is expensive: the account is created with a password its owner does not
    # know, and the only way out is a reset.
    if not errors.get('password'):
        if not cleaned['confirm_password']:
            errors['confirm_password'] = 'Type your password again to confirm it.'
        elif cleaned['confirm_password'] != password:
            errors['confirm_password'] = 'These passwords do not match.'

    return cleaned, errors


def _send_trial_welcome(request, user, org):
    ctx = {
        'full_name': user.get_full_name() or user.username,
        'org_name': org.name,
        'trial_days': trial_period_days(),
        'trial_ends_at': org.trial_ends_at,
        'protocol': 'https' if request.is_secure() else 'http',
        'domain': request.get_host(),
    }
    send_mail(
        subject=f'Your {trial_period_days()}-day Social Light trial has started',
        message=render_to_string('monitor/email/trial_started.txt', ctx),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=render_to_string('monitor/email/trial_started.html', ctx),
        fail_silently=True,
    )


def _notify_sales(request, sub_request):
    recipients = getattr(settings, 'SALES_NOTIFICATION_EMAILS', [])
    if not recipients:
        return
    org = sub_request.organization
    package = sub_request.package
    lines = [
        f'Organisation: {org.name}',
        f'Package requested: {package.name if package else "not specified"}'
        + (f' ({package.price_display} {package.period_display})' if package and package.price else ''),
        f'Contact: {sub_request.contact_name} <{sub_request.contact_email}>',
        f'Phone: {sub_request.contact_phone or "—"}',
        f'Trial ended: {org.trial_ends_at:%d %b %Y}' if org.trial_ends_at else 'Trial: n/a',
        '',
        sub_request.note or '(no note)',
        '',
        'Activate the package from the admin once payment is settled:',
        f'{"https" if request.is_secure() else "http"}://{request.get_host()}/admin/monitor/organization/{org.id}/change/',
    ]
    send_mail(
        subject=f'Package request — {org.name}',
        message='\n'.join(lines),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
        fail_silently=True,
    )


# ── Signup ───────────────────────────────────────────────────────────────────

def signup(request):
    """Public self-service signup — step 1 of onboarding.

    Creates the organisation and its first user (an org admin), starts the free
    trial, and hands the user straight to step 2. The account exists at this
    point but is not yet verified, so the middleware will keep it out of the
    application until the emailed link is followed.
    """
    if request.user.is_authenticated:
        return redirect('monitor:organizations')

    # Pre-select the home market so a blank form does not silently default to
    # whichever country sorts first.
    values, errors = {'country': DEFAULT_COUNTRY}, {}
    if request.method == 'POST':
        values, errors = _validate_signup(request.POST)
        if not errors:
            with transaction.atomic():
                org = Organization(
                    name=values['org_name'],
                    email=values['email'],
                    status='active',
                )
                if values['country']:
                    org.country = values['country']
                org.start_trial()
                org.save()
                user = User.objects.create_user(
                    username=values['email'].lower(),
                    email=values['email'],
                    password=values['password'],
                    first_name=values['first_name'],
                    last_name=values['last_name'],
                    phone=values['phone'],
                    job_title=values['job_title'],
                    country=values['country'],
                    organization=org,
                    role='org_admin',
                )
                onboarding.start(user)
            login(request, user)
            _send_trial_welcome(request, user, org)
            _token, mail_error = send_verification_email(request, user)
            if mail_error:
                # The account is created and the user is signed in — a mail
                # failure must not undo that. Hand the reason to the verify step
                # so it can say what happened instead of claiming success.
                request.session['verification_email_error'] = mail_error
            return redirect(onboarding.next_url(user))

    # Re-render the form without the passwords in it. The template never echoes
    # them into a value attribute, but there is no reason for a plaintext
    # password to sit in a template context at all — one careless
    # `{{ values.password }}` later and it is on the page.
    values = {k: v for k, v in values.items() if k not in ('password', 'confirm_password')}

    return render(request, 'monitor/signup.html', {
        'values': values,
        'errors': errors,
        'trial_days': trial_period_days(),
        'min_password_length': MIN_PASSWORD_LENGTH,
        'packages': active_packages(),
        'countries': COUNTRY_CHOICES,
    })


# ── Price list & paywall ─────────────────────────────────────────────────────

def pricing(request):
    """The public price list — one list, the same for every client."""
    org = getattr(request.user, 'organization', None) if request.user.is_authenticated else None
    return render(request, 'monitor/billing.html', {
        'packages': active_packages(),
        'org': org,
        'public': True,
        'trial_days': trial_period_days(),
    })


@login_required
def billing(request):
    """The signed-in view of the price list. Doubles as the paywall an expired
    organisation is redirected to."""
    org = request.user.organization
    latest_request = None
    if org:
        latest_request = org.subscription_requests.filter(status='pending').first()
    return render(request, 'monitor/billing.html', {
        'packages': active_packages(),
        'org': org,
        'public': False,
        'trial_days': trial_period_days(),
        'pending_request': latest_request,
        'submitted': request.GET.get('requested') == '1',
        # When a gateway is on, the package buttons post straight to checkout
        # instead of opening the "request a call" modal.
        'gateway_checkout': redirect_checkout_provider() is not None,
        'checkout_status': request.GET.get('checkout', ''),
        'latest_payment': (org.payments.first() if org else None),
    })


@login_required
@require_http_methods(['POST'])
def package_request(request):
    """Record the organisation's choice of package and alert sales. Access is
    only restored once a platform admin activates the package, so this does not
    change plan_status to anything that grants access."""
    org = request.user.organization
    if org is None:
        return redirect('monitor:billing')

    package = Package.objects.filter(slug=request.POST.get('package'), is_active=True).first()
    sub_request = SubscriptionRequest.objects.create(
        organization=org,
        package=package,
        requested_by=request.user,
        contact_name=request.user.get_full_name() or request.user.username,
        contact_email=request.user.email,
        contact_phone=request.POST.get('phone', '').strip(),
        note=request.POST.get('note', '').strip(),
    )

    # Mark the organisation as awaiting activation so the paywall can say so.
    # 'pending' still withholds access — only activate_package() restores it.
    if org.trial_has_expired and org.plan_status != 'active':
        org.plan_status = 'pending'
        org.save(update_fields=['plan_status'])

    _notify_sales(request, sub_request)
    return redirect(f"{reverse('monitor:billing')}?requested=1")


# ── Gateway checkout ─────────────────────────────────────────────────────────
# Only reached when a hosted-redirect provider is configured. The manual path
# above is untouched and remains the fallback on every deployment.

def redirect_checkout_provider():
    """The configured provider, but only if it can actually take a payment by
    redirect. Returns None otherwise, which is what makes every view below fall
    back to the manual request flow instead of erroring."""
    if not payments_enabled():
        return None
    provider = get_provider()
    if not (provider.is_redirect and provider.is_enabled):
        return None
    return provider


def _absolute(request, url_name):
    """The public URL a payment gateway should send the customer back to.

    Normally derived from the request, which is right in production: the host the
    customer arrived on is the host they should return to.

    ``PAYMENT_RETURN_BASE_URL`` overrides that, and exists because the derived
    host is not always usable. Behind a tunnel or a proxy the request can carry a
    rewritten or loopback host, and DPO rejects a loopback return URL outright —
    the whole transaction, with 403, before its API ever sees the request. That
    failure is invisible from the code's point of view: everything is correct
    except the one value that came from the environment rather than from us.

    Setting it pins the URL regardless of how the request arrived, which is what
    makes a tunnelled development setup behave like production.
    """
    base = (getattr(settings, 'PAYMENT_RETURN_BASE_URL', '') or '').rstrip('/')
    if base:
        return f'{base}{reverse(url_name)}'
    return request.build_absolute_uri(reverse(url_name))


def _billing_url(**params):
    query = '&'.join(f'{k}={v}' for k, v in params.items() if v)
    return f"{reverse('monitor:billing')}?{query}" if query else reverse('monitor:billing')


@login_required
@require_http_methods(['POST'])
def checkout_start(request):
    """Open a payment at the gateway and send the customer to it.

    Falls back to the manual request flow whenever a gateway cannot be used —
    no provider, a contact-only tier, a package with no price. A customer must
    never reach a dead end here: the worst case is the flow they would have had
    before payments were switched on.
    """
    org = request.user.organization
    provider = redirect_checkout_provider()
    package = Package.objects.filter(slug=request.POST.get('package'), is_active=True).first()

    if org is None or provider is None or package is None or package.contact_only or not package.price:
        return package_request(request)

    return_url = _absolute(request, 'monitor:checkout_return')
    cancel_url = _absolute(request, 'monitor:checkout_cancelled')
    # Logged because these are built from the request's own host, so they are the
    # first thing to check when a gateway rejects a checkout: behind a proxy or a
    # tunnel the host can be rewritten to something the gateway will not accept —
    # DPO refuses loopback addresses outright.
    logger.info('Starting checkout for org %s: return=%s cancel=%s',
                org.id, return_url, cancel_url)

    try:
        session = provider.start_checkout(
            org, package.price, package.currency,
            package=package,
            user=request.user,
            description=f'{package.name} — {package.get_billing_period_display()}',
            return_url=return_url,
            # DPO's BackURL is where it sends a customer who clicks back from the
            # payment page without paying. It is NOT the completion webhook —
            # that is checkout_callback, which DPO calls server-to-server and
            # which is configured account-side rather than per transaction.
            callback_url=cancel_url,
            # Self-serve tiers renew monthly, so ask DPO to save the card on the
            # first payment. Without this there is no token to charge later and
            # every renewal would mean sending the customer back through checkout.
            allow_recurrent=not package.contact_only,
        )
    except PaymentRejected as exc:
        # Reached the gateway and been refused. Retrying will be refused the same
        # way, so the customer must not be told to try again in a moment. The
        # gateway's own wording is logged rather than shown: it often names the
        # gateway's support address, which is not who the customer should ask.
        logger.warning('Gateway refused checkout for org %s (%s): %s',
                       org.id, package.slug, exc.reason)
        return redirect(_billing_url(checkout='rejected'))
    except PaymentError as exc:
        # Could not reach the gateway at all. Worth trying again shortly.
        logger.warning('Checkout could not be started for org %s: %s', org.id, exc)
        return redirect(_billing_url(checkout='unavailable'))

    return redirect(session.redirect_url)


def _settle(payment):
    """Verify a payment with the gateway and activate the package if it is paid.

    The single place a subscription is turned on by a gateway payment, so that
    the browser return and the webhook cannot disagree about what happened. Safe
    to call repeatedly — ``verify_checkout`` is idempotent and
    ``activate_package`` is a straight assignment.
    """
    provider = redirect_checkout_provider()
    if provider is None:
        return None

    # Whether this call is the one that settles the payment, or a repeat of a
    # settlement that already happened. Both the browser return and the webhook
    # arrive for every payment, and a support agent re-checking arrives again.
    was_already_paid = payment.status == 'succeeded'

    result = provider.verify_checkout(payment)
    if result.succeeded and payment.package_id:
        # activate_package sets current_period_end from the package's billing
        # period, so the subscription actually lapses rather than running forever.
        payment.organization.activate_package(payment.package)
        if not was_already_paid:
            # Only on the transition to paid. Re-verifying a settled payment must
            # not generate fresh gateway traffic.
            _capture_saved_card(provider, payment)
    return result


def _capture_saved_card(provider, payment):
    """Store the saved-card token so this subscription can renew itself.

    Best effort by design. The customer has paid and their access is already
    restored; failing to retrieve a token only means the next renewal asks them
    to pay again, which is a worse experience but not a broken one. It must never
    turn a successful payment into an error.
    """
    capture = getattr(provider, 'capture_subscription_token', None)
    if capture is None:
        return

    # Already have one. Both the browser return and the webhook reach this for the
    # same payment, and a support agent re-checking reaches it again — fetching
    # the same token repeatedly would be pointless traffic against the gateway.
    if provider.recurring_method_for(payment.organization) is not None:
        return

    try:
        capture(payment.organization, _customer_email(payment), user=payment.created_by)
    except Exception as exc:                     # noqa: BLE001 - never break a paid checkout
        logger.warning('Could not store the saved card for org %s: %s',
                       payment.organization_id, exc)


def _customer_email(payment):
    """The address the customer was identified by at checkout, which is what the
    gateway's token lookup is keyed on."""
    if payment.created_by and payment.created_by.email:
        return payment.created_by.email
    return payment.organization.email if payment.organization else ''


def _payment_from_reference(reference):
    """Find the payment a gateway is talking about.

    ``CompanyRef`` is the Payment row's own UUID, so this is a primary-key
    lookup. It is also the only thing taken from the query string: the *outcome*
    is always re-fetched from the gateway, never read from what the browser
    carried back.
    """
    if not reference:
        return None
    try:
        return Payment.objects.select_related('organization', 'package').get(pk=reference)
    except (Payment.DoesNotExist, ValueError, ValidationError):
        return None


def checkout_return(request):
    """Where the gateway sends the customer's browser after payment.

    Deliberately not behind ``@login_required`` and deliberately outside
    ``/app/``. The customer arriving here is, by definition, one whose trial has
    expired, so the paywall would bounce them; and a session cookie that does not
    survive the round trip would send them to a login page instead of confirming
    the payment they just made. The reference is an unguessable UUID and the
    verdict comes from the gateway, so nothing here depends on the session.
    """
    payment = _payment_from_reference(
        request.GET.get('CompanyRef') or request.GET.get('company_ref'))
    if payment is None:
        return redirect(_billing_url(checkout='unknown'))

    try:
        result = _settle(payment)
    except PaymentError as exc:
        logger.warning('Could not verify payment %s on return: %s', payment.id, exc)
        return redirect(_billing_url(checkout='unverified'))

    if result is not None and result.succeeded:
        return redirect(_billing_url(checkout='paid'))
    if payment.status == 'pending':
        # Still in flight — the webhook will finish it. Saying "failed" here
        # would be wrong and would push the customer into paying twice.
        return redirect(_billing_url(checkout='pending'))
    return redirect(_billing_url(checkout='failed'))


def checkout_cancelled(request):
    """DPO's BackURL — the customer clicked back without paying.

    Deliberately verifies nothing and changes nothing. There is no payment to
    confirm: they chose not to make one, and calling verifyToken here would only
    ask DPO about a transaction we already know was abandoned. The pending
    Payment row is left exactly as it is, which is what an abandoned attempt
    should look like in the admin.

    Separate from checkout_callback, which is the server-to-server webhook DPO
    calls when a payment completes. Conflating the two would mean a customer
    backing out hit the same code path as a completed payment.
    """
    payment = _payment_from_reference(
        request.GET.get('CompanyRef') or request.GET.get('company_ref'))
    if payment is not None:
        logger.info('Checkout abandoned at the gateway for payment %s', payment.id)
    return redirect(_billing_url(checkout='cancelled'))


@csrf_exempt
@require_http_methods(['POST'])
def checkout_callback(request):
    """The gateway's server-to-server notification.

    CSRF-exempt because the caller is DPO, not a browser with a session — and
    safe to be, because the request grants nothing on its own: it names a payment
    and this view then asks the gateway directly what that payment's status is. A
    forged call can at most trigger a verification that returns the truth.

    DPO expects an ``<API3G><Response>OK</Response></API3G>`` body, and treats
    anything else as a delivery failure worth retrying — which is fine, because
    the handler is idempotent.
    """
    reference = request.POST.get('CompanyRef') or request.POST.get('TransactionToken')
    payment = _payment_from_reference(request.POST.get('CompanyRef'))
    if payment is None and reference:
        payment = Payment.objects.filter(provider_reference=reference).first()

    if payment is not None:
        try:
            _settle(payment)
        except PaymentError as exc:
            # Acknowledge anyway: a retry storm from the gateway will not fix a
            # gateway we cannot reach, and the return leg verifies as well.
            logger.warning('Callback verification failed for %s: %s', payment.id, exc)
    else:
        logger.warning('Payment callback for an unknown reference: %r', reference)

    return HttpResponse(
        '<?xml version="1.0" encoding="utf-8"?><API3G><Response>OK</Response></API3G>',
        content_type='application/xml')
