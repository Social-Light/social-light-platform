"""Email address verification.

A new account is not trusted to control the address it signed up with until it
follows a link we send there. Until it does, ``User.email_verified`` is False and
the middleware keeps it out of the application — the account exists, can sign in,
and can do exactly one thing: finish verifying.

Accounts created before this existed are backfilled to verified by migration
0018; they were confirmed by other means and are not asked to do it again.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .onboarding_models import EmailVerificationToken

logger = logging.getLogger(__name__)


def verification_url(request, token):
    path = reverse('monitor:onboarding_verify_confirm', args=[token.token])
    if request is not None:
        return request.build_absolute_uri(path)
    base = getattr(settings, 'SITE_URL', '').rstrip('/')
    return f'{base}{path}'


# The values shipped in .env.example. A deployment still carrying one of them has
# never configured email at all, which is worth saying plainly rather than
# reporting as a generic transport failure — it is the single most likely reason a
# verification link does not arrive on a fresh install. Matching is on the host
# and username only; the password is never compared, logged or displayed.
PLACEHOLDER_SMTP_VALUES = frozenset({
    'smtp.your-provider.example',
    'your-smtp-username',
})


def email_configuration_problem():
    """A human-readable reason email cannot possibly send, or None.

    Checked before attempting a send so that an unconfigured deployment gets a
    useful message instead of a socket error from deep inside the mail backend.
    Every message names environment variables only — never their values — so
    nothing here can leak an SMTP password into a page, a log line or an admin
    message.
    """
    backend = getattr(settings, 'EMAIL_BACKEND', '')
    if 'smtp' not in backend:
        return None                                  # console/locmem/file — nothing to check

    host = (getattr(settings, 'EMAIL_HOST', '') or '').strip()
    if not host:
        return 'EMAIL_HOST is not set, so no email can be sent.'
    if host in PLACEHOLDER_SMTP_VALUES:
        return ('EMAIL_HOST is still the placeholder from .env.example, so there is no '
                'mail server to send through.')

    user = (getattr(settings, 'EMAIL_HOST_USER', '') or '').strip()
    if user in PLACEHOLDER_SMTP_VALUES:
        return ('EMAIL_HOST_USER is still the placeholder from .env.example, so the mail '
                'server rejects every send.')
    if user and not (getattr(settings, 'EMAIL_HOST_PASSWORD', '') or ''):
        return ('EMAIL_HOST_USER is set but EMAIL_HOST_PASSWORD is empty, so the mail '
                'server cannot authenticate this sender.')

    if getattr(settings, 'EMAIL_USE_TLS', False) and getattr(settings, 'EMAIL_USE_SSL', False):
        return ('EMAIL_USE_TLS and EMAIL_USE_SSL are both on. Set exactly one — TLS for '
                'port 587, SSL for port 465.')
    return None


def send_verification_email(request, user):
    """Issue a fresh token and email the link.

    Returns ``(token, error)``. ``error`` is None on success, otherwise a short
    message for whoever is looking at the screen.

    The send is **not** silent. It used to be, on the reasoning that a mail
    outage should not roll back a completed registration — which is true, and is
    why the exception is caught here rather than allowed to propagate. But
    swallowing it entirely made the page say "Sent" when nothing had been sent,
    which strands the user at a step they cannot pass and gives whoever is
    debugging nothing to go on. The registration still stands; the difference is
    that the failure is now visible and logged.
    """
    token = EmailVerificationToken.issue(user)
    ctx = {
        'full_name': user.get_full_name() or user.username,
        'verification_url': verification_url(request, token),
        'expires_at': token.expires_at,
        'ttl_hours': int(getattr(settings, 'EMAIL_VERIFICATION_TTL_HOURS', 48)),
    }

    misconfigured = email_configuration_problem()
    if misconfigured:
        logger.error('Verification email for user %s not sent: %s', user.pk, misconfigured)
        return token, _failure(misconfigured)

    try:
        send_mail(
            subject='Confirm your email address — Social Light',
            message=render_to_string('monitor/email/verify_email.txt', ctx),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=render_to_string('monitor/email/verify_email.html', ctx),
            fail_silently=False,
        )
    except Exception as exc:
        # Deliberately broad: every mail backend raises its own exception types,
        # and none of them may take down a registration that has already been
        # committed.
        logger.exception('Verification email to %s failed', user.email)
        return token, _failure(f'{exc.__class__.__name__}: {exc}')

    logger.info('Verification email sent to user %s', user.pk)
    return token, None


# A user-facing sentence and the technical detail, kept apart on purpose. The
# person signing up gets the first — an exception class name or a provider's
# validation error tells them nothing they can act on, and reads like the site is
# broken. The second is for the log, the admin and DEBUG.
USER_FACING_MAIL_FAILURE = (
    "We couldn't send your confirmation email just now. Your account is saved — "
    'nothing was lost.'
)


def redact_smtp_credentials(text):
    """Blank out the SMTP password anywhere it appears in a message.

    Mail servers do not normally echo a password back, but a misconfigured relay
    or a chatty library can, and the detail string travels to the log, the admin
    and the DEBUG panel. Cheap insurance against printing the one value in the
    configuration that must never be printed.
    """
    text = str(text)
    secret = getattr(settings, 'EMAIL_HOST_PASSWORD', '') or ''
    if secret:
        text = text.replace(secret, '[redacted]')
    return text


def _failure(detail):
    """A mail failure as a plain dict, so it survives being put in the session
    (which must be JSON-serialisable) on the way to the verify page."""
    return {'message': USER_FACING_MAIL_FAILURE, 'detail': redact_smtp_credentials(detail)}


def mark_verified(user):
    """Flip the account to verified and move onboarding past the verify step."""
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=['email_verified', 'email_verified_at'])

    progress = user.onboarding_progress
    if progress is not None:
        progress.mark('email_verified')
    return user


def consume_token(raw_token):
    """Resolve a token string to its user and verify them.

    Returns ``(user, error)``. ``error`` is one of ``'unknown'``, ``'expired'``,
    ``'used'`` — distinguished so the page can offer to send a new link rather
    than showing one opaque failure for three different situations.
    """
    token = EmailVerificationToken.objects.filter(token=raw_token).select_related('user').first()
    if token is None:
        return None, 'unknown'
    if token.used_at is not None:
        # Already used. If the account is verified this is just a re-click of the
        # same link, which is not an error worth alarming anyone about.
        return (token.user, None) if token.user.email_verified else (token.user, 'used')
    if token.is_expired:
        return token.user, 'expired'

    token.consume()
    return mark_verified(token.user), None
