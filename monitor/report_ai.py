"""AI-generated report analysis: ESG, Stakeholder, Sectorial Competitor, and
Reputational Risks / Opportunities.

Two Groq calls (openai/gpt-oss-120b) per (organisation, period) — split to fit
Groq's rate limit, see the 2026-08-29 note below — together
produces the structured data for report sections that the raw models can't
supply on their own. The result is persisted in the ReportAnalysis model and
reused for the life of that record — there is no time-based expiry. It is
regenerated only when new mentions have been added to the period (see
``_has_new_data`` / ``analysis_is_stale``) or when the user forces a regenerate.
This survives restarts and keeps normal page views off the API.

Generation is on demand (the report's "Generate AI Analysis" button); page loads
call ``get_cached_analysis`` only. If the SDK/API key is missing or a generation
fails, the helpers return None / raise ReportAIError and the report simply omits
the AI sections instead of erroring.

2026-08-24: was hardcoded to Groq's llama-3.3-70b-versatile, which GET
https://api.groq.com/openai/v1/models confirmed is no longer on this account's
catalog at all (calls were failing outright). Moved to openai/gpt-oss-120b,
still on Groq — but that model's rate limit on this account is 8,000 TPM
combined input+output, verified against the real API, and one single call for
the full schema (fixed prompt overhead + a big enough mention sample + enough
output room to avoid truncation) didn't fit. Moved to Anthropic the same day
instead (also what README.md/RUNBOOK.md always documented — Groq was an
undocumented substitution at some point).

2026-08-29: moved back to Groq. The Anthropic account has no credit balance
and isn't expected to for a while — see _friendly_status_error's docstring
for how that surfaced. The 8,000 TPM ceiling is real, but the earlier attempt
treated it as "shrink the mentions to fit one call"; the actual fix is
splitting the ONE call into TWO smaller ones instead. Measured against the
real API: the full 60-mention block alone is ~3,570 input tokens, and it's
the OUTPUT side (the full 6-section schema needs 5,500+ tokens to avoid
truncation) that actually blew the single-call budget, not the input. Each
half below needs the full mention context but only produces half the
schema, so it comfortably fits: input barely changes per call, but required
output roughly halves. No structured-output enforcement on Groq (no
json_schema equivalent to Anthropic's output_config here) — same prompt-
plus-_parse_json-plus-_normalise defence this used before Anthropic.
"""
import json
import logging
import re
import time

from django.conf import settings
from django.urls import reverse

from .relevancy import filter_relevant

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'REPORT_AI_MODEL', 'openai/gpt-oss-120b')
# 2026-08-29: was 60. Measured against the real API, gpt-oss-120b's hidden
# reasoning cost (spent before the final JSON, counted against the same
# budget) scales with how many mentions it has to reason through, not with
# how much JSON it ultimately emits — 60 mentions truncated the
# competitor/risks/opportunities/kpi call even at max_tokens=6000, while 25
# completed cleanly at a comfortable 5,072 of 8,000 tokens total. This is the
# real constraint the two-call split (see the module docstring) couldn't
# route around by itself.
MAX_MENTIONS = 25  # cap the prompt size / token cost

# ESG Analysis is a matrix: SASB-style issues (rows) × stakeholders (columns),
# each cell a -100..100 sentiment score, plus a per-issue narrative. Paginated
# 2 issues per page to match the deck (ESG Analysis 1/N … N/N).
_STAKEHOLDERS = [
    ('government',  'Government / Politicians'),
    ('regulators',  'Regulators'),
    ('customers',   'Customers'),
    ('communities', 'Communities'),
]
_ESG_ISSUES = [
    'Financial Inclusion & Access',
    'Fair Lending & Responsible Finance',
    'Data Security & Customer Privacy',
    'Business Ethics & Transparency',
    'Customer Welfare & Product Responsibility',
    'Employee Diversity & Wellbeing',
    'Community Investment & Development',
    'Environmental & Climate Impact',
]
ESG_PER_PAGE = 2

SYSTEM_PROMPT = (
    "You are a senior media-intelligence analyst. You read a sample of media "
    "mentions about an organisation and return a concise, structured analysis. "
    "Respond with ONLY a single JSON object — no markdown, no prose, no code fences."
)


class ReportAIError(Exception):
    """A user-presentable failure while generating the AI analysis."""


# Probed as GROQ_API_KEY, GROQ_API_KEY_2, ... GROQ_API_KEY_10 — same pool
# sentiment_ai.py/date_ai.py draw from (GROQ_API_KEY specifically is shared
# with all of them). Duplicated here rather than imported so this module
# still works if theirs changes shape; issue_report_ai.py imports this copy
# rather than keeping a third.
_MAX_GROQ_KEYS = 10


def _groq_api_keys():
    """Every configured Groq key, in fallback order — see sentiment_ai.py's
    identically-named helper for the full story on why a key's daily token
    cap needs a fallback, not a wait.

    GROQ_API_KEY_REPORTS is appended last: it's dedicated to report_ai.py/
    issue_report_ai.py (never read by sentiment_ai.py/date_ai.py), reserved
    specifically as a spare for these two report generators — added
    2026-09-01 after the shared pool above turned out to be fully spent by
    live ingestion traffic exactly when a report needed it. Tried last, not
    first, so it stays fresh for as long as possible rather than being
    drained on every run alongside the shared keys."""
    keys = []
    attrs = ['GROQ_API_KEY'] + [f'GROQ_API_KEY_{i}' for i in range(2, _MAX_GROQ_KEYS + 1)]
    for attr in attrs:
        key = (getattr(settings, attr, '') or '').strip().strip('"').strip("'")
        if key:
            keys.append(key)
    reports_key = (getattr(settings, 'GROQ_API_KEY_REPORTS', '') or '').strip().strip('"').strip("'")
    if reports_key:
        keys.append(reports_key)
    return keys


def _friendly_status_error(exc, provider='Groq'):
    """Turn a provider APIStatusError into a message that actually tells the
    user what to do, rather than a blanket "please try again" that's actively
    misleading for a non-transient failure (e.g. an empty credit balance,
    where retrying can never succeed) — surfaced 2026-08-29 testing this
    against the real Anthropic API for the first time; kept generic (provider
    is a parameter, not hardcoded) since this moved to Groq the same day and
    may move again. Neither provider has a distinct exception subclass for a
    bad-billing 400, so it's detected by message text."""
    body = str(exc)
    if 'credit balance is too low' in body.lower() or 'insufficient' in body.lower():
        return ReportAIError(
            f'The {provider} account has run out of credit. Add credits, then try again.')
    return ReportAIError(f'{provider} API error (HTTP {exc.status_code}). Please try again.')


def _record(org, date_from, date_to):
    from .models import ReportAnalysis
    return ReportAnalysis.objects.filter(
        organization=org, date_from=date_from, date_to=date_to,
    ).first()


def _store(org, date_from, date_to, payload):
    from .models import ReportAnalysis
    ReportAnalysis.objects.update_or_create(
        organization=org, date_from=date_from, date_to=date_to,
        defaults={'payload': payload},
    )


def _log_analysis_event(org, date_from, date_to):
    """Record an Event so alert digests notify recipients that a fresh AI analysis
    is available for this period. Only called for a genuinely new/regenerated
    result — not for the empty-coverage sentinel or a reused cached analysis."""
    from .models import Event
    base = (getattr(settings, 'SITE_URL', 'https://sociallight.africa') or '').rstrip('/')
    Event.objects.create(
        organization=org, category='report', event_type='analysis_generated',
        title=f'AI analysis ready: {org.name} ({date_from}–{date_to})',
        summary='ESG, stakeholder and sectorial competitor analysis has been generated for this period.',
        url=f"{base}{reverse('monitor:report_full', args=[org.id])}?date_from={date_from}&date_to={date_to}",
    )


def get_cached_analysis(org, date_from, date_to):
    """Return the stored analysis dict for this period, or None if none exists.
    Persists for the life of the record (no time-based expiry). Never calls the
    API — safe on every page load."""
    rec = _record(org, date_from, date_to)
    if not rec:
        return None
    return rec.payload or None  # empty-dict sentinel → None


def get_analysis_generated_at(org, date_from, date_to):
    """Timestamp of the stored, non-empty analysis, or None."""
    rec = _record(org, date_from, date_to)
    if rec and rec.payload:
        return rec.updated_at
    return None


def _has_new_data(org, date_from, date_to, since):
    """True if any mention in the period was added after ``since`` — i.e. the
    analysis generated at that time no longer reflects all the coverage."""
    kw = dict(date_published__gte=date_from, date_published__lte=date_to, created_at__gt=since)
    for rel in (org.online_articles, org.print_articles, org.social_posts, org.broadcast_mentions):
        if rel.filter(**kw).exists():
            return True
    return False


def analysis_is_stale(org, date_from, date_to):
    """True when a stored analysis exists but new mentions have been added to the
    period since it was generated, so it should be regenerated to include them."""
    rec = _record(org, date_from, date_to)
    if not rec:
        return False
    return _has_new_data(org, date_from, date_to, rec.updated_at)


def generate_analysis(org, date_from, date_to, force=False):
    """Generate (and persist) the analysis via the Groq API. Returns the dict,
    or None when there's no coverage. Raises ReportAIError on config/API errors.

    Unless ``force`` is set, an existing analysis is reused indefinitely and only
    regenerated when new mentions have been added to the period (new data)."""
    if not force:
        rec = _record(org, date_from, date_to)
        if rec and not _has_new_data(org, date_from, date_to, rec.updated_at):
            return rec.payload or None  # reuse stored result (empty-dict → no coverage)

    api_keys = _groq_api_keys()
    if not api_keys:
        raise ReportAIError('AI is not configured — set GROQ_API_KEY.')

    payload = _gather(org, date_from, date_to)
    if not payload['mentions']:
        _store(org, date_from, date_to, {})  # no coverage in this period — not an error
        return None

    try:
        import groq
    except ImportError:
        raise ReportAIError('The "groq" package is not installed (pip install groq).')

    try:
        result = _normalise(_call_ai(api_keys, org, date_from, date_to, payload))
    except groq.AuthenticationError:
        raise ReportAIError('Groq authentication failed — the API key is invalid. '
                            'Check GROQ_API_KEY.')
    except groq.RateLimitError:
        raise ReportAIError('Groq rate limit reached on every configured key. '
                            'Please try again later.')
    except groq.APIStatusError as exc:
        raise _friendly_status_error(exc)
    except groq.APIConnectionError:
        raise ReportAIError('Could not reach the AI service in time (timeout or network). '
                            'Try again, or use a shorter reporting period.')
    except ReportAIError:
        raise
    except Exception:
        logger.exception("report AI analysis failed for org=%s", org.id)
        raise ReportAIError('Analysis failed to generate. Check the server logs for details.')

    _store(org, date_from, date_to, result)
    _log_analysis_event(org, date_from, date_to)
    return result


# ── Data gathering ────────────────────────────────────────────────────────────

def _gather(org, date_from, date_to):
    kw = dict(date_published__gte=date_from, date_published__lte=date_to)
    mentions = []
    per_type = max(8, MAX_MENTIONS // 4)
    # src_field is the model's outlet/platform column, surfaced so the model can
    # attribute each reputational risk/opportunity to a concrete source.
    for qs, mt, src_field in [
        (org.online_articles, 'Online', 'source'),
        (org.print_articles, 'Print', 'source'),
        (org.social_posts, 'Social', 'platform'),
        (org.broadcast_mentions, 'Broadcast', 'source'),
    ]:
        qs = filter_relevant(qs.all())
        for a in qs.filter(**kw).order_by('-ave').values('headline', 'summary', 'sentiment', src_field)[:per_type]:
            text = (a['headline'] or '').strip()
            if a['summary']:
                text = f"{text} — {a['summary'].strip()[:160]}"
            if text:
                mentions.append({'text': text[:240], 'sentiment': a['sentiment'],
                                 'media': mt, 'source': (a.get(src_field) or '').strip() or mt})

    # Merge competitor records that refer to the same brand (e.g. "ABSA Botswana"
    # and "Absa / Bank") so they aren't ranked as two separate players.
    competitors = []
    by_key = {}
    for comp in org.competitors.all():
        arts = comp.articles.all()
        entry = {
            'name': comp.name,
            'mentions': arts.count(),
            'positive': arts.filter(sentiment='positive').count(),
            'negative': arts.filter(sentiment='negative').count(),
        }
        key = _competitor_key(comp.name)
        if key and key in by_key:
            existing = by_key[key]
            # Keep the name of the better-evidenced/more complete record.
            if (entry['mentions'], len(entry['name'])) > (existing['mentions'], len(existing['name'])):
                existing['name'] = entry['name']
            existing['mentions'] += entry['mentions']
            existing['positive'] += entry['positive']
            existing['negative'] += entry['negative']
        else:
            by_key[key] = entry
            competitors.append(entry)
    competitors = competitors[:8]

    return {'mentions': mentions[:MAX_MENTIONS], 'competitors': competitors}


# ── AI call (Groq, two calls — see the 2026-08-29 module docstring note) ──────

def _call_ai(api_keys, org, date_from, date_to, payload):
    mentions_block = "\n".join(
        f"- [{m['media']}/{m['sentiment']}] ({m['source']}) {m['text']}" for m in payload['mentions']
    )
    comp_block = "\n".join(
        f"- {c['name']}: {c['mentions']} mentions ({c['positive']} positive, {c['negative']} negative)"
        for c in payload['competitors']
    ) or "(no competitors configured)"

    header = f"""Organisation: {org.name}
Reporting period: {date_from} to {date_to}

MEDIA MENTIONS (sample, with media type and sentiment):
{mentions_block}"""

    esg_prompt = f"""{header}

Return a JSON object with EXACTLY these two keys: "esg" and "stakeholders".

The "esg" array MUST contain one object for EACH of these issues, in this exact order: Financial Inclusion & Access; Fair Lending & Responsible Finance; Data Security & Customer Privacy; Business Ethics & Transparency; Customer Welfare & Product Responsibility; Employee Diversity & Wellbeing; Community Investment & Development; Environmental & Climate Impact. Each object has "issue" (the name above), "scores" (an object with keys government/regulators/customers/communities, each -100..100, 0 = not covered or neutral), and "analysis" (a short 2-3 sentence write-up of how the coverage relates to this issue).

The "stakeholders" array has one object per dimension — Customers, Employees, Investors, Regulators, Government & Politics, Community, Media — each with "dimension", "score" (0-100 favourability, 50 = neutral), "sentiment" ("positive"/"neutral"/"negative"), and "note" (one short sentence).

Output ONLY the JSON object, no markdown, no commentary."""

    comp_prompt = f"""{header}

COMPETITOR COVERAGE (for sectorial comparison):
{comp_block}

Return a JSON object with EXACTLY these four keys: "competitor", "reputational_risks", "reputational_opportunities", "kpi_insights".

"competitor" is an object: "sector_average" (-100..100), "commentary" (2-3 sentences on how {org.name} compares to the sector), and "ranked" — an array with ONE entry for {org.name} AND one for EACH competitor listed above, each with "name", "score" (-100..100, 0 = neutral), "description" (1-2 sentences), and "is_org" (true only for {org.name}).

"reputational_risks" and "reputational_opportunities": derive each from the negative (risks) and positive (opportunities) mentions respectively. Each item has a short abstracted "title" (NOT the raw headline, e.g. "Service Disruption" / "Sports Event Sponsorship"), a one-to-two sentence "description", a "score" 1-10 (severity for risks, strength for opportunities), "media" (Social/Online/Print/Broadcast), and "source" (the outlet/platform, e.g. Facebook). Return 2-4 of the most significant items per media type that has coverage; omit a media type entirely if it has none (an empty array is fine).

"kpi_insights": identify business/performance themes the coverage actually speaks to (e.g. Compliance, Customer Experience, Innovation, Community Investment). Each item has "category" (2-4 words), a detailed 2-4 sentence "text" grounded in specific mentions — name the initiatives, people, products or events involved — "score" (-100..100, 0 = neutral/balanced), "mentions" (how many relate to the theme), and "media". Return 2-5 substantive insights per media type that has relevant coverage; omit a media type with none (an empty array is fine).

Output ONLY the JSON object, no markdown, no commentary."""

    # Asymmetric on purpose: comp_prompt's four sections (competitor ranking +
    # risks + opportunities + kpi_insights, each a variable-length list) measured
    # meaningfully heavier than esg_prompt's two fixed-count sections (8 ESG
    # issues + 7 stakeholders) at the same mention count — see MAX_MENTIONS' note.
    esg_data = _groq_json_call(api_keys, SYSTEM_PROMPT, esg_prompt, max_tokens=3500)
    comp_data = _groq_json_call(api_keys, SYSTEM_PROMPT, comp_prompt, max_tokens=5500)
    return {**esg_data, **comp_data}


# A rolling-TPM 429's retry-after is normally well under a second to a few
# seconds (measured against the real API) and clears on its own shortly —
# worth one inline retry on the same key. But it's not always tiny: a big
# request landing right at the edge of the window has measured as high as
# ~6s (surfaced 2026-09-01 — an initial 5.0s cutoff here rejected a genuine
# 5.97s TPM wait as if it were a daily-cap wait, failing a request that a
# few more seconds would have carried through fine). A daily-cap (TPD) 429
# instead reports retry-after in the TENS OF MINUTES (1300s+, observed
# 1300-2600s the same day) and won't clear for hours: sleeping that out
# blocks the request thread past gunicorn's --timeout, which kills the
# worker with no response ever reaching the client at all. 20s sits with
# huge margin on both sides of that TPM/TPD gap — generous enough not to
# reject a real TPM wait, nowhere near long enough to risk sleeping out a
# TPD one. Anything longer than this is treated as "this key is out for
# today" and falls through to the next key immediately instead.
_MAX_SHORT_RETRY_SECONDS = 20.0


def _short_retry_after(exc):
    """Seconds to wait inline, or None if this 429's retry-after is too long
    to be worth waiting out on the same key (see the constant above)."""
    try:
        wait = float(exc.response.headers.get('retry-after', 0))
    except (AttributeError, TypeError, ValueError):
        return None
    return wait + 0.5 if 0 < wait <= _MAX_SHORT_RETRY_SECONDS else None


def _groq_json_call(api_keys, system_prompt, user_prompt, max_tokens):
    """One structured-JSON call, trying each configured Groq key in turn
    (see _groq_api_keys) — a key's daily token cap doesn't free up mid-day,
    so once one key is exhausted this moves on rather than blocking the
    request thread waiting for it (see _MAX_SHORT_RETRY_SECONDS above).
    Only RateLimitError triggers a retry/key-fallback; every other failure
    (auth, connection, status) propagates immediately to the caller's own
    handling, since trying six more keys wouldn't fix a bad key or a
    malformed response.

    Shared with issue_report_ai.py's own _call_ai — hence system_prompt is a
    parameter, not this module's own SYSTEM_PROMPT global."""
    import groq  # lazy import so the app runs without the SDK installed

    last_exc = None
    for i, api_key in enumerate(api_keys):
        is_last_key = i == len(api_keys) - 1
        # Bound each call well under gunicorn's --timeout so a slow response
        # fails cleanly (ReportAIError) instead of getting the worker killed
        # mid-request.
        client = groq.Groq(api_key=api_key, timeout=60.0, max_retries=1)
        retried_after_wait = False
        while True:
            try:
                completion = client.chat.completions.create(
                    model=MODEL,
                    # gpt-oss models spend hidden reasoning tokens before the final
                    # JSON, counted against max_tokens (same behaviour as
                    # sentiment_ai.py) — this is headroom, not a target.
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                )
            except groq.RateLimitError as exc:
                wait = None if retried_after_wait else _short_retry_after(exc)
                if wait is not None:
                    retried_after_wait = True
                    logger.info("report_ai: Groq TPM limit hit, waiting %.1fs (key #%d/%d)",
                                wait, i + 1, len(api_keys))
                    time.sleep(wait)
                    continue
                logger.warning(
                    "report_ai: Groq rate limit on key #%d/%d%s: %s",
                    i + 1, len(api_keys),
                    '' if is_last_key else ' — moving to next key', exc)
                last_exc = exc
                break  # give up on this key — move to the next one (or raise below)

            choice = completion.choices[0]
            if choice.finish_reason == 'length':
                raise ReportAIError('The AI response was truncated. Try a shorter reporting period.')
            return _parse_json(choice.message.content)

    raise last_exc


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    # Strip JSON-invalid extras the model occasionally emits.
    text = re.sub(r'(?m)^\s*//.*$', '', text)          # whole-line // comments
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)  # block comments
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model response")
    candidate = text[start:end + 1]
    candidate = re.sub(r',(\s*[}\]])', r'\1', candidate)  # trailing commas
    return json.loads(candidate)


# ── Normalisation (defensive — never trust the model's shape blindly) ─────────

def _as_list(value):
    """Coerce a field that's supposed to be an array but, without Anthropic's
    schema-enforced structured outputs to constrain Groq's plain json_object
    mode, sometimes isn't — surfaced 2026-08-29: the model returned
    reputational_risks grouped as {"Online": [...], "Social": [...]} instead
    of one flat array, crashing a bare `(rows or [])[:16]` with a KeyError
    (slicing a dict). A dict's values are flattened (each expected to itself
    be a list of row-dicts); anything else that isn't already a list becomes
    empty rather than raising."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        flat = []
        for v in value.values():
            flat.extend(v if isinstance(v, list) else [v])
        return flat
    return []


def _normalise(data):
    out = {'esg': [], 'stakeholders': [], 'competitor': None,
           'reputational_risks': [], 'reputational_opportunities': [], 'kpi_insights': []}

    for row in _as_list(data.get('esg'))[:12]:
        scores = row.get('scores') or {}
        cells = []
        for skey, _label in _STAKEHOLDERS:
            v = _sent(scores.get(skey))
            cells.append({
                'value': v,
                'tone': 'positive' if v > 0 else 'negative' if v < 0 else 'neutral',
            })
        out['esg'].append({
            'issue': str(row.get('issue') or row.get('category') or '')[:80],
            'cells': cells,
            'analysis': str(row.get('analysis', ''))[:700],
        })
    # Paginate into pages of ESG_PER_PAGE issues (deck shows ~2 per page).
    out['esg_pages'] = [out['esg'][i:i + ESG_PER_PAGE] for i in range(0, len(out['esg']), ESG_PER_PAGE)]

    for row in _as_list(data.get('stakeholders'))[:10]:
        out['stakeholders'].append({
            'dimension': str(row.get('dimension', ''))[:40],
            'score': _clamp(row.get('score')),
            'sentiment': row.get('sentiment') if row.get('sentiment') in ('positive', 'neutral', 'negative') else 'neutral',
            'note': str(row.get('note', ''))[:200],
        })

    comp = data.get('competitor') or {}
    if comp:
        ranked = []
        seen_keys = {}
        for lv in _as_list(comp.get('ranked') or comp.get('levels'))[:12]:
            name = str(lv.get('name') or lv.get('label') or '')[:60]
            score = _sent(lv.get('score') if lv.get('score') is not None else lv.get('value'))
            # Collapse any same-brand duplicates the model may still emit, keeping
            # the higher-scored / better-described entry.
            key = _competitor_key(name)
            if key and key in seen_keys:
                prev = ranked[seen_keys[key]]
                if score > prev['score']:
                    prev['score'] = score
                    prev['tone'] = 'positive' if score > 0 else 'negative' if score < 0 else 'neutral'
                if len(str(lv.get('description', ''))) > len(prev['description']):
                    prev['description'] = str(lv.get('description', ''))[:400]
                prev['is_org'] = prev['is_org'] or bool(lv.get('is_org'))
                continue
            seen_keys[key] = len(ranked)
            ranked.append({
                'name': name,
                'score': score,
                'description': str(lv.get('description', ''))[:400],
                'is_org': bool(lv.get('is_org')),
                'tone': 'positive' if score > 0 else 'negative' if score < 0 else 'neutral',
            })
        ranked.sort(key=lambda x: x['score'], reverse=True)
        for i, item in enumerate(ranked, 1):
            item['rank'] = _ordinal_label(i)

        avg = _sent(comp.get('sector_average'))
        org_item = next((x for x in ranked if x['is_org']), None)
        org_score = org_item['score'] if org_item else _sent(comp.get('org_score'))
        out['competitor'] = {
            'org_score': org_score,
            'org_pct': round((org_score + 100) / 2),     # position on a 0–100% bar
            'sector_average': avg,
            'avg_pct': round((avg + 100) / 2),
            'commentary': str(comp.get('commentary', ''))[:600],
            'ranked': ranked,
        }

    out['reputational_risks'] = _norm_reputational(data.get('reputational_risks'))
    out['reputational_opportunities'] = _norm_reputational(data.get('reputational_opportunities'))
    out['kpi_insights'] = _norm_kpi_insights(data.get('kpi_insights'))

    # stakeholder radar chart config (consumed by the report's lazy chart builder)
    if out['stakeholders']:
        out['stakeholder_chart'] = json.dumps({
            'type': 'radar',
            'data': {
                'labels': [s['dimension'] for s in out['stakeholders']],
                'datasets': [{
                    'label': 'Favourability',
                    'data': [s['score'] for s in out['stakeholders']],
                    'backgroundColor': 'rgba(0,169,157,.18)',
                    'borderColor': '#00A99D',
                    'borderWidth': 2,
                    'pointBackgroundColor': '#00A99D',
                }],
            },
            'options': {
                'responsive': True, 'maintainAspectRatio': False,
                'scales': {'r': {'min': 0, 'max': 100, 'ticks': {'stepSize': 20, 'font': {'size': 9}}}},
                'plugins': {'legend': {'display': False}},
            },
        })

    if not (out['esg'] or out['stakeholders'] or out['competitor']
            or out['reputational_risks'] or out['reputational_opportunities']
            or out['kpi_insights']):
        return {}
    return out


# Generic words dropped when keying a competitor, so brand variants collapse
# ("ABSA Botswana" / "Absa / Bank" → "absa") while distinct brands stay separate.
_GENERIC_COMP_TOKENS = {'bank', 'botswana', 'limited', 'ltd', 'plc', 'the',
                        'group', 'holdings', 'co', 'company', 'inc'}


def _competitor_key(name):
    """A normalised brand key for de-duplicating competitor records."""
    tokens = re.findall(r'[a-z0-9]+', (name or '').lower())
    core = [t for t in tokens if t not in _GENERIC_COMP_TOKENS]
    return ''.join(core) or ''.join(tokens)


def _media_key(value):
    """Map a model-supplied media label to the report's section key. Tolerant of
    variants like "Social Media", "Online Articles", "Broadcast Media" so items
    aren't silently dropped (and lost from their section) on a labelling mismatch."""
    s = str(value or '').strip().lower()
    for key in ('social', 'online', 'print', 'broadcast'):
        if key in s:
            return key
    return ''


def _norm_reputational(rows):
    """Normalise a reputational risks/opportunities array. Each item carries a
    media_key so the view can drop it into the matching media-type section."""
    out = []
    for row in _as_list(rows)[:16]:
        title = str(row.get('title') or row.get('issue') or '').strip()[:60]
        if not title:
            continue
        out.append({
            'title': title,
            'description': str(row.get('description') or row.get('analysis') or '').strip()[:300],
            'score': _score10(row.get('score')),
            'media_key': _media_key(row.get('media')),
            'source': str(row.get('source') or '').strip()[:40],
        })
    return out


def _norm_kpi_insights(rows):
    """Normalise detailed KPI insights. Each carries a -100..100 sentiment score
    (with a display label and sentiment band) and a media_key for its section."""
    out = []
    for row in _as_list(rows)[:24]:
        category = str(row.get('category') or row.get('theme') or '').strip()[:50]
        text = str(row.get('text') or row.get('analysis') or '').strip()[:600]
        if not category or not text:
            continue
        score = _sent(row.get('score'))
        out.append({
            'category': category,
            'text': text,
            'score': score,
            'score_label': f'+{score}' if score > 0 else str(score),
            'sentiment': 'positive' if score > 0 else 'negative' if score < 0 else 'neutral',
            'mentions': _int(row.get('mentions')),
            'media_key': _media_key(row.get('media')),
        })
    return out


def _int(v):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _clamp(v):
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return 50


def _score10(v):
    """Clamp to a 1..10 magnitude score (used for risk severity / opportunity strength)."""
    try:
        return max(1, min(10, int(round(abs(float(v))))))
    except (TypeError, ValueError):
        return 5


def _sent(v):
    """Clamp to a -100..100 sentiment-favourability score (0 = neutral)."""
    try:
        return max(-100, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return 0


def _ordinal_label(n):
    if 10 <= n % 100 <= 20:
        suffix = 'TH'
    else:
        suffix = {1: 'ST', 2: 'ND', 3: 'RD'}.get(n % 10, 'TH')
    return f"{n}{suffix}"
