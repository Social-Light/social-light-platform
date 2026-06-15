"""Relevancy scoring for media coverage.

Relevancy measures how strongly a piece of coverage matches an organisation's
tracked keywords. It is a deterministic 0–100 score based on which of the org's
keywords appear in the headline + summary, weighted by keyword category.

The score is computed at creation time (single add, CSV upload, webhook) so it
needs no external services and stays fast for bulk imports.
"""
import re

# Points awarded the first time a keyword of each category is matched.
# Brand mentions are the strongest relevancy signal; personnel/campaign are
# supporting signals. Unknown categories fall back to DEFAULT_WEIGHT.
CATEGORY_WEIGHTS = {
    'brand': 50,
    'personnel': 30,
    'campaign': 30,
}
DEFAULT_WEIGHT = 20

# Each additional occurrence of an already-matched keyword adds a small bonus
# (diminishing returns) so heavily-on-topic articles rank above passing mentions.
REPEAT_BONUS = 0.1

MAX_SCORE = 100.0


def compute_relevancy(headline, summary='', keywords=None, org=None):
    """Return a 0–100 relevancy score for the given text.

    Pass either ``keywords`` (an iterable of Keyword instances — preferred for
    bulk loops so the queryset is fetched once) or ``org`` (the score will load
    that org's keywords). With no keywords configured the score is 0.
    """
    if keywords is None:
        if org is None:
            return 0.0
        keywords = list(org.keywords.all())

    text = f"{headline or ''} {summary or ''}".lower()
    if not text.strip():
        return 0.0

    score = 0.0
    for kw in keywords:
        term = (kw.keyword or '').strip().lower()
        if not term:
            continue
        occurrences = len(re.findall(r'\b' + re.escape(term) + r'\b', text))
        if occurrences:
            weight = CATEGORY_WEIGHTS.get(kw.category, DEFAULT_WEIGHT)
            score += weight + (occurrences - 1) * weight * REPEAT_BONUS

    return round(min(score, MAX_SCORE), 2)


def relevance_threshold():
    """The minimum relevancy score a mention must reach to be surfaced.

    Read from settings each call so it can be tuned via the
    ``MENTION_RELEVANCY_THRESHOLD`` env var without a restart of unrelated state.
    Defaults to 0 (no filtering) when unset.
    """
    from django.conf import settings
    return getattr(settings, 'MENTION_RELEVANCY_THRESHOLD', 0) or 0


def filter_relevant(qs):
    """Restrict a mention queryset to rows meeting the relevancy threshold.

    A no-op when the threshold is 0, so callers can apply it unconditionally.
    The queryset's model must have a ``relevancy`` field (all four mention
    models do).
    """
    threshold = relevance_threshold()
    return qs.filter(relevancy__gte=threshold) if threshold else qs
