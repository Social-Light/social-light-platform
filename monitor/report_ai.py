"""AI-generated report analysis: ESG, Stakeholder, and Sectorial Competitor.

A single Anthropic call per (organisation, period) produces the structured data
for three report sections that the raw models can't supply on their own. The
result is persisted in the ReportAnalysis model and reused for a week (FRESH),
so it survives restarts and the report never hits the API on a normal page view.

Generation is on demand (the report's "Generate AI Analysis" button); page loads
call ``get_cached_analysis`` only. If the SDK/API key is missing or a generation
fails, the helpers return None / raise ReportAIError and the report simply omits
the AI sections instead of erroring.
"""
import json
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

FRESH = timedelta(days=7)  # reuse a generated analysis for a week before re-prompting
MODEL = getattr(settings, 'REPORT_AI_MODEL', 'claude-sonnet-4-6')
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


def get_cached_analysis(org, date_from, date_to):
    """Return a previously-generated analysis dict if one exists and is still fresh
    (< 1 week old), else None. Never calls the API — safe on every page load."""
    rec = _record(org, date_from, date_to)
    if not rec or (timezone.now() - rec.updated_at) > FRESH:
        return None
    return rec.payload or None  # empty-dict sentinel → None


def get_analysis_generated_at(org, date_from, date_to):
    """Timestamp of the current fresh, non-empty analysis, or None."""
    rec = _record(org, date_from, date_to)
    if rec and rec.payload and (timezone.now() - rec.updated_at) <= FRESH:
        return rec.updated_at
    return None


def generate_analysis(org, date_from, date_to, force=False):
    """Generate (and persist) the analysis via the Anthropic API. Returns the dict,
    or None when there's no coverage. Raises ReportAIError on config/API errors."""
    if not force:
        fresh = get_cached_analysis(org, date_from, date_to)
        if fresh is not None:
            return fresh

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
        result = _normalise(_call_anthropic(api_key, org, date_from, date_to, payload))
    except anthropic.AuthenticationError:
        raise ReportAIError('Anthropic authentication failed — the API key is invalid. '
                            'Check ANTHROPIC_API_KEY.')
    except anthropic.RateLimitError:
        raise ReportAIError('Anthropic rate limit reached. Please try again shortly.')
    except anthropic.APIStatusError as exc:
        raise ReportAIError(f'Anthropic API error (HTTP {exc.status_code}). Please try again.')
    except ReportAIError:
        raise
    except Exception:
        logger.exception("report AI analysis failed for org=%s", org.id)
        raise ReportAIError('Analysis failed to generate. Check the server logs for details.')

    _store(org, date_from, date_to, result)
    return result


# ── Data gathering ────────────────────────────────────────────────────────────

def _gather(org, date_from, date_to):
    kw = dict(date_published__gte=date_from, date_published__lte=date_to)
    mentions = []
    per_type = max(8, MAX_MENTIONS // 4)
    for qs, mt in [
        (org.online_articles, 'Online'),
        (org.print_articles, 'Print'),
        (org.social_posts, 'Social'),
        (org.broadcast_mentions, 'Broadcast'),
    ]:
        for a in qs.filter(**kw).order_by('-ave').values('headline', 'summary', 'sentiment')[:per_type]:
            text = (a['headline'] or '').strip()
            if a['summary']:
                text = f"{text} — {a['summary'].strip()[:160]}"
            if text:
                mentions.append({'text': text[:240], 'sentiment': a['sentiment'], 'media': mt})

    competitors = []
    for comp in org.competitors.all()[:8]:
        arts = comp.articles.all()
        total = arts.count()
        competitors.append({
            'name': comp.name,
            'mentions': total,
            'positive': arts.filter(sentiment='positive').count(),
            'negative': arts.filter(sentiment='negative').count(),
        })

    return {'mentions': mentions[:MAX_MENTIONS], 'competitors': competitors}


# ── Anthropic call ────────────────────────────────────────────────────────────

def _call_anthropic(api_key, org, date_from, date_to, payload):
    import anthropic  # lazy import so the app runs without the SDK installed

    client = anthropic.Anthropic(api_key=api_key)

    mentions_block = "\n".join(
        f"- [{m['media']}/{m['sentiment']}] {m['text']}" for m in payload['mentions']
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

Produce a JSON object with EXACTLY these keys:

{{
  "esg": [
    {{"issue": "Financial Inclusion & Access",
      "scores": {{"government": <-100 to 100>, "regulators": <-100 to 100>, "customers": <-100 to 100>, "communities": <-100 to 100>}},
      "analysis": "<2-3 sentence analysis of how the coverage relates to this issue>"}}
  ],
  "stakeholders": [
    {{"dimension": "Customers", "score": <0-100>, "sentiment": "positive|neutral|negative", "note": "<one short sentence>"}},
    {{"dimension": "Employees", "score": <0-100>, "sentiment": "...", "note": "..."}},
    {{"dimension": "Investors", "score": <0-100>, "sentiment": "...", "note": "..."}},
    {{"dimension": "Regulators", "score": <0-100>, "sentiment": "...", "note": "..."}},
    {{"dimension": "Community", "score": <0-100>, "sentiment": "...", "note": "..."}},
    {{"dimension": "Media", "score": <0-100>, "sentiment": "...", "note": "..."}}
  ],
  "competitor": {{
    "sector_average": <-100 to 100>,
    "commentary": "<2-3 sentences on how {org.name} compares to the sector>",
    "ranked": [
      {{"name": "{org.name}", "score": <-100 to 100>, "description": "<1-2 sentence assessment of this player's coverage>", "is_org": true}},
      {{"name": "<competitor>", "score": <-100 to 100>, "description": "<1-2 sentence assessment>", "is_org": false}}
    ]
  }}
}}

The "esg" array MUST contain one object for EACH of these issues, in this exact order: Financial Inclusion & Access; Fair Lending & Responsible Finance; Data Security & Customer Privacy; Business Ethics & Transparency; Customer Welfare & Product Responsibility; Employee Diversity & Wellbeing; Community Investment & Development; Environmental & Climate Impact. For each issue, score the sentiment from each stakeholder's perspective on a -100..100 scale (0 = not covered or neutral) based on the mentions, and write a short analysis. Include {org.name} AND each competitor above in "ranked", each with its own description and a score. Stakeholder "score" is a 0–100 favourability index (50 = neutral). Competitor and ESG "score" values are a -100..100 sentiment index (0 = neutral). Do NOT include comments or any text outside the JSON. Return ONLY the JSON object."""

    msg = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if getattr(msg, 'stop_reason', None) == 'max_tokens':
        raise ReportAIError('The AI response was truncated. Try a shorter reporting period.')
    text = "".join(block.text for block in msg.content if getattr(block, 'type', '') == 'text')
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
    out = {'esg': [], 'stakeholders': [], 'competitor': None}

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
        for lv in (comp.get('ranked') or comp.get('levels') or [])[:8]:
            score = _sent(lv.get('score') if lv.get('score') is not None else lv.get('value'))
            ranked.append({
                'name': str(lv.get('name') or lv.get('label') or '')[:60],
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

    if not (out['esg'] or out['stakeholders'] or out['competitor']):
        return {}
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
