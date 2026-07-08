"""
Notification email sent to an organisation's admins when the org is disabled.

Kept separate from alert_email.py (the media digest) since this is an
account/administrative notice, not a coverage digest. Reused by the
organization_update view when an org transitions active -> inactive.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


def org_admin_recipients(org):
    """Email addresses to notify about org-level account changes: the org's
    org_admin users, plus the org's own contact email. De-duplicated, blanks
    dropped, original order preserved (admins first)."""
    emails = [
        (u.email or '').strip()
        for u in org.members.filter(role='org_admin')
    ]
    if org.email:
        emails.append(org.email.strip())

    seen, out = set(), []
    for e in emails:
        key = e.lower()
        if e and key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _send_org_status_email(org, recipients, subject, template, text_body):
    """Render `template` and send it to `recipients`. Returns a result dict;
    raises only if the mail backend itself fails."""
    if not recipients:
        return {'sent': False, 'reason': 'no recipients'}

    base = getattr(settings, 'SITE_URL', 'https://sociallight.africa').rstrip('/')
    login_url = base + (getattr(settings, 'LOGIN_URL', '/login/') or '/login/')
    logo_url = (base + org.logo.url) if org.logo else ''

    context = {
        'org': org,
        'subject': subject,
        'login_url': login_url,
        'logo_url': logo_url,
    }
    html_body = render_to_string(template, context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send()

    return {'sent': True, 'recipients': recipients}


def send_org_disabled_email(org, recipients=None):
    """Notify org admins that their organisation has been disabled."""
    recipients = recipients if recipients is not None else org_admin_recipients(org)
    return _send_org_status_email(
        org,
        recipients,
        subject=f"Social Light: {org.name} monitoring has been paused",
        template='monitor/email/org_disabled.html',
        text_body=(
            f"Hello,\n\n"
            f"Media monitoring for {org.name} has been paused. While paused, no new "
            f"mentions are collected and no alert emails are sent.\n\n"
            f"If you believe this is a mistake, please contact your Social Light "
            f"administrator.\n"
        ),
    )


def send_org_enabled_email(org, recipients=None):
    """Notify org admins that their organisation has been reactivated."""
    recipients = recipients if recipients is not None else org_admin_recipients(org)
    return _send_org_status_email(
        org,
        recipients,
        subject=f"Social Light: {org.name} monitoring has resumed",
        template='monitor/email/org_enabled.html',
        text_body=(
            f"Hello,\n\n"
            f"Media monitoring for {org.name} has resumed. New mentions are being "
            f"collected again and alert emails will be sent as scheduled.\n\n"
            f"Log in to view your latest coverage.\n"
        ),
    )
