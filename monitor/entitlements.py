"""The single source of truth for *what a user may do*, as distinct from *what a
user may see*.

The platform already knew which package an organisation was on (``Package``) and
whether its subscription was live (``Organization.effective_plan_status``). What
it did not have was a machine-readable answer to "is this account allowed to
download a report?" — ``Package.features`` is a list of marketing bullets shown
on the price-list card, edited freely in the admin, and must never be used to
decide access: rewording a bullet would silently revoke a paying client's
functionality.

So entitlements live in their own validated field, ``Package.entitlements``, as a
list of codes drawn from the registry below. Everything else in the application
asks one of three questions and never inspects a package directly:

    user.has_feature('report_download')          # in Python
    {% if features.report_download %}            # in a template
    @require_feature('report_download')          # over a view

Adding a paid capability means adding a ``Feature`` here and ticking it on the
packages that include it in the Django admin — no code change anywhere else.
"""
import functools

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse


class Feature:
    """One gated capability.

    ``upgrade_message`` is what the user is actually shown — both by the API when
    it rejects the request and by the template when it renders the locked state —
    so the two can never drift apart into "this feature is not available on your
    plan" in one place and a generic 403 in the other.
    """

    def __init__(self, code, label, description, upgrade_message, category='General'):
        self.code = code
        self.label = label
        self.description = description
        self.upgrade_message = upgrade_message
        self.category = category

    def __str__(self):
        return self.label

    def __repr__(self):
        return f'<Feature {self.code}>'


UPGRADE_SUFFIX = 'Please upgrade your plan to use it.'


def _f(code, label, description, upgrade, category):
    return Feature(code, label, description, f'{upgrade} {UPGRADE_SUFFIX}', category)


# ── The registry ─────────────────────────────────────────────────────────────
# Order matters only for presentation (admin help text, the plan comparison on
# the onboarding plan step). Codes are permanent: renaming one orphans the
# entitlement lists already stored against every package.
FEATURES = {f.code: f for f in [
    # Monitoring
    _f('basic_monitoring', 'Basic monitoring',
       'View online, print, social and broadcast coverage collected for the organisation.',
       'Media monitoring is not included on your current plan.', 'Monitoring'),
    _f('dashboard', 'Dashboard',
       'The coverage overview, headline figures and recent-mentions feed.',
       'The dashboard is not available on your current plan.', 'Monitoring'),
    _f('media_sources', 'Media source management',
       'Maintain the organisation\'s own list of monitored sources.',
       'Media source management is not available on your current plan.', 'Monitoring'),

    # Analysis
    _f('advanced_analytics', 'Advanced analytics',
       'Sentiment trends, share-of-voice, reach and AVE analysis over time.',
       'Advanced analytics is a paid feature.', 'Analysis'),
    _f('competitor_analysis', 'Competitor analysis',
       'Track competitors and compare their coverage against your own.',
       'Competitor analysis is a paid feature.', 'Analysis'),
    _f('campaigns', 'Campaign tracking',
       'Track named campaigns and measure the coverage they generate.',
       'Campaign tracking is a paid feature.', 'Analysis'),
    _f('ai_analysis', 'AI-assisted analysis',
       'Generated executive summaries, issue narratives and recommendations.',
       'AI-assisted analysis is a paid feature.', 'Analysis'),

    # Reports
    _f('premium_reports', 'Premium reports',
       'Issue-focused ("saga") reports, campaign reports and the full report builder.',
       'Premium reports are not included on your current plan.', 'Reports'),
    _f('report_download', 'Report downloads',
       'Download reports as PDF or PowerPoint.',
       'Downloading reports is not available on your current plan.', 'Reports'),
    _f('crawl_result_download', 'Coverage / crawl-result downloads',
       'Export the underlying coverage records as CSV.',
       'Exporting coverage data is not available on your current plan.', 'Reports'),

    # Delivery
    _f('alerts', 'Email alerts',
       'Scheduled keyword alert digests delivered by email.',
       'Email alerts are not available on your current plan.', 'Delivery'),
    _f('api_access', 'API access',
       'Programmatic read access to the organisation\'s coverage.',
       'API access is not included on your current plan.', 'Delivery'),
]}


ALL_FEATURES = tuple(FEATURES)

# What an account gets with no paid package behind it. Overridable per-deployment
# with FREE_PLAN_ENTITLEMENTS so the free tier can be widened or narrowed without
# a release.
DEFAULT_FREE_ENTITLEMENTS = (
    'basic_monitoring',
    'dashboard',
    'alerts',
)


def feature_choices():
    """``(code, label)`` pairs for form/admin widgets."""
    return [(f.code, f.label) for f in FEATURES.values()]


def get_feature(code):
    return FEATURES.get(code)


def upgrade_message(code):
    feat = FEATURES.get(code)
    if feat:
        return feat.upgrade_message
    return f'This feature is not available on your current plan. {UPGRADE_SUFFIX}'


def free_entitlements():
    return set(getattr(settings, 'FREE_PLAN_ENTITLEMENTS', DEFAULT_FREE_ENTITLEMENTS))


def trial_entitlements():
    """What the free trial unlocks. Defaults to everything, which is what a trial
    has always granted here — the trial is a full-product evaluation, and its
    limit is the 14-day clock, not the feature set."""
    configured = getattr(settings, 'TRIAL_ENTITLEMENTS', None)
    if configured is None:
        return set(ALL_FEATURES)
    return set(configured)


def _clean(codes):
    """Drop anything not in the registry, so a stale code left in a package's
    JSON after a feature is retired can never grant access to nothing."""
    if isinstance(codes, str):
        codes = [c.strip() for c in codes.replace('\n', ',').split(',')]
    return {str(c).strip() for c in (codes or []) if str(c).strip() in FEATURES}


def entitlements_for_organization(org):
    """The set of feature codes an organisation is entitled to right now.

    The rules, in order:

    * No organisation at all → the free set.
    * A live trial → :func:`trial_entitlements` (everything, by default).
    * A paid subscription with a package → that package's entitlements.
    * A paid subscription with **no** package → everything. This is the state
      every organisation that predates self-signup is in (``plan_status``
      defaults to ``'active'`` with ``package = NULL``), and they have always had
      unrestricted access. Treating them as free-tier would revoke functionality
      from existing clients, so they keep it.
    * Anything else — expired, pending — the free set. Those organisations are
      already stopped at the paywall by the middleware; this is the backstop.
    """
    if org is None:
        return free_entitlements()

    status = org.effective_plan_status

    if status == 'trial':
        return trial_entitlements()

    if status == 'active':
        package = org.package
        if package is None:
            return set(ALL_FEATURES)
        granted = _clean(package.entitlements)
        # A package with nothing ticked yet is a configuration gap, not an
        # instruction to lock a paying client out of everything.
        return granted or free_entitlements()

    return free_entitlements()


def entitlements_for_user(user):
    """Everything for staff; otherwise whatever the user's organisation has."""
    if user is None or not user.is_authenticated:
        return set()
    if user.is_superuser or getattr(user, 'role', '') == 'platform_admin':
        return set(ALL_FEATURES)
    return entitlements_for_organization(user.organization)


def user_has_feature(user, code):
    return code in entitlements_for_user(user)


def feature_flags(user):
    """Every feature code mapped to True/False — what the context processor hands
    templates so they can render locked states from the same data the backend
    enforces with."""
    granted = entitlements_for_user(user)
    return {code: (code in granted) for code in FEATURES}


# ── Enforcement ──────────────────────────────────────────────────────────────

def _wants_json(request):
    return (
        request.path.startswith('/api/')
        or request.headers.get('x-requested-with') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('accept', '')
    )


def locked_response(request, code):
    """The rejection. JSON for API/XHR callers, a real upgrade page for a normal
    browser navigation — never a bare 403, so a user who reaches a paid URL
    directly is told what to do about it."""
    message = upgrade_message(code)
    feat = get_feature(code)
    upgrade_url = reverse('monitor:billing')

    if _wants_json(request):
        return JsonResponse({
            'error': message,
            'code': 'feature_not_available',
            'feature': code,
            'feature_label': feat.label if feat else code,
            'upgrade_url': upgrade_url,
        }, status=403)

    org = getattr(request.user, 'organization', None) if request.user.is_authenticated else None
    return render(request, 'monitor/feature_locked.html', {
        'org': org,
        'feature': feat,
        'feature_code': code,
        'message': message,
        'upgrade_url': upgrade_url,
    }, status=403)


def enforce_feature(request, code):
    """Return a rejection response, or ``None`` when the request may proceed.

    For views that are only *partly* paid — the sentiment report renders on
    screen for everyone but only downloads as PDF on a paid plan — so the gate
    has to sit on the download branch rather than over the whole view.
    """
    if user_has_feature(request.user, code):
        return None
    return locked_response(request, code)


def require_feature(code):
    """Decorator form, for views that are paid in their entirety.

    Sits *below* ``@login_required`` so an anonymous user is sent to log in
    rather than being told to upgrade.
    """
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f'{settings.LOGIN_URL}?next={request.path}')
            denied = enforce_feature(request, code)
            if denied is not None:
                return denied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator
