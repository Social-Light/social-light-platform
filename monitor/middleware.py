"""Access control for the application area of the site.

Two rules are enforced in one place, on every request, rather than being
repeated in each of the ~90 views:

1. **Organisation scoping.** A user who is not a platform admin may only touch
   URLs belonging to their own organisation. Every app and API URL carries the
   organisation as ``<uuid:org_id>``, so the check is a single comparison
   against ``user.organization_id``. This matters much more now that anyone can
   create an account from the public trial signup: without it, a stranger who
   signed up could read every client's coverage by guessing a URL.

2. **The free-trial paywall.** Once an organisation's 14-day trial has run out
   and no package has been activated, its users are redirected to the billing
   page to pick a package. API calls get a 402 with the billing URL so front-end
   fetches fail loudly instead of silently rendering half a page.

Platform admins and superusers bypass both rules.
"""
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse


# URL names that stay reachable no matter what state the subscription is in —
# otherwise an expired organisation could not reach the page that lets it pay,
# or log out.
PAYWALL_EXEMPT_URL_NAMES = {
    'home', 'login', 'logout', 'signup', 'pricing', 'billing', 'package_request',
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
        if user.is_superuser or getattr(user, 'role', '') == 'platform_admin':
            return None
        if request.path.startswith('/admin/'):
            return None

        url_name = request.resolver_match.url_name if request.resolver_match else None

        # ── 1. Organisation scoping ──────────────────────────────────────────
        org_id = view_kwargs.get('org_id')
        if org_id is not None and str(org_id) != str(user.organization_id or ''):
            return self._deny(request, "You don't have access to this organisation.")

        # ── 2. Trial paywall ─────────────────────────────────────────────────
        if url_name in PAYWALL_EXEMPT_URL_NAMES:
            return None
        if not request.path.startswith(GATED_PATH_PREFIXES):
            return None
        org = user.organization
        if org is not None and not org.has_platform_access:
            return self._paywall(request)
        return None

    # ── helpers ──────────────────────────────────────────────────────────────
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
