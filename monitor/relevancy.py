"""Relevancy scoring for media coverage.

Relevancy measures how strongly a piece of coverage matches an organisation's
tracked terms. It is a deterministic 0–100 score based on which of the org's
keywords *and competitors* appear in the headline + summary, weighted by type.
Coverage that names a tracked competitor is relevant too, so competitor terms
(the competitor's name plus its aliases) score alongside the org's own keywords.

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

# A tracked competitor being named is relevant coverage — weighted like the
# supporting keyword categories.
COMPETITOR_WEIGHT = 30

# Each additional occurrence of an already-matched keyword adds a small bonus
# (diminishing returns) so heavily-on-topic articles rank above passing mentions.
REPEAT_BONUS = 0.1

MAX_SCORE = 100.0


def _scoring_terms(keywords, competitors):
    """Yield (lowercased term, weight) for every tracked keyword and competitor.

    Competitors contribute their name plus aliases (Competitor.match_terms()).
    De-duplicated case-insensitively; a keyword's weight wins over a competitor's
    if the same term is tracked as both.
    """
    seen = set()
    for kw in keywords or []:
        term = (kw.keyword or '').strip().lower()
        if term and term not in seen:
            seen.add(term)
            yield term, CATEGORY_WEIGHTS.get(kw.category, DEFAULT_WEIGHT)
    for comp in competitors or []:
        for raw in comp.match_terms():
            term = raw.strip().lower()
            if term and term not in seen:
                seen.add(term)
                yield term, COMPETITOR_WEIGHT


def compute_relevancy(headline, summary='', keywords=None, org=None, competitors=None):
    """Return a 0–100 relevancy score for the given text.

    Pass ``keywords`` (Keyword instances) and/or ``competitors`` (Competitor
    instances) — preferred for bulk loops so the querysets are fetched once — or
    ``org`` to load both for that organisation. Whichever of keywords/competitors
    is left as None is loaded from ``org`` when given. With nothing tracked the
    score is 0.
    """
    if keywords is None:
        keywords = list(org.keywords.all()) if org is not None else []
    if competitors is None:
        competitors = list(org.competitors.all()) if org is not None else []
    if not keywords and not competitors:
        return 0.0

    text = f"{headline or ''} {summary or ''}".lower()
    if not text.strip():
        return 0.0

    score = 0.0
    for term, weight in _scoring_terms(keywords, competitors):
        occurrences = len(re.findall(r'\b' + re.escape(term) + r'\b', text))
        if occurrences:
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
