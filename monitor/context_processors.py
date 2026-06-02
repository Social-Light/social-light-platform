from django.conf import settings

from .models import Organization


def all_orgs(request):
    if request.user.is_authenticated:
        return {'all_orgs': Organization.objects.all().order_by('name')}
    return {'all_orgs': []}


def external_links(request):
    """Expose URLs of separately-deployed sibling apps to all templates."""
    return {'article_extractor_url': settings.ARTICLE_EXTRACTOR_URL}
