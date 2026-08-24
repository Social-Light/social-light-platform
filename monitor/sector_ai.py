"""AI-generated public sector-intelligence content: SectorStory and
CommodityQuote rows for the landing page's "Sector Intelligence" section
(monitor/templates/monitor/landing.html).

Unlike report_ai.py/issue_report_ai.py, this doesn't analyse the platform's own
ingested mentions — it searches the live web (Claude's server-side web_search
tool) for genuinely current news and prices, since this content is public
marketing-page material shown to visitors who aren't any org's client, not a
summary of any client's monitored coverage.

Model: claude-opus-5, same as report_ai.py, via the Messages API's structured
outputs (output_config.format: json_schema) combined with the web_search tool
in a single call — Claude searches (server-side, no client loop needed) and
composes the final schema-constrained answer in one client.messages.create().

Every row this writes carries is_ai_generated=True (see the SectorStory/
CommodityQuote model docstrings) — an editor's own hand-written rows are never
touched. Scheduled daily via Celery Beat -> monitor.update_sector_intelligence
(see tasks.py / the update_sector_intelligence management command); can also
be run ad hoc: ``python manage.py update_sector_intelligence``.

If the SDK/API key is missing or a generation fails, callers get a
SectorAIError — the caller decides whether that's fatal (the management
command logs and continues to the next sector rather than aborting the whole
run over one bad search).
"""
import json
import logging
import re
from datetime import date

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'REPORT_AI_MODEL', 'claude-opus-5')  # reuse report_ai.py's override hook

STORIES_PER_SECTOR = 6

# Botswana's economy is dominated by diamonds, with copper, coal and increasingly
# oil/gas exploration also material — gold and Brent crude are included as
# widely-tracked regional/global benchmarks. price_display is free text (see the
# CommodityQuote docstring) specifically so a commodity without a simple spot
# price (diamonds trade via private tender, not an exchange) can still be shown
# as an index level or range rather than a fabricated per-carat figure.
COMMODITIES = ['Diamond', 'Copper', 'Gold', 'Coal', 'Brent Crude Oil']

SYSTEM_PROMPT = (
    "You are a research assistant for a media-intelligence company's public "
    "website. You use web search to find genuinely current, real news and "
    "prices — never invent, estimate or reuse stale information. If a search "
    "turns up nothing solid for an item, omit that item rather than guess."
)


class SectorAIError(Exception):
    """A caller-presentable failure while generating sector-intelligence content."""


def _client(api_key):
    import anthropic  # lazy import so the app runs without the SDK installed
    return anthropic.Anthropic(api_key=api_key, timeout=180.0, max_retries=1)


def _get_api_key():
    api_key = (getattr(settings, 'ANTHROPIC_API_KEY', '') or '').strip().strip('"').strip("'")
    if not api_key:
        raise SectorAIError('AI is not configured — set ANTHROPIC_API_KEY.')
    return api_key


def _run(user_prompt, schema, max_uses=6):
    """Shared call path: web_search tool + json_schema structured output, one
    non-streaming call. Wraps every SDK exception in SectorAIError so callers
    never need to import anthropic themselves."""
    try:
        import anthropic
    except ImportError:
        raise SectorAIError('The "anthropic" package is not installed (pip install anthropic).')

    api_key = _get_api_key()
    client = _client(api_key)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AuthenticationError:
        raise SectorAIError('Anthropic authentication failed — the API key is invalid. '
                            'Check ANTHROPIC_API_KEY.')
    except anthropic.RateLimitError:
        raise SectorAIError('Anthropic rate limit reached. Please try again shortly.')
    except anthropic.APIStatusError as exc:
        raise SectorAIError(f'Anthropic API error (HTTP {exc.status_code}). Please try again.')
    except anthropic.APIConnectionError:
        raise SectorAIError('Could not reach the AI service in time (timeout or network).')

    if response.stop_reason == 'max_tokens':
        raise SectorAIError('The AI response was truncated.')
    if response.stop_reason == 'pause_turn':
        # A long-running server-tool turn stopped mid-search-loop rather than
        # finishing. There is no client tool to resume it with here (web_search
        # is fully server-side) — surface it as a failure rather than silently
        # returning a partial/no answer.
        raise SectorAIError('The AI paused mid-search and did not finish. Try again.')

    text_blocks = [b.text for b in response.content if b.type == "text"]
    if not text_blocks:
        raise SectorAIError('The AI returned no text response.')
    return _parse_json(text_blocks[-1])


def _parse_json(text):
    """Defensive extraction — output_config.format guarantees the block IS
    valid JSON, but this mirrors report_ai.py's belt-and-braces parsing in
    case a future model/SDK version wraps it in commentary."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise SectorAIError("No JSON object in the AI's response")
    return json.loads(text[start:end + 1])


# ── Sector stories ─────────────────────────────────────────────────────────────

STORY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "source_label": {"type": "string"},
                    "url": {"type": "string"},
                    "published_on": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["title", "summary", "source_label", "url", "published_on"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["stories"],
    "additionalProperties": False,
}


def generate_sector_stories(sector, count=STORIES_PER_SECTOR):
    """Search the web for real, current news relevant to ``sector`` (a Sector
    instance) and return up to ``count`` story dicts ready for SectorStory.
    Raises SectorAIError on config/API failure. Returns [] if the search
    genuinely turns up nothing usable — that's not an error."""
    today = date.today().isoformat()
    user_prompt = f"""Find {count} genuinely current news stories about the {sector.name} sector in \
Botswana and neighbouring Southern African countries (South Africa, Namibia, Zambia, Zimbabwe), \
published within roughly the last 7 days as of {today}.

Prioritise stories with real business/market impact: major deals, regulatory or policy changes, \
production or output figures, significant investments, notable disruptions, and credible market \
commentary — not routine press-release fluff.

For each story return:
- "title": the real headline (or a faithful close paraphrase if the exact headline is unwieldy).
- "summary": one or two sentences of genuine substance from the actual article — never invented.
- "source_label": how to credit the source, e.g. an outlet name/type ("Reuters", "Mmegi Online",
  "Regional business press").
- "url": the real article URL.
- "published_on": the article's actual real publish date, YYYY-MM-DD — never today's date unless
  the story genuinely broke today.

Only include stories you found via search and can point to a real URL for. If fewer than {count}
genuinely qualify, return fewer rather than padding with weaker items — an empty list is fine if
nothing qualifies."""

    data = _run(user_prompt, STORY_JSON_SCHEMA, max_uses=8)
    stories = data.get('stories') or []
    out = []
    for row in stories[:count]:
        title = (row.get('title') or '').strip()
        url = (row.get('url') or '').strip()
        if not title or not url:
            continue  # a story this module can't attribute to a real link isn't worth publishing
        pub = _parse_date(row.get('published_on'))
        out.append({
            'title': title[:300],
            'summary': (row.get('summary') or '').strip()[:2000],
            'source_label': (row.get('source_label') or 'Web search').strip()[:120],
            'url': url[:2000],
            'published_on': pub,
        })
    return out


def _parse_date(value):
    try:
        y, m, d = (value or '').split('-')
        parsed = date(int(y), int(m), int(d))
        # A model occasionally hallucinates a future date — clamp to today rather
        # than publish something that reads as from the future.
        return min(parsed, date.today())
    except (ValueError, TypeError):
        return date.today()


# ── Commodity quotes ───────────────────────────────────────────────────────────

QUOTE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price_display": {"type": "string"},
                    "change_percent": {"type": "number"},
                },
                "required": ["name", "price_display", "change_percent"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["quotes"],
    "additionalProperties": False,
}


def generate_commodity_quotes(names=None):
    """Search the web for today's real price (or index/range where there's no
    simple spot price — see the module-level COMMODITIES comment) and % change
    for each commodity in ``names``. Returns quote dicts ready for
    CommodityQuote, in the same order as ``names``. Raises SectorAIError on
    config/API failure; omits (doesn't fabricate) any commodity search
    couldn't find a real current figure for."""
    names = names or COMMODITIES
    today = date.today().isoformat()
    items = "\n".join(f"- {n}" for n in names)
    user_prompt = f"""Find today's ({today}) real market price and day-over-day (or most recent \
trading session) percentage change for each of these commodities:

{items}

For each, return:
- "name": exactly as given above.
- "price_display": the price as it would actually be quoted, with currency/unit, e.g. "$2,412.30/oz"
  or "$185/tonne". If a commodity has no simple daily spot price (diamonds trade via private tender,
  not an exchange), use a genuine index or benchmark instead, e.g. "Index: 142.3 pts" or a real
  reported range — clearly still a real, searched figure, not invented.
- "change_percent": the percentage change as a plain number (e.g. 1.2 or -0.8), matching what you
  found; 0 only if the source genuinely reports no change.

Only include a commodity if you found a real, current figure via search. Omit any you can't find
solid data for — do not estimate or invent a plausible-sounding number."""

    data = _run(user_prompt, QUOTE_JSON_SCHEMA, max_uses=len(names) + 2)
    quotes = data.get('quotes') or []
    by_name = {(q.get('name') or '').strip().lower(): q for q in quotes}
    out = []
    for name in names:
        row = by_name.get(name.lower())
        if not row:
            continue  # couldn't find this one — leave it out rather than fabricate
        price = (row.get('price_display') or '').strip()
        if not price:
            continue
        try:
            change = float(row.get('change_percent') or 0)
        except (TypeError, ValueError):
            change = 0.0
        out.append({'name': name, 'price_display': price[:40], 'change_percent': round(change, 2)})
    return out
