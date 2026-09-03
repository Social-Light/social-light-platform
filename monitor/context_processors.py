from django.conf import settings


def all_orgs(request):
    """Organisations offered in the header's "Switch Organisations" picker —
    every client for a platform admin, only their own for everyone else."""
    if not request.user.is_authenticated:
        return {'all_orgs': []}
    from .views import visible_organizations
    return {'all_orgs': visible_organizations(request.user)}


def external_links(request):
    """Expose URLs of separately-deployed sibling apps to all templates."""
    return {'article_extractor_url': settings.ARTICLE_EXTRACTOR_URL}


def entitlements(request):
    """`features.<code>` for every template, so locked/upgrade states are drawn
    from exactly the same entitlement data the backend enforces with. A template
    that forgets the check is a cosmetic bug, not a security one — the view
    rejects the request regardless."""
    from .entitlements import feature_flags
    return {'features': feature_flags(getattr(request, 'user', None))}


def onboarding(request):
    """`onboarding_state` and `onboarding_incomplete` for every template, so the
    app chrome can prompt a user who still has something outstanding. Absent for
    anonymous users and for accounts that predate onboarding."""
    from . import onboarding as flow

    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'onboarding_state': None, 'onboarding_incomplete': False}
    from .legal import outstanding_documents

    progress = flow.get_progress(user)
    incomplete = progress is not None and not progress.is_complete
    # Only prompt a settled account about new document versions. Someone still
    # in the wizard is already being asked for them by the wizard itself.
    outstanding = [] if incomplete else outstanding_documents(user)
    return {
        'onboarding_state': progress.state if progress else None,
        'onboarding_incomplete': incomplete,
        'onboarding_next_url': flow.next_url(user) if incomplete else None,
        'outstanding_documents': outstanding,
    }
