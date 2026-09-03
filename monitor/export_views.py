"""Coverage export — downloading the underlying crawl results as CSV.

This is the "crawl-result download" the plan tiers refer to. Until now coverage
could only be *uploaded*; the data a client's crawl produced could be read on
screen but never taken away in bulk, which is precisely the capability the paid
tiers advertise ("Full media database with export").

It is gated on ``crawl_result_download`` at the view, not in the template. A free
user who constructs this URL by hand gets the same refusal as one who clicks a
hidden button, because the check is here and not in the markup.

The response streams. A large organisation's twelve months of coverage is tens of
thousands of rows, and building that in memory to hand to ``HttpResponse`` would
be a way of turning an export into an outage.
"""
import csv
from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify

from .entitlements import require_feature
from .models import (BroadcastMention, CompetitorArticle, OnlineArticle, Organization,
                     PrintArticle, SocialMediaPost)


class _Echo:
    """A file-like object whose write() returns the line, so csv.writer can feed
    a generator instead of a buffer."""

    def write(self, value):
        return value


# What each media type exports. Keeping the column list here rather than deriving
# it from the model means adding a field to a model does not silently change the
# shape of every client's export.
EXPORTS = {
    'online': {
        'model': OnlineArticle,
        'related': 'online_articles',
        'label': 'Online coverage',
        'columns': [
            ('date_published', 'Date'), ('source', 'Source'), ('headline', 'Headline'),
            ('summary', 'Summary'), ('url', 'URL'), ('country', 'Country'),
            ('sentiment', 'Sentiment'), ('coverage', 'Coverage type'), ('reach', 'Reach'),
            ('ave', 'AVE'), ('relevancy', 'Relevancy'),
        ],
    },
    'print': {
        'model': PrintArticle,
        'related': 'print_articles',
        'label': 'Print coverage',
        'columns': [
            ('date_published', 'Date'), ('source', 'Publication'), ('headline', 'Headline'),
            ('summary', 'Summary'), ('author', 'Author'), ('section', 'Section'),
            ('url', 'URL'), ('country', 'Country'), ('sentiment', 'Sentiment'),
            ('reach', 'Readership'), ('ave', 'AVE'), ('relevancy', 'Relevancy'),
        ],
    },
    'social': {
        'model': SocialMediaPost,
        'related': 'social_posts',
        'label': 'Social coverage',
        'columns': [
            ('date_published', 'Date'), ('platform', 'Platform'), ('page_name', 'Page'),
            ('headline', 'Post'), ('summary', 'Summary'), ('url', 'URL'),
            ('country', 'Country'), ('sentiment', 'Sentiment'), ('reach', 'Reach'),
            ('ave', 'AVE'), ('relevancy', 'Relevancy'),
        ],
    },
    'broadcast': {
        'model': BroadcastMention,
        'related': 'broadcast_mentions',
        'label': 'Broadcast coverage',
        'columns': [
            ('date_published', 'Date'), ('source', 'Station'), ('broadcast_type', 'Type'),
            ('headline', 'Item'), ('summary', 'Summary'), ('url', 'URL'),
            ('country', 'Country'), ('sentiment', 'Sentiment'), ('duration', 'Duration'),
            ('ave', 'AVE'), ('relevancy', 'Relevancy'),
        ],
    },
    'competitor': {
        'model': CompetitorArticle,
        'related': 'competitor_articles',
        'label': 'Competitor coverage',
        'columns': [
            ('date_published', 'Date'), ('company_name', 'Company'), ('source', 'Source'),
            ('headline', 'Headline'), ('summary', 'Summary'), ('url', 'URL'),
            ('country', 'Country'), ('sentiment', 'Sentiment'), ('reach', 'Reach'),
            ('ave', 'AVE'),
        ],
    },
}


def _parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _rows(queryset, columns):
    writer = csv.writer(_Echo())
    yield writer.writerow([label for _field, label in columns])
    for obj in queryset.iterator(chunk_size=500):
        yield writer.writerow([_cell(obj, field) for field, _label in columns])


def _cell(obj, field):
    value = getattr(obj, field, '')
    if value is None:
        return ''
    return str(value)


@login_required
@require_feature('crawl_result_download')
def coverage_export(request, org_id, media_type):
    """Stream one media type's coverage for an organisation as CSV.

    Organisation scoping is enforced by ``OrganizationAccessMiddleware`` on the
    ``org_id`` in the URL, exactly as it is for every other ``/api/`` endpoint —
    a paid plan entitles you to export *your* coverage, not anybody else's.
    """
    spec = EXPORTS.get(media_type)
    if spec is None:
        raise Http404(f'Unknown coverage type: {media_type}')

    org = get_object_or_404(Organization, id=org_id)
    queryset = getattr(org, spec['related']).all()

    date_from = _parse_date(request.GET.get('date_from'))
    date_to = _parse_date(request.GET.get('date_to'))
    if date_from:
        queryset = queryset.filter(date_published__gte=date_from)
    if date_to:
        queryset = queryset.filter(date_published__lte=date_to)
    country = request.GET.get('country', '').strip()
    if country:
        queryset = queryset.filter(country__iexact=country)

    filename = f"{slugify(org.name)}-{media_type}-coverage.csv"
    response = StreamingHttpResponse(_rows(queryset, spec['columns']), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
