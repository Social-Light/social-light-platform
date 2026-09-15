"""AI query understanding for the media search box (media_online/print/social/
broadcast, competitors_view).

The underlying search is unchanged — still a plain DB filter (icontains on
headline/source/etc., plus sentiment/country/date_from/date_to). What this
adds is a translation step in front of it: a natural-language query like
"negative coverage about the fuel shortage last month" is turned into the
filter values that query already understands (keywords="fuel shortage",
sentiment="negative", date_from/date_to = last calendar month) — no new
storage, no embeddings, no backfill. A plain short query ("fuel shortage")
never reaches the AI at all; see resolve_filters' word-count gate below.

Model: claude-haiku-4-5, not claude-opus-5 like report_ai.py/sector_ai.py.
This is deliberate, not a cost shortcut: this call sits in a page load's
critical path (unlike those two, which are on-demand/background), so latency
is the deciding factor — Haiku is the right tool for a small, well-defined
extraction task where speed matters more than depth. See the Claude API
skill's "Haiku only for simple, speed-critical tasks" guidance.

If the SDK/API key is missing, the call fails, or the AI is simply slow, the
raw query is used as-is (today's plain icontains behaviour) — search must
never break or hang because the AI step had a bad day. Nothing here raises;
callers get today's exact behaviour back on any failure.
"""
import json
import logging
from datetime import date, timedelta

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'SEARCH_AI_MODEL', 'claude-haiku-4-5')

# Below this word count a query is almost always already a clean keyword/brand
# name ("FNBB", "fuel shortage") — sending it to the AI would only add latency
# for no benefit, so it skips the AI step entirely and behaves exactly as it
# always has.
MIN_WORDS_FOR_AI = 3

# Bound how long the AI step may add to a page load. A slow/hanging call must
# degrade to plain search, not stall the request.
TIMEOUT_SECONDS = 4.0

SYSTEM_PROMPT = (
    "You extract structured search filters from a short natural-language query "
    "about media coverage. You never invent facts — only interpret what the "
    "query actually says."
)

QUERY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "keywords": {
            "type": "string",
            "description": "The core subject/topic to search for, with sentiment "
                            "and date phrasing stripped out. Empty string if the "
                            "query is only a sentiment/date instruction with no topic.",
        },
        "sentiment": {
            "type": ["string", "null"],
            "enum": ["positive", "negative", "neutral", None],
            "description": "Only set if the query explicitly asks for a sentiment "
                            "(e.g. 'negative coverage', 'positive stories'); null otherwise.",
        },
        "days_back": {
            "type": ["integer", "null"],
            "description": "If the query names a relative time window (e.g. 'last "
                            "month' -> ~30, 'this week' -> ~7, 'last quarter' -> ~90), "
                            "how many days back from today that starts. Null if the "
                            "query names no time window.",
        },
    },
    "required": ["keywords", "sentiment", "days_back"],
    "additionalProperties": False,
}


def _looks_like_natural_language(q):
    return bool(q) and len(q.split()) >= MIN_WORDS_FOR_AI


def interpret_query(q):
    """Best-effort natural-language -> filter translation. Never raises — on
    any failure (no API key, network, timeout, bad response) returns the
    "no-op" result that leaves search behaving exactly as it does today."""
    noop = {'keywords': q, 'sentiment': None, 'date_from': None, 'date_to': None}
    if not _looks_like_natural_language(q):
        return noop

    api_key = (getattr(settings, 'ANTHROPIC_API_KEY', '') or '').strip().strip('"').strip("'")
    if not api_key:
        return noop

    try:
        import anthropic
    except ImportError:
        return noop

    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=TIMEOUT_SECONDS, max_retries=0)
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f'Query: "{q}"'}],
            output_config={"format": {"type": "json_schema", "schema": QUERY_JSON_SCHEMA}},
        )
        if response.stop_reason == 'max_tokens':
            return noop
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return noop
        data = json.loads(text)
    except Exception:
        # Deliberately broad: whatever goes wrong (timeout, rate limit, malformed
        # response), the fallback is the same — plain search, not a broken page.
        logger.warning("search_ai.interpret_query failed for q=%r", q, exc_info=True)
        return noop

    today = date.today()
    days_back = data.get('days_back')
    date_from = (today - timedelta(days=int(days_back))) if isinstance(days_back, int) and days_back > 0 else None

    return {
        'keywords': (data.get('keywords') or '').strip() or q,
        'sentiment': data.get('sentiment') or None,
        'date_from': date_from,
        'date_to': None,
    }


def resolve_filters(q, sentiment, date_from, date_to):
    """Apply interpret_query's output to a view's existing (q, sentiment,
    date_from, date_to) GET params — an explicit value the user already set
    via a dropdown/date picker always wins over anything the AI inferred.
    Returns (q, sentiment, date_from, date_to) ready to filter with, exactly
    the shape every media_* view already builds its queryset from."""
    parsed = interpret_query(q)
    return (
        parsed['keywords'],
        sentiment or parsed['sentiment'] or '',
        date_from or (parsed['date_from'].isoformat() if parsed['date_from'] else ''),
        date_to or (parsed['date_to'].isoformat() if parsed['date_to'] else ''),
    )
