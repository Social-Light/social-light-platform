"""
monitor/mediahost.py

Client and ingest logic for the mediahost API (http://mh-api.mediahost.co.za).

The API exposes a single endpoint — GET /api/clips — that returns paginated
"clips" (media coverage items) for the authenticated client, filtered by an
allocation date range. Authentication is a single global API key sent in the
`x-api-key` header (settings.MEDIAHOST_API_KEY).

Clips come in three media types that map onto our existing models:

    Print     -> PrintArticle
    Online    -> OnlineArticle
    Broadcast -> BroadcastMention

The OpenAPI spec (v1.json) documents the request parameters but not the
response body. The mapping below is built from a real clip (confirmed via
--probe), whose uniform schema looks like::

    {"type": "Broadcast", "headline": "Headline: <h>\\r\\rSummary: <s>",
     "text": "<full text>", "source": "BDTV", "byline": "N/A",
     "pubDate": "10 Jun 2026 23:58:06", "allocateDate": "2026-06-11T00:17:18",
     "htmlLink": "N/A", "pdfLink": "https://.../18114774-...html",
     "ave": 1516.67, "reach": 213787, "country": "South Africa",
     "sentiment": "NS", "reference": 18114774, "allocationId": 39538427, ...}

Notes that drive the parsing: "N/A" is a null sentinel; the `headline` field
embeds both headline and summary; the clip URL lives in `pdfLink` (a stable
per-clip artifact used for dedup); dates are `pubDate` ("%d %b %Y %H:%M:%S")
with `allocateDate` (ISO) as fallback; sentiment uses codes like "NS".
"""
from __future__ import annotations

import re
import time
from datetime import datetime, date, timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .relevancy import compute_relevancy
from .print_metrics import estimate_print_reach


# mediahost datetime format for the from/to query params. All times are South
# Africa Standard Time (UTC+2), which is also this project's TIME_ZONE.
SAST_FMT = '%Y-%m-%d %H:%M:%S'

# Valid `type` filter values accepted by the API.
MEDIA_TYPES = ('Print', 'Online', 'Broadcast')

_SENTIMENT_MAP = {
    '1': 'positive', 'positive': 'positive', 'pos': 'positive', 'p': 'positive',
    '0': 'neutral', 'neutral': 'neutral', 'neu': 'neutral', '': 'neutral',
    'ns': 'neutral', 'not set': 'neutral', 'n': 'neutral',
    '-1': 'negative', 'negative': 'negative', 'neg': 'negative',
    'mixed': 'mixed',
}

# Values mediahost uses to mean "no value".
_NA_VALUES = {'', 'n/a', 'na', 'none', 'null', '-', 'unknown'}

# Candidate envelope keys that may wrap the list of clips in the JSON response.
_LIST_KEYS = ('data', 'clips', 'items', 'results', 'rows', 'records')


class MediahostError(Exception):
    """Raised when the mediahost API call fails."""


class MediahostClient:
    """Thin wrapper over GET /api/clips with x-api-key auth and pagination."""

    def __init__(self, api_key=None, base_url=None, timeout=None, retries=3):
        self.api_key = api_key or getattr(settings, 'MEDIAHOST_API_KEY', '')
        self.base_url = (base_url or getattr(
            settings, 'MEDIAHOST_API_URL', 'http://mh-api.mediahost.co.za')).rstrip('/')
        # The mediahost host is slow; default generously and allow override.
        self.timeout = timeout or getattr(settings, 'MEDIAHOST_TIMEOUT', 120)
        self.retries = retries
        if not self.api_key:
            raise MediahostError(
                'MEDIAHOST_API_KEY is not configured (set it in the environment).')

    def _get_page(self, params):
        url = f'{self.base_url}/api/clips'
        last_exc = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = requests.get(
                    url,
                    params=params,
                    headers={'x-api-key': self.api_key, 'Accept': 'application/json'},
                    timeout=self.timeout,
                )
                break
            except (requests.Timeout, requests.ConnectionError) as exc:
                # Transient: back off and retry (2s, 4s, 6s, ...).
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(2 * attempt)
                    continue
                raise MediahostError(
                    f'Request to {url} failed after {self.retries} attempts: {exc}') from exc
            except requests.RequestException as exc:
                raise MediahostError(f'Request to {url} failed: {exc}') from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise MediahostError(f'Authentication failed ({resp.status_code}). Check MEDIAHOST_API_KEY.')
        if resp.status_code >= 400:
            raise MediahostError(f'mediahost API returned {resp.status_code}: {resp.text[:500]}')

        try:
            return resp.json()
        except ValueError as exc:
            raise MediahostError(f'Response was not valid JSON: {resp.text[:500]}') from exc

    @staticmethod
    def _extract_items(payload):
        """Pull the list of clips out of whatever envelope the API used."""
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in _LIST_KEYS:
                value = payload.get(key)
                if isinstance(value, list):
                    return value
            # A dict that already looks like a single clip.
            if payload:
                return [payload]
        return []

    def fetch_clips(self, date_from, date_to, media_type=None, page_size=500, max_pages=1000):
        """Yield every clip across all pages for the given window.

        date_from / date_to may be datetime objects or pre-formatted SAST
        strings. media_type, if given, must be one of MEDIA_TYPES.
        """
        params_base = {
            'from': _fmt_sast(date_from),
            'to': _fmt_sast(date_to),
            'pageSize': page_size,
        }
        if media_type:
            params_base['type'] = media_type

        page = 1
        while page <= max_pages:
            payload = self._get_page({**params_base, 'page': page})
            items = self._extract_items(payload)
            if not items:
                break
            for item in items:
                yield item
            # Stop when the last page was not full. If the envelope advertises a
            # page count, honour it as well.
            total_pages = _read_total_pages(payload)
            if total_pages is not None:
                if page >= total_pages:
                    break
            elif len(items) < page_size:
                break
            page += 1


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_sast(value):
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.strftime(SAST_FMT)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).strftime(SAST_FMT)
    raise MediahostError(f'Unsupported date value: {value!r}')


def _read_total_pages(payload):
    if not isinstance(payload, dict):
        return None
    for key in ('totalPages', 'total_pages', 'pageCount', 'pages'):
        if key in payload:
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                return None
    return None


def _is_blank(value):
    """True for None, '', or an N/A sentinel ("N/A", "-", "null", ...)."""
    return value is None or _to_str(value).lower() in _NA_VALUES


def _first(clip, keys, default=''):
    """First candidate value that isn't blank/an N/A sentinel (case-tolerant keys).

    Crucially this skips "N/A" — so e.g. _first(('htmlLink','pdfLink')) returns the
    real pdfLink even when htmlLink is the literal string "N/A".
    """
    if not isinstance(clip, dict):
        return default
    for key in keys:
        if key in clip and not _is_blank(clip[key]):
            return clip[key]
    # Fall back to a case-insensitive match.
    lowered = {k.lower(): v for k, v in clip.items()}
    for key in keys:
        if not _is_blank(lowered.get(key.lower())):
            return lowered[key.lower()]
    return default


def _to_float(value):
    try:
        return float(str(value).replace(',', '').strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value):
    try:
        return int(float(str(value).replace(',', '').strip() or 0))
    except (TypeError, ValueError):
        return 0


def _to_str(value):
    return ('' if value is None else str(value)).strip()


def _clean(value):
    """Like _to_str, but mediahost null sentinels ("N/A", "-", ...) become ''."""
    s = _to_str(value)
    return '' if s.lower() in _NA_VALUES else s


def _parse_date(value):
    """Parse a clip date: ISO 8601, mediahost's "10 Jun 2026 23:58:06", or date-only."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except ValueError:
        pass
    for fmt in (SAST_FMT, '%d %b %Y %H:%M:%S', '%d %b %Y', '%Y-%m-%d',
                '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _sentiment(clip):
    raw = _clean(_first(clip, ('sentiment', 'sentimentLabel', 'tone'))).lower()
    return _SENTIMENT_MAP.get(raw, 'neutral')


def _clip_url(clip):
    """The clip's link. htmlLink is usually "N/A"; pdfLink is the stable artifact."""
    return _clean(_first(clip, ('url', 'link', 'htmlLink', 'pdfLink', 'imageLink')))


def _headline_and_summary(clip):
    """mediahost packs both into one field as "Headline: <h>\\r\\rSummary: <s>".

    Returns (headline, summary). Falls back to the raw value as headline, and to
    the `text` field for the summary when no embedded Summary is present.
    """
    raw = _clean(_first(clip, ('headline', 'title', 'mention')))
    headline, summary = raw, ''
    if raw:
        normalised = raw.replace('\r', '\n')
        m = re.match(r'\s*Headline\s*:\s*(.*?)\s*(?:\n+\s*Summary\s*:\s*(.*))?$',
                     normalised, re.IGNORECASE | re.DOTALL)
        if m:
            headline = (m.group(1) or '').strip()
            summary = (m.group(2) or '').strip()
    if not summary:
        summary = _clean(_first(clip, ('summary', 'snippet', 'description', 'text')))
    return headline, summary


def _clip_type(clip, fallback=None):
    """Resolve a clip's media type to one of MEDIA_TYPES."""
    raw = _to_str(_first(clip, ('type', 'mediaType', 'media_type', 'clipType'))).lower()
    for t in MEDIA_TYPES:
        if raw == t.lower():
            return t
    # mediahost sometimes labels broadcast sub-types (radio/tv/podcast).
    if raw in ('radio', 'tv', 'television', 'podcast', 'broadcast'):
        return 'Broadcast'
    if raw in ('print', 'newspaper', 'magazine'):
        return 'Print'
    if raw in ('online', 'web', 'social'):
        return 'Online'
    return fallback


# ── clip -> model builders ──────────────────────────────────────────────────────
# Each returns (instance, dedup_key) or (None, reason) when the clip is unusable.

def _dedup_key(url, headline, published):
    url = (url or '').strip().lower()
    if url:
        return ('url', url)
    return ('hd', (headline or '').strip().lower(), published)


def _clip_date(clip):
    """pubDate is the publication time; allocateDate is when it was allocated."""
    return _parse_date(_first(clip, ('pubDate', 'publicationDate', 'publication_date',
                                     'date', 'allocateDate', 'allocationDate', 'createdAt')))


_BROADCAST_TYPE_MAP = {
    'radio': 'RADIO', 'tv': 'TV', 'television': 'TV',
    'podcast': 'PODCAST', 'online': 'ONLINE',
}


def _broadcast_type(clip):
    """Resolve the radio/tv/podcast sub-type. mediahost's `mediaType` is an opaque
    code (e.g. "STT"), so fall back to a heuristic on the station/source name."""
    raw = _clean(_first(clip, ('stationType', 'station_type', 'broadcastType'))).lower()
    if raw in _BROADCAST_TYPE_MAP:
        return _BROADCAST_TYPE_MAP[raw]
    source = _clean(_first(clip, ('source', 'station', 'client'))).upper()
    if 'PODCAST' in source:
        return 'PODCAST'
    if 'TV' in source:
        return 'TV'
    if 'FM' in source or 'RADIO' in source:
        return 'RADIO'
    return 'RADIO'


def is_youtube_clip(clip):
    """True when a clip originates from YouTube (by link host or source name).

    Used to temporarily suppress YouTube coverage at ingest time. The same rule
    is reused by the remove_youtube_articles management command for cleanup.
    """
    url = _clip_url(clip).lower()
    if 'youtube.com' in url or 'youtu.be' in url:
        return True
    source = _clean(_first(clip, ('source', 'station', 'publication', 'client'))).lower()
    return 'youtube' in source


def _search_term(clip):
    """The mediahost search/brand a clip matched (e.g. "FNBB") — used to route it
    to the organisation(s) that track that term as a keyword."""
    return _clean(_first(clip, ('search', 'searchName', 'searchTerm', 'keyword')))


def _print_section(clip):
    """Section name for a print clip (e.g. Main, Business, Sport).

    The feed sometimes carries a bare page number in 'section', or only a numeric
    'page' — a section is a name, not a number, so don't fall back to 'page' and
    drop purely-numeric values (they leave the section blank → shown as 'Main')."""
    value = _clean(_first(clip, ('section',)))
    return '' if value.isdigit() else value


def _map_clip(clip):
    """Map a raw clip to (ctype, fields, dedup_key, error).

    `fields` holds the model fields for its type EXCEPT `organization` and (for
    Online) `relevancy`, which depend on the target org and are filled in by the
    caller. On failure `error` is 'unknown_type' or 'invalid' and fields is None.
    """
    ctype = _clip_type(clip)
    if ctype not in MEDIA_TYPES:
        return None, None, None, 'unknown_type'

    headline, summary = _headline_and_summary(clip)
    published = _clip_date(clip)
    if not headline or not published:
        return ctype, None, None, 'invalid'

    url = _clip_url(clip)
    fields = {
        'headline': headline,
        'summary': summary,
        'url': url,
        'date_published': published,
        'country': _clean(_first(clip, ('country', 'region'))),
        'sentiment': _sentiment(clip),
        'ave': _to_float(_first(clip, ('ave', 'AVE'))),
    }
    if ctype == 'Print':
        publication = _clean(_first(clip, ('source', 'publication', 'station')))
        fields.update(
            source=publication,
            author=_clean(_first(clip, ('byline', 'author'))),
            section=_print_section(clip),
            # Print has no measured reach — estimate it from circulation, unless the
            # feed supplies one.
            reach=_to_int(_first(clip, ('reach', 'audience'))) or estimate_print_reach(publication),
        )
    elif ctype == 'Online':
        fields.update(
            source=_clean(_first(clip, ('source', 'publication'))),
            coverage=_clean(_first(clip, ('coverage_type', 'coverage', 'coverageType'))) or 'Not Set',
            reach=_to_int(_first(clip, ('reach', 'audience'))),
        )
    else:  # Broadcast
        fields.update(
            source=_clean(_first(clip, ('source', 'station', 'client'))),
            duration=_clean(_first(clip, ('duration',))),
            broadcast_type=_broadcast_type(clip),
        )
    return ctype, fields, _dedup_key(url, headline, published), None


# ── public ingest entry point ───────────────────────────────────────────────────

def _build_search_map(restrict_org=None):
    """Map a lowercased search/keyword term -> [organisations that track it].

    Built from the Keyword table so a clip's `search` value (e.g. "FNBB") routes
    to the org(s) owning that keyword. A term tracked by several orgs routes to
    all of them. `restrict_org` limits the map to a single org (for testing).

    Inactive (disabled) organisations are excluded, so a disabled org receives no
    new mentions until it is reactivated.
    """
    from .models import Keyword

    kw_qs = Keyword.objects.select_related('organization').filter(
        organization__status='active')
    if restrict_org is not None:
        kw_qs = kw_qs.filter(organization=restrict_org)

    search_to_orgs = {}
    for kw in kw_qs:
        term = (kw.keyword or '').strip().lower()
        if not term:
            continue
        orgs = search_to_orgs.setdefault(term, [])
        if kw.organization not in orgs:
            orgs.append(kw.organization)
    return search_to_orgs


def ingest_clips(date_from, date_to, media_type=None, dry_run=False,
                 client=None, page_size=200, logger=None, restrict_org=None):
    """Fetch clips and create rows, routing each clip to the organisation(s)
    whose Keyword matches the clip's `search` term. Deduped per org.

    Returns a summary dict::

        {
          'fetched': int,
          'created': {'Print': n, 'Online': n, 'Broadcast': n},   # totals across orgs
          'skipped': {'duplicate': n, 'invalid': n, 'unknown_type': n, 'unmapped': n,
                      'youtube': n, 'low_relevancy': n},
          'per_org': {org_name: {'Print': n, 'Online': n, 'Broadcast': n}},
          'unmapped_searches': {search_term: count},
          'sample': <first raw clip or None>,
        }

    'low_relevancy' counts clips that matched an org's Keyword-routed `search`
    term but scored 0 via compute_relevancy() against that org's actual
    keywords/competitors, and were skipped rather than inserted — routing above
    only checks Mediahost's own `search` label, never the clip's content, so
    this is the only content-based check before a row is written.

    When `dry_run` is True nothing is written; counts reflect what *would* be
    created. `media_type` restricts the API call to one of MEDIA_TYPES;
    `restrict_org` limits routing to a single organisation.
    """
    from .models import PrintArticle, OnlineArticle, BroadcastMention

    if media_type and media_type not in MEDIA_TYPES:
        raise MediahostError(f'Invalid media_type {media_type!r}; expected one of {MEDIA_TYPES}.')

    client = client or MediahostClient()
    model_for = {'Print': PrintArticle, 'Online': OnlineArticle, 'Broadcast': BroadcastMention}
    search_to_orgs = _build_search_map(restrict_org)

    win_from = _parse_date(date_from) or (timezone.localdate() - timedelta(days=3650))
    win_to = _parse_date(date_to) or timezone.localdate()

    summary = {
        'fetched': 0,
        'created': {'Print': 0, 'Online': 0, 'Broadcast': 0},
        'skipped': {'duplicate': 0, 'invalid': 0, 'unknown_type': 0, 'unmapped': 0, 'youtube': 0,
                    'low_relevancy': 0},
        'per_org': {},
        'unmapped_searches': {},
        'sample': None,
    }

    # TEMPORARY: suppress YouTube clips (toggle via settings.MEDIAHOST_EXCLUDE_YOUTUBE).
    exclude_youtube = getattr(settings, 'MEDIAHOST_EXCLUDE_YOUTUBE', True)

    # Per-org state, built lazily the first time an org is touched.
    org_keywords = {}      # org.id -> [Keyword]     (for relevancy scoring)
    org_competitors = {}   # org.id -> [Competitor]  (competitor coverage is relevant too)
    seen = {}              # (org.id, ctype) -> set of dedup keys (preloaded from DB)
    buckets = {}           # (org.id, ctype) -> [model instances]
    prepared = set()

    def _prepare(org):
        if org.id in prepared:
            return
        prepared.add(org.id)
        org_keywords[org.id] = list(org.keywords.all())
        org_competitors[org.id] = list(org.competitors.all())
        for ctype, model in model_for.items():
            existing = model.objects.filter(
                organization=org, date_published__range=(win_from, win_to)
            ).values_list('url', 'headline', 'date_published')
            seen[(org.id, ctype)] = {_dedup_key(u, h, d) for u, h, d in existing}
            buckets[(org.id, ctype)] = []

    for clip in client.fetch_clips(date_from, date_to, media_type=media_type, page_size=page_size):
        summary['fetched'] += 1
        if summary['sample'] is None:
            summary['sample'] = clip

        if exclude_youtube and is_youtube_clip(clip):
            summary['skipped']['youtube'] += 1
            continue

        term = _search_term(clip)
        orgs = search_to_orgs.get(term.lower()) if term else None
        if not orgs:
            summary['skipped']['unmapped'] += 1
            label = term or '(no search)'
            summary['unmapped_searches'][label] = summary['unmapped_searches'].get(label, 0) + 1
            continue

        ctype, fields, key, err = _map_clip(clip)
        if err:
            summary['skipped'][err] += 1
            continue

        for org in orgs:
            _prepare(org)
            bucket_seen = seen[(org.id, ctype)]
            if key in bucket_seen:
                summary['skipped']['duplicate'] += 1
                continue

            # Relevancy gates INSERTION here, not just UI display (see
            # relevancy.filter_relevant, which is a separate, independent gate on
            # top of this one). Routing above matches only Mediahost's own `search`
            # label against our keyword string — it never checks the clip's actual
            # content — so an over-broad label on Mediahost's side lands here
            # unfiltered otherwise. Confirmed necessary 2026-08-11: FNBB had 1,297
            # relevancy=0 rows in production, all pan-African wire noise (Stanbic
            # Ghana, Access Bank Nigeria, PowerBall jackpots, ...) with zero
            # Botswana/FNBB overlap, routed under a "FNBB"-labelled search that
            # Mediahost itself was clearly casting far too wide.
            #
            # Caveat — compute_relevancy() only counts LITERAL keyword/competitor
            # string matches in headline+summary. Genuinely relevant coverage that
            # doesn't happen to repeat a configured term (e.g. "Morupule B Power
            # Station Commissions" for a BPC-focused org, with no literal "BPC" in
            # the text) also scores 0 and is skipped here. If a real story goes
            # missing after this gate, the fix is almost always to add the missing
            # name/alias to the org's Keyword list — not to lower this threshold.
            # All mention models carry the relevancy field, so score every type,
            # not just Online. Competitors count too — coverage naming a tracked
            # competitor is relevant.
            relevancy = compute_relevancy(
                fields['headline'], fields['summary'],
                keywords=org_keywords[org.id], competitors=org_competitors[org.id])
            if relevancy <= 0:
                summary['skipped']['low_relevancy'] += 1
                continue

            bucket_seen.add(key)
            row = dict(fields, organization=org, relevancy=relevancy)
            buckets[(org.id, ctype)].append(model_for[ctype](**row))
            summary['created'][ctype] += 1
            po = summary['per_org'].setdefault(org.name, {'Print': 0, 'Online': 0, 'Broadcast': 0})
            po[ctype] += 1

    if not dry_run:
        for (org_id, ctype), rows in buckets.items():
            if rows:
                model_for[ctype].objects.bulk_create(rows, batch_size=500)

    if logger and summary['unmapped_searches']:
        top = sorted(summary['unmapped_searches'].items(), key=lambda kv: -kv[1])[:10]
        logger('Unmapped searches (no org keyword): '
                + ', '.join(f'{t}×{n}' for t, n in top))

    return summary
