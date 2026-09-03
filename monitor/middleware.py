"""Access control for the application area of the site.

Four rules are enforced in one place, on every request, rather than being
repeated in each of the ~90 views:

1. **Organisation scoping.** A user who is not a platform admin may only touch
   URLs belonging to their own organisation. Every app and API URL carries the
   organisation as ``<uuid:org_id>``, so the check is a single comparison
   against ``user.organization_id``. This matters much more now that anyone can
   create an account from the public trial signup: without it, a stranger who
   signed up could read every client's coverage by guessing a URL.

2. **Email verification.** An account that has not proved control of its email
   address cannot reach the application at all. It can sign in, and it can finish
   verifying — nothing else.

3. **Onboarding.** An account part-way through onboarding is returned to the step
   it stopped at. Accounts that predate onboarding have no progress record and
   are exempt, so nobody who was already using the platform is asked to complete
   a flow they were never shown.

4. **The free-trial paywall.** Once an organisation's trial has run out and no
   package has been activated, its users are redirected to the billing page to
   pick a package. API calls get a 402 with the billing URL so front-end fetches
   fail loudly instead of silently rendering half a page.

Platform admins and superusers bypass rules 1 and 4. They do **not** bypass 2 and
3 for their own account — a staff account created today verifies its email like
any other — but every existing staff account was grandfathered by migration 0018
and so has nothing outstanding.

Feature entitlements are deliberately *not* enforced here. They are per-view and
per-branch (a report renders for everyone but only downloads on a paid plan), so
they live in monitor/entitlements.py and are applied at the views themselves.
"""
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


# URL names that stay reachable no matter what state the subscription is in —
# otherwise an expired organisation could not reach the page that lets it pay,
# or log out.
PAYWALL_EXEMPT_URL_NAMES = {
    'home', 'login', 'logout', 'signup', 'pricing', 'billing', 'package_request',
    # Paying is the way *out* of the paywall, so the checkout must never be
    # behind it. Without this an expired organisation — the only kind that
    # reaches checkout — would be redirected back to billing on its way to pay.
    'checkout_start', 'checkout_return', 'checkout_callback', 'checkout_cancelled',
    'assessment', 'assessment_submit', 'assessment_action',
    'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete',
}

# The onboarding wizard itself, plus the pages a half-onboarded user must still
# be able to reach. Without this the verification gate would redirect the verify
# page to itself.
ONBOARDING_URL_NAMES = {
    'onboarding_verify', 'onboarding_verify_confirm', 'onboarding_profile',
    'onboarding_agency', 'onboarding_terms', 'onboarding_privacy',
    'onboarding_disclaimer', 'onboarding_payment', 'onboarding_plan',
    'onboarding_done', 'onboarding_status', 'reconsent',
}

ALWAYS_ALLOWED_URL_NAMES = ONBOARDING_URL_NAMES | {
    'home', 'login', 'logout', 'pricing', 'signup',
    # A payment that has been made must always be able to complete, whatever
    # else the account still has outstanding.
    'checkout_return', 'checkout_callback', 'checkout_cancelled',
    # Public marketing, reachable at any point — a half-onboarded account
    # following a campaign link should see the page, not be bounced back.
    'assessment', 'assessment_submit', 'assessment_action',
    'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete',
}

# Only the application itself is gated. The landing page, auth pages and the
# webhook receiver are not under these prefixes.
GATED_PATH_PREFIXES = ('/app/', '/api/')


class OrganizationAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if user is None or not user.is_authenticated:
            return None
        if request.path.startswith('/admin/'):
            return None

        url_name = request.resolver_match.url_name if request.resolver_match else None
        is_staff_account = user.is_superuser or getattr(user, 'role', '') == 'platform_admin'

        # ── 2 & 3. Verification and onboarding ───────────────────────────────
        # Checked before the organisation rules so that a new account is sent to
        # the step it is on rather than to a page it cannot use yet. Applies to
        # staff accounts too — see the module docstring.
        if url_name not in ALWAYS_ALLOWED_URL_NAMES:
            gate = self._onboarding_gate(request, user)
            if gate is not None:
                return gate

        if is_staff_account:
            return None

        # ── 1. Organisation scoping ──────────────────────────────────────────
        org_id = view_kwargs.get('org_id')
        if org_id is not None and str(org_id) != str(user.organization_id or ''):
            return self._deny(request, "You don't have access to this organisation.")

        # ── 4. Trial paywall ─────────────────────────────────────────────────
        if url_name in PAYWALL_EXEMPT_URL_NAMES:
            return None
        if not request.path.startswith(GATED_PATH_PREFIXES):
            return None
        org = user.organization
        if org is not None and not org.has_platform_access:
            return self._paywall(request)
        return None

    # ── helpers ──────────────────────────────────────────────────────────────
    def _onboarding_gate(self, request, user):
        """Send a user who has not finished onboarding back to their next step.

        A missing progress record means the account predates onboarding — it is
        exempt, which is what stops this from locking out every existing user.
        """
        from . import onboarding

        progress = onboarding.get_progress(user)
        if progress is None or progress.is_complete:
            return None

        if not user.email_verified:
            return self._redirect_onboarding(
                request, reverse('monitor:onboarding_verify'),
                'Confirm your email address to continue.')

        step = onboarding.next_step(user)
        if step is None:
            onboarding.advance(user, 'complete')
            return None

        return self._redirect_onboarding(
            request, step.url, 'Finish setting up your account to continue.')

    def _redirect_onboarding(self, request, url, message):
        if self._is_api(request):
            return JsonResponse(
                {'error': message, 'code': 'onboarding_incomplete', 'onboarding_url': url},
                status=403,
            )
        return redirect(url)

    def _is_api(self, request):
        return request.path.startswith('/api/') or \
            request.headers.get('x-requested-with') == 'XMLHttpRequest'

    def _deny(self, request, message):
        if self._is_api(request):
            return JsonResponse({'error': message}, status=403)
        return redirect('monitor:organizations')

    def _paywall(self, request):
        billing_url = reverse('monitor:billing')
        if self._is_api(request):
            return JsonResponse(
                {'error': 'Your free trial has ended. Choose a package to continue.',
                 'billing_url': billing_url},
                status=402,
            )
        return redirect(billing_url)
