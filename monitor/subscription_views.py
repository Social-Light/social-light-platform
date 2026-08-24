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

from .models import Organization, Package, SubscriptionRequest, User, trial_period_days


MIN_PASSWORD_LENGTH = 10


def active_packages():
    return Package.objects.filter(is_active=True)


def _split_name(full_name):
    parts = full_name.split()
    if not parts:
        return '', ''
    return parts[0], ' '.join(parts[1:])


def _validate_signup(data):
    """Returns (cleaned, errors). Kept separate from the view so the rules are
    readable in one place and testable without a request."""
    cleaned = {
        'contact_name': data.get('contact_name', '').strip(),
        'email': data.get('email', '').strip(),
        'org_name': data.get('org_name', '').strip(),
        'password': data.get('password', ''),
    }
    errors = {}

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
    """Public self-service signup. Creates the organisation and its first user
    (an org admin) and starts the free trial."""
    if request.user.is_authenticated:
        return redirect('monitor:organizations')

    values, errors = {}, {}
    if request.method == 'POST':
        values, errors = _validate_signup(request.POST)
        if not errors:
            first_name, last_name = _split_name(values['contact_name'])
            with transaction.atomic():
                org = Organization(
                    name=values['org_name'],
                    email=values['email'],
                    status='active',
                )
                org.start_trial()
                org.save()
                user = User.objects.create_user(
                    username=values['email'].lower(),
                    email=values['email'],
                    password=values['password'],
                    first_name=first_name,
                    last_name=last_name,
                    organization=org,
                    role='org_admin',
                )
            login(request, user)
            _send_trial_welcome(request, user, org)
            return redirect('monitor:dashboard', org_id=org.id)

    return render(request, 'monitor/signup.html', {
        'values': values,
        'errors': errors,
        'trial_days': trial_period_days(),
        'min_password_length': MIN_PASSWORD_LENGTH,
        'packages': active_packages(),
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
