"""
monitor/online_metrics.py

Automatic AVE (Advertising Value Equivalency) estimation for online coverage.

Before this, every OnlineArticle/CompetitorArticle created by the crawler
bridge (media-monitor's fetcher/bridge.py) defaulted to ave=0 — nothing ever
computed it automatically. The only place `ave` was ever set was a human
typing a number into the edit form or a CSV upload (see monitor/views.py's
`ave=float(data.get('ave', 0) or 0)` handlers). article-extractor's print
pipeline and the crawler's own social-post bridge (fetcher/bridge.py's
`_social_ave`) already auto-compute AVE for their channels; online coverage
was the one gap.

Formula mirrors article-extractor's print AVE calc for consistency across
channels (extractor.py's `calculate_ave`):

    AVE = base_rate × (reach / 1000) × sentiment_multiplier

Online coverage has no print "page position", so there's no page_multiplier
term here — everything else matches, including the sentiment weights, so an
org comparing print vs. online AVE isn't comparing two unrelated scales.

Reach figures below are real, verified monthly-traffic numbers for outlets
this platform already has in its media-source directory (from a media-source
export) — not invented. Any outlet not listed falls back to the same
conservative default article-extractor already uses for an unmatched print
publisher (reach=5000), so an unknown site doesn't silently score 0 nor get
an inflated number pulled from nowhere. Extend KNOWN_ONLINE_REACH as real
traffic data for more outlets becomes available.
"""
from urllib.parse import urlparse

# domain -> verified monthly reach (unique visitors / readership estimate).
KNOWN_ONLINE_REACH = {
    'businessweekly.co.bw':        161000,
    'guardiansun.co.bw':           362000,
    'mmegi.bw':                    833000,
    'sundaystandard.info':         684000,
    'thegazette.news':             381600,
    'thepatriot.co.bw':            351600,
    'thevoicebw.com':             1300000,
    'weekendpost.co.bw':           446000,
    'iol.co.za':                10850000,
    'moneyweb.co.za':            2504000,
    'techcentral.co.za':          750370,
    'mybroadband.co.za':       10000000,
    'businessinsider.com':     67940000,
    'africa.businessinsider.com': 2955000,
    'voanews.com':               2526000,
    'eastleighvoice.co.ke':       234785,
}

DEFAULT_REACH = 5000    # unmatched-outlet fallback — same figure article-extractor uses for an unknown print publisher
BASE_RATE = 250.0       # flat rate-card placeholder, matching article-extractor's unknown-publisher base_rate.
                         # Only reach is verified per-outlet below; a differentiated real ad-rate card isn't
                         # available, so the rate stays uniform and reach (real where known) drives the AVE
                         # difference between a major outlet and an unknown one.

# Same weights article-extractor's print AVE uses, so a positive/negative story
# is valued consistently whether it ran in print or online.
SENTIMENT_MULTIPLIER = {'positive': 1.2, 'neutral': 1.0, 'negative': 0.8, 'mixed': 1.0}


def _domain(source_or_url: str) -> str:
    """Bare host from a domain, source name, or full URL. '' if nothing parseable."""
    raw = (source_or_url or '').strip().lower()
    if not raw:
        return ''
    netloc = urlparse(raw).netloc or urlparse('//' + raw).netloc
    return netloc[4:] if netloc.startswith('www.') else netloc


def calculate_online_ave(source_or_url: str, sentiment: str = 'neutral') -> float:
    """
    Estimate AVE for one piece of online coverage.

    Args:
        source_or_url: the article's source/domain/URL — only the host is used.
        sentiment:      'positive' | 'neutral' | 'negative' | 'mixed' (case-insensitive).

    Returns:
        AVE rounded to 2 decimal places. Never raises — unrecognised input
        falls back to DEFAULT_REACH and neutral sentiment, same fail-open
        posture as the rest of the bridge.
    """
    domain = _domain(source_or_url)
    reach = KNOWN_ONLINE_REACH.get(domain, DEFAULT_REACH)
    mult = SENTIMENT_MULTIPLIER.get((sentiment or 'neutral').strip().lower(), 1.0)
    return round(BASE_RATE * (reach / 1000) * mult, 2)
