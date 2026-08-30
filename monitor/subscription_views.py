"""Public trial signup, the price list, and the paywall an organisation lands on
when its free trial ends.

Flow:
    landing page  →  signup (creates org + org admin, starts a 14-day trial)
                  →  full access, with a countdown banner in the app
    trial ends    →  OrganizationAccessMiddleware redirects every /app/ and
                     /api/ request to `billing`
    billing       →  the user picks a package, which raises a SubscriptionRequest
                     and emails sales; a platform admin activates the package
                     from the Django admin, which restores access.
"""
import re

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import transaction
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import onboarding
from .models import Organization, Package, SubscriptionRequest, User, trial_period_days
from .onboarding import COUNTRY_CHOICES, DEFAULT_COUNTRY
from .verification import send_verification_email


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
