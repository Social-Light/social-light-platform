from django.conf import settings

from .models import Organization


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
