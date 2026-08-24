"""AI-generated report analysis: ESG, Stakeholder, Sectorial Competitor, and
Reputational Risks / Opportunities.

A single Groq call (openai/gpt-oss-120b) per (organisation, period)
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
catalog at all (calls were failing outright). A same-day attempt to move to
Groq's openai/gpt-oss-120b (matching the fallback media-monitor's discovery/
services/query_planner.py made for the same reason) hit a harder wall: that
model's rate limit on this account is 8,000 TPM combined input+output — verified
against the real API — and the fixed prompt overhead (system prompt + the full
JSON-schema instructions) plus a JSON output big enough to avoid truncation left
room for only ~7 sample mentions, too thin to be a real analysis. Moved to
Anthropic instead (this is also what README.md/RUNBOOK.md always documented —
Groq was an undocumented substitution at some point). Uses Messages API
structured outputs (``output_config.format``, a JSON schema) rather than a
"return JSON" prompt instruction — the response is guaranteed to match the
schema, not just be syntactically valid JSON.
"""
import json
import logging
import re

from django.conf import settings
from django.urls import reverse

from .relevancy import filter_relevant

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'REPORT_AI_MODEL', 'claude-opus-5')
MAX_MENTIONS = 60  # cap the prompt size / token cost

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

    # Trim stray whitespace/quotes that often sneak in from .env files.
    api_key = (getattr(settings, 'ANTHROPIC_API_KEY', '') or '').strip().strip('"').strip("'")
    if not api_key:
        raise ReportAIError('AI is not configured — set ANTHROPIC_API_KEY.')

    payload = _gather(org, date_from, date_to)
    if not payload['mentions']:
        _store(org, date_from, date_to, {})  # no coverage in this period — not an error
        return None

    try:
        import anthropic
    except ImportError:
        raise ReportAIError('The "anthropic" package is not installed (pip install anthropic).')

    try:
        result = _normalise(_call_ai(api_key, org, date_from, date_to, payload))
    except anthropic.AuthenticationError:
        raise ReportAIError('Anthropic authentication failed — the API key is invalid. '
                            'Check ANTHROPIC_API_KEY.')
    except anthropic.RateLimitError:
        raise ReportAIError('Anthropic rate limit reached. Please try again shortly.')
    except anthropic.APIStatusError as exc:
        raise ReportAIError(f'Anthropic API error (HTTP {exc.status_code}). Please try again.')
    except anthropic.APIConnectionError:
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


# ── JSON schema for the structured-output call below ──────────────────────────
# Mirrors the shape the prompt asks for. Kept loose on exact array lengths
# (minItems only where the count is fixed by the ESG issue list) — _normalise()
# below is still the defensive layer that clamps/coerces values, on the
# principle of never trusting a model's shape blindly even under a schema.

_SCORE_SCHEMA = {"type": "number", "minimum": -100, "maximum": 100}


def _risk_opportunity_schema():
    """reputational_risks and reputational_opportunities share this shape —
    a factory (not a shared dict) so nothing downstream can mutate one and
    silently affect the other."""
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "score": {"type": "integer", "minimum": 1, "maximum": 10},
            "media": {"type": "string", "enum": ["Social", "Online", "Print", "Broadcast"]},
            "source": {"type": "string"},
        },
        "required": ["title", "description", "score", "media", "source"],
        "additionalProperties": False,
    }


REPORT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "esg": {
            "type": "array",
            "minItems": len(_ESG_ISSUES), "maxItems": len(_ESG_ISSUES),
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "properties": {k: _SCORE_SCHEMA for k, _label in _STAKEHOLDERS},
                        "required": [k for k, _label in _STAKEHOLDERS],
                        "additionalProperties": False,
                    },
                    "analysis": {"type": "string"},
                },
                "required": ["issue", "scores", "analysis"],
                "additionalProperties": False,
            },
        },
        "stakeholders": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string"},
                    "score": {"type": "number", "minimum": 0, "maximum": 100},
                    "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                    "note": {"type": "string"},
                },
                "required": ["dimension", "score", "sentiment", "note"],
                "additionalProperties": False,
            },
        },
        "competitor": {
            "type": "object",
            "properties": {
                "sector_average": _SCORE_SCHEMA,
                "commentary": {"type": "string"},
                "ranked": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "score": _SCORE_SCHEMA,
                            "description": {"type": "string"},
                            "is_org": {"type": "boolean"},
                        },
                        "required": ["name", "score", "description", "is_org"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["sector_average", "commentary", "ranked"],
            "additionalProperties": False,
        },
        "reputational_risks": {"type": "array", "items": _risk_opportunity_schema()},
        "reputational_opportunities": {"type": "array", "items": _risk_opportunity_schema()},
        "kpi_insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "score": _SCORE_SCHEMA,
                    "mentions": {"type": "integer", "minimum": 0},
                    "media": {"type": "string", "enum": ["Social", "Online", "Print", "Broadcast"]},
                    "text": {"type": "string"},
                },
                "required": ["category", "score", "mentions", "media", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["esg", "stakeholders", "competitor", "reputational_risks",
                 "reputational_opportunities", "kpi_insights"],
    "additionalProperties": False,
}


# ── AI call (Anthropic Messages API, structured outputs) ──────────────────────

def _call_ai(api_key, org, date_from, date_to, payload):
    import anthropic  # lazy import so the app runs without the SDK installed

    # Bound the call well under gunicorn's --timeout so a slow response fails
    # cleanly (ReportAIError) instead of getting the worker killed mid-request.
    client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=1)

    mentions_block = "\n".join(
        f"- [{m['media']}/{m['sentiment']}] ({m['source']}) {m['text']}" for m in payload['mentions']
    )
    comp_block = "\n".join(
        f"- {c['name']}: {c['mentions']} mentions ({c['positive']} positive, {c['negative']} negative)"
        for c in payload['competitors']
    ) or "(no competitors configured)"

    user_prompt = f"""Organisation: {org.name}
Reporting period: {date_from} to {date_to}

MEDIA MENTIONS (sample, with media type and sentiment):
{mentions_block}

COMPETITOR COVERAGE (for sectorial comparison):
{comp_block}

The response schema is enforced separately (see REPORT_JSON_SCHEMA) — this is
guidance on WHAT to write in each field, not the structure itself:

The "esg" array MUST contain one object for EACH of these issues, in this exact order: Financial Inclusion & Access; Fair Lending & Responsible Finance; Data Security & Customer Privacy; Business Ethics & Transparency; Customer Welfare & Product Responsibility; Employee Diversity & Wellbeing; Community Investment & Development; Environmental & Climate Impact. For each issue, score the sentiment from each stakeholder's perspective on a -100..100 scale (0 = not covered or neutral) based on the mentions, and write a short 2-3 sentence analysis. Stakeholder "score" is a 0–100 favourability index (50 = neutral); write one short sentence per "note". Include {org.name} AND each competitor above in "competitor.ranked", each with its own 1-2 sentence description and a -100..100 score (0 = neutral); "commentary" is 2-3 sentences on how {org.name} compares to the sector.

For "reputational_risks" and "reputational_opportunities": derive each item from the negative (risks) and positive (opportunities) mentions respectively. Give a short, abstracted issue title (NOT the raw headline, e.g. "Service Disruption" / "Sports Event Sponsorship"), a clear one to two sentence description, a 1-10 score (severity for risks, strength for opportunities), and set "media" to the mention's media type and "source" to its outlet/platform (e.g. Facebook). Return 2-4 of the most significant items per media type that has coverage; omit a media type entirely if it has no relevant coverage (an empty array is fine).

For "kpi_insights": identify the business/performance themes that the coverage actually speaks to (e.g. Compliance, Customer Experience, Innovation, Community Investment) — a short 2-4 word category name — and, for each, write a detailed 2-4 sentence narrative grounded in the specific mentions — name the initiatives, people, products or events involved and explain the implication. Set "score" to the theme's sentiment on a -100..100 scale (0 = neutral/balanced), "mentions" to how many mentions relate to the theme (its visibility), and "media" to the media type the insight is drawn from. Return 2-5 substantive insights per media type that has relevant coverage; omit a media type with no relevant coverage (an empty array is fine)."""

    response = client.messages.create(
        model=MODEL,
        # Anthropic's rate limits are far more generous than the on-demand-tier
        # Groq ceiling this used to be squeezed under (see the 2026-08-24 module
        # docstring note) — no need to trim mentions or output room to fit.
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={"format": {"type": "json_schema", "schema": REPORT_JSON_SCHEMA}},
    )
    if response.stop_reason == 'max_tokens':
        raise ReportAIError('The AI response was truncated. Try a shorter reporting period.')
    text = next(b.text for b in response.content if b.type == "text")
    return _parse_json(text)


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

def _normalise(data):
    out = {'esg': [], 'stakeholders': [], 'competitor': None,
           'reputational_risks': [], 'reputational_opportunities': [], 'kpi_insights': []}

    for row in (data.get('esg') or [])[:12]:
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

    for row in (data.get('stakeholders') or [])[:10]:
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
        for lv in (comp.get('ranked') or comp.get('levels') or [])[:12]:
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
    for row in (rows or [])[:16]:
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
    for row in (rows or [])[:24]:
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
