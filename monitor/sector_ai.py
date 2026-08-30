"""AI-generated public sector-intelligence content: SectorStory and
CommodityQuote rows for the landing page's "Sector Intelligence" section
(monitor/templates/monitor/landing.html).

Unlike report_ai.py/issue_report_ai.py, this doesn't analyse the platform's own
ingested mentions — it searches the live web (Claude's server-side web_search
tool) for genuinely current news and prices, since this content is public
marketing-page material shown to visitors who aren't any org's client, not a
summary of any client's monitored coverage.

Primary model: claude-opus-5, same as report_ai.py, via the Messages API's
structured outputs (output_config.format: json_schema) combined with the
web_search tool in a single call — Claude searches (server-side, no client
loop needed) and composes the final schema-constrained answer in one
client.messages.create().

Free fallback (added 2026-08-30, for whenever Anthropic is unavailable — no
credit balance, auth, rate limit, whatever): SearXNG (self-hosted metasearch,
same instance media-monitor's discovery/services/search_api.py uses) does the
actual search, then Groq (openai/gpt-oss-120b, already used by
sentiment_ai.py/report_ai.py — free-tier friendly) selects and formats from
those real results. Groq is told to extract only from what search actually
returned, never to free-generate, so the fallback can't hallucinate a
story/price search didn't find. generate_sector_stories/generate_commodity_
quotes try Anthropic first and drop to this automatically on SectorAIError;
callers don't need to know or care which path actually answered.
(Considered, and rejected: Groq's own "compound"/"compound-mini" models,
which have built-in web search — live-tested 2026-08-30 and every prompt
that actually triggers their search tool 413s on this account. The listed
models respond fine, but not to anything requiring a real search.)

Every row this writes carries is_ai_generated=True (see the SectorStory/
CommodityQuote model docstrings) — an editor's own hand-written rows are never
touched. Scheduled daily via Celery Beat -> monitor.update_sector_intelligence
(see tasks.py / the update_sector_intelligence management command); can also
be run ad hoc: ``python manage.py update_sector_intelligence``.

If both the primary and free fallback fail, callers get a SectorAIError — the
caller decides whether that's fatal (the management command logs and
continues to the next sector rather than aborting the whole run over one bad
search).
"""
import json
import logging
import re
from datetime import date

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'REPORT_AI_MODEL', 'claude-opus-5')  # reuse report_ai.py's override hook

# Free fallback path (2026-08-30): when Anthropic fails — no credit balance,
# auth, rate limit, whatever — generate_sector_stories/generate_commodity_quotes
# fall back to SearXNG (self-hosted metasearch, same instance media-monitor's
# discovery/services/search_api.py uses) for the actual search, then Groq
# (already used by sentiment_ai.py/report_ai.py, free-tier friendly) to select
# and format from those real results. Groq is instructed to extract ONLY from
# the search results it's given — never free-generate — so this can't
# hallucinate a story/price search didn't actually return.
FALLBACK_MODEL = getattr(settings, 'SECTOR_AI_FALLBACK_MODEL', 'openai/gpt-oss-120b')

FALLBACK_SYSTEM_PROMPT = (
    "You extract genuinely current information from real web search results provided to "
    "you below. Only use what is actually in the provided results — never invent, estimate, "
    "or fill gaps from general knowledge. If the results don't contain a solid answer, say so."
)

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


# ── Free fallback: SearXNG (search) + Groq (extract/format) ───────────────────

def _searxng_search(query, count=8, time_range='week', categories='news'):
    """Free, self-hosted web search. Returns a list of {title, url, content,
    published_date} dicts, or [] if the instance is unreachable/misconfigured
    or returns nothing — never raises, since "no SearXNG" and "SearXNG found
    nothing" both mean the same thing to a caller: no results to work with."""
    base_url = (getattr(settings, 'SEARXNG_URL', '') or '').rstrip('/')
    if not base_url:
        return []
    params = {'q': query, 'format': 'json', 'categories': categories}
    if time_range:
        params['time_range'] = time_range
    try:
        resp = requests.get(f'{base_url}/search', params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get('results', [])
    except (requests.RequestException, ValueError) as exc:
        logger.warning('sector_ai: SearXNG search failed for %r: %s', query, exc)
        return []

    out = []
    for r in results[:count]:
        url = (r.get('url') or '').strip()
        if not url:
            continue
        out.append({
            'title': (r.get('title') or '').strip(),
            'url': url,
            'content': (r.get('content') or '').strip(),
            'published_date': (r.get('publishedDate') or '').strip(),
        })
    return out


def _groq_extract_json(user_prompt, max_tokens=2000):
    """One Groq structured-JSON call for the free fallback path. Raises
    SectorAIError on any failure, mirroring _run()'s contract, so callers
    don't need to know which backend actually produced (or failed to
    produce) a result."""
    try:
        import groq
    except ImportError:
        raise SectorAIError('The "groq" package is not installed (pip install groq).')

    api_key = (getattr(settings, 'GROQ_API_KEY', '') or '').strip().strip('"').strip("'")
    if not api_key:
        raise SectorAIError('Free fallback unavailable — GROQ_API_KEY is not set.')

    client = groq.Groq(api_key=api_key, timeout=30.0, max_retries=1)
    try:
        completion = client.chat.completions.create(
            model=FALLBACK_MODEL,
            # gpt-oss spends hidden reasoning tokens before the visible JSON —
            # confirmed live 2026-08-30 (too tight a budget silently returns
            # empty content, same finding as sentiment_ai.py/report_ai.py).
            # This is headroom, not a target.
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": FALLBACK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
    except groq.AuthenticationError:
        raise SectorAIError('Groq authentication failed — the API key is invalid.')
    except groq.RateLimitError:
        raise SectorAIError('Groq rate limit reached. Please try again shortly.')
    except groq.APIStatusError as exc:
        raise SectorAIError(f'Groq API error (HTTP {exc.status_code}). Please try again.')
    except groq.APIConnectionError:
        raise SectorAIError('Could not reach Groq in time (timeout or network).')

    choice = completion.choices[0]
    if choice.finish_reason == 'length':
        raise SectorAIError('The free fallback response was truncated.')
    content = (choice.message.content or '').strip()
    if not content:
        raise SectorAIError('The free fallback returned no content.')
    start, end = content.find('{'), content.rfind('}')
    if start == -1 or end == -1:
        raise SectorAIError("No JSON object in the free fallback's response")
    return json.loads(content[start:end + 1])


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

    try:
        data = _run(user_prompt, STORY_JSON_SCHEMA, max_uses=8)
        stories = data.get('stories') or []
    except SectorAIError as primary_exc:
        logger.warning(
            "sector_ai: Anthropic search failed for sector %r (%s) — trying free SearXNG+Groq fallback",
            sector.name, primary_exc,
        )
        try:
            stories = _generate_sector_stories_free(sector, count)
        except SectorAIError as fallback_exc:
            raise SectorAIError(
                f'AI search unavailable (Anthropic: {primary_exc}; free fallback: {fallback_exc})'
            ) from fallback_exc

    return _clean_stories(stories, count)


def _clean_stories(stories, count):
    """Shared validation/truncation for both the Anthropic path's 'stories'
    and the free fallback's — same shape, same rules either way."""
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


def _generate_sector_stories_free(sector, count):
    """Free fallback: SearXNG finds real, current results; Groq selects and
    formats from THOSE results only — it's told to extract, not generate, so
    it can't hallucinate a story search didn't find. Returns a raw 'stories'
    list in the same shape _run() would, for _clean_stories to validate."""
    results = _searxng_search(f"{sector.name} Botswana", count=count * 3,
                               time_range='week', categories='news')
    if not results:
        return []

    listing = "\n\n".join(
        f"[{i}] {r['title']}\nURL: {r['url']}\nPublished: {r['published_date'] or 'unknown'}\n"
        f"Excerpt: {r['content'][:400]}"
        for i, r in enumerate(results)
    )
    user_prompt = f"""Below are {len(results)} real, current web search results about the \
{sector.name} sector in Botswana and Southern Africa.

{listing}

From ONLY the results above, pick up to {count} that report genuine business/market news — \
deals, regulatory or policy changes, production/output figures, investments, disruptions, \
credible market commentary. Skip anything that isn't real news (ads, unrelated topics, or a \
duplicate of a story you've already picked).

Return a JSON object: {{"stories": [{{"title": "...", "summary": "...", "source_label": "...", \
"url": "...", "published_on": "YYYY-MM-DD or empty string if unknown"}}, ...]}}. "url" MUST be \
copied verbatim from the listing above — never guessed or altered. "summary" is one or two \
sentences of genuine substance from the excerpt — never invented. "source_label" is the outlet, \
inferred from the URL's domain. If nothing above genuinely qualifies, return {{"stories": []}}."""

    data = _groq_extract_json(user_prompt, max_tokens=2500)
    return data.get('stories') or []


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

    try:
        data = _run(user_prompt, QUOTE_JSON_SCHEMA, max_uses=len(names) + 2)
        quotes = data.get('quotes') or []
    except SectorAIError as primary_exc:
        logger.warning(
            "sector_ai: Anthropic quote search failed (%s) — trying free SearXNG+Groq fallback",
            primary_exc,
        )
        quotes = _generate_commodity_quotes_free(names)

    return _clean_quotes(quotes, names)


def _clean_quotes(quotes, names):
    """Shared validation for both the Anthropic path's 'quotes' and the free
    fallback's — same shape, same rules either way."""
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


def _generate_commodity_quotes_free(names):
    """Free fallback: one SearXNG search per commodity, Groq extracts a
    price/change from those results only (never invents one). Unlike stories,
    this never raises — a commodity search finding nothing is not an error
    (see generate_commodity_quotes' docstring) — UNLESS every single one
    fails for a real reason (bad key, rate limit, etc.), in which case that's
    surfaced rather than silently reported as 'nothing found'.

    Known weak spot (confirmed live 2026-08-30, several query phrasings
    tried): SearXNG's own snippets on this instance run ~120-150 chars and
    rarely carry a clean numeric price, unlike headline+snippet being enough
    for stories above. Groq correctly returns found=False rather than
    fabricate a number from a vague snippet, so this path often comes back
    empty — that's it working as designed, not a bug. Fetching each result's
    full article text (like media-monitor's fetcher does) would likely fix
    this, at the cost of being a real fetch pipeline rather than a search
    call; not built here since it wasn't asked for."""
    quotes = []
    errors = []
    for name in names:
        # categories='general' returns nothing on this SearXNG instance (no
        # engines enabled for it, confirmed live 2026-08-30) — 'news' does.
        results = _searxng_search(f"{name} price today", count=6,
                                   time_range='day', categories='news')
        if not results:
            continue

        listing = "\n\n".join(
            f"[{i}] {r['title']}\nExcerpt: {r['content'][:400]}"
            for i, r in enumerate(results)
        )
        user_prompt = f"""Below are real, current web search results for "{name} price today":

{listing}

From ONLY the text above, extract today's real market price and day-over-day (or most recent \
trading session) percentage change for {name}. Return a JSON object: {{"found": true or false, \
"price_display": "...", "change_percent": number}}. "price_display" must be the price as it \
would actually be quoted, with currency/unit (e.g. "$2,412.30/oz" or "$185/tonne"). If nothing \
above gives a solid current figure, return {{"found": false}} — do not estimate or invent."""

        try:
            data = _groq_extract_json(user_prompt, max_tokens=800)
        except SectorAIError as exc:
            logger.warning('sector_ai: free fallback quote extraction failed for %s: %s', name, exc)
            errors.append(exc)
            continue
        if not data.get('found'):
            continue
        quotes.append({
            'name': name,
            'price_display': data.get('price_display'),
            'change_percent': data.get('change_percent'),
        })

    if not quotes and errors and len(errors) == len(names):
        # every attempt hard-failed (config/auth/rate-limit) — that's a real
        # problem, not "the market has nothing to report today".
        raise SectorAIError(f'Free fallback failed for every commodity: {errors[0]}')
    return quotes
