"""Single sign-on handoff into the Newspaper Extractor sibling app.

The extractor (``/opt/sociallight/article-extractor``) is a completely
separate Django project with its own ``User`` model and its own login —
there is no shared session between the two. Historically the "Newspaper
Extractor" nav link just opened it cold, and reaching actual extraction
access ("Agency" role) meant an admin manually inviting the person there and
them setting a second password.

This module is the main-platform half of the fix: a logged-in org member
hitting this view gets redirected straight into the extractor, already
signed in, with no extractor-side signup at all. The extractor half —
``sso_consume``, in article-extractor's ``users/views.py`` — verifies the
token this mints and does the actual login there.

The token is not a standing credential. It is signed, 60-second, single-use,
and names exactly one ``(email, org, plan)`` — it cannot be edited, forged
without ``EXTRACTOR_SSO_SECRET``, or replayed once consumed. See that
setting's own comment in ``socialmonitor/settings.py`` for why it is its own
secret rather than either app's ``SECRET_KEY``.
"""
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect

from .models import Organization

# Must match article-extractor's users/views.py:SSO_HANDOFF_TTL_SECONDS and
# SSO_SIGNING_SALT exactly — the two are verifying each other's work, not
# independent choices.
HANDOFF_TTL_SECONDS = 60
SSO_SIGNING_SALT = 'extractor-sso'


def _signer():
    # TimestampSigner falls back to SECRET_KEY when key='' — exactly the
    # thing this setting exists to avoid (see the module docstring: rotating
    # one app's SECRET_KEY must not silently break the other side's handoff).
    # An unset EXTRACTOR_SSO_SECRET must fail loudly here, not sign tokens
    # article-extractor can never verify and fail mysteriously over there.
    secret = settings.EXTRACTOR_SSO_SECRET
    if not secret:
        raise ImproperlyConfigured(
            'EXTRACTOR_SSO_SECRET is not set — required for the Newspaper '
            'Extractor SSO handoff (see monitor/extractor_sso.py).')
    return signing.TimestampSigner(key=secret, salt=SSO_SIGNING_SALT)


@login_required
def handoff(request, org_id):
    """Mint a one-shot token and send the browser to the extractor with it.

    Scoped to the caller's own organisation the same way every other
    ``<uuid:org_id>`` view is — see OrganizationAccessMiddleware's module
    docstring. A platform admin viewing someone else's org still only ever
    gets a token for *that* org, never their own.
    """
    org = get_object_or_404(Organization, id=org_id)
    is_staff_account = request.user.is_superuser or request.user.role == 'platform_admin'
    if not is_staff_account and str(org.id) != str(request.user.organization_id or ''):
        raise Http404

    # An expired/pending org can never reach this view in the first place —
    # the nav link this serves only renders inside /app/, which the paywall
    # already stops first — so there are only ever two plans to claim here.
    #
    # 'active' alone isn't "paid": an org that self-selects the Free tier
    # during onboarding (monitor/onboarding_views.py:_assign_plan) is also
    # 'active', on the Free package — same as a trial, not a genuine paying
    # client. Matches the distinction entitlements.py already draws (a Free
    # package grants the same limited set a trial does); a legacy org with no
    # package at all predates self-signup and has always had full access.
    package = org.package
    is_paid = org.effective_plan_status == 'active' and (package is None or package.slug != 'free')
    plan = 'active' if is_paid else 'trial'

    payload = {
        'email': request.user.email,
        'org_id': str(org.id),
        'org_name': org.name,
        'plan': plan,
        'nonce': secrets.token_urlsafe(24),
    }
    token = _signer().sign_object(payload)

    base = settings.ARTICLE_EXTRACTOR_URL.rstrip('/')
    return redirect(f'{base}/sso/{token}/')
