"""Issue-focused ("saga") report generation.

From every mention in a period, a single Groq call (openai/gpt-oss-120b — see
report_ai.py's module docstring for the full story of why Groq, and its
MODEL/_groq_json_call, which this module imports and reuses) selects only
those relevant to a named issue (the saga) and writes the issue-specific
narrative: executive summary, "at a glance" facts, a case timeline,
narrative/framing analysis, reputational risks, stakeholder impact and
recommendations. Modelled on the special-edition deck (P8 Billion Fund Dispute).

Unlike report_ai.py this stays ONE call, not two: "selected" (which candidate
refs are actually on-topic) is foundational to every other field here — a
timeline event, a risk, a stakeholder note all only make sense grounded in the
same selected set, so splitting would mean either re-deriving "selected"
twice (risking the two calls disagreeing) or a real sequential dependency
between them. Instead this fits Groq's rate limit the same way report_ai.py's
per-call budget does: MAX_CANDIDATES trimmed to what actually fits — see its
own comment for the measurement.

Anti-divergence contract — the model NEVER produces coverage, it only:
  1. selects from real rows, returning their stable refs (e.g. "online-12"); and
  2. writes commentary about them.
Every headline / AVE / reach / date shown in the report is read back from the DB
row by that ref. Any returned ref not in the candidate set is discarded, and any
timeline event without a valid source ref is dropped. The executive-summary
figures are computed from the selected rows at render time, never taken from the
model (see views.report_issue).

Candidates are pre-filtered by the saga's keywords (keeping the token cost bounded
regardless of how busy the period was), through the same ``filter_relevant`` gate
the rest of the app uses, then the AI refines that set to the truly on-topic
mentions. The selection is stored editable on IssueReport so a human can add back
an excluded candidate or drop a wrong include and regenerate.
"""
import logging
import re

from django.conf import settings
from django.db.models import Q
from django.urls import reverse

from .relevancy import filter_relevant
from .report_ai import (
    ReportAIError, _sent, MODEL, _friendly_status_error, _groq_json_call, _as_list,
    _groq_api_keys,
)

logger = logging.getLogger(__name__)

# 2026-08-29: was 100. Same real constraint as report_ai.py's MAX_MENTIONS —
# gpt-oss-120b's hidden reasoning cost scales with how many candidates it has
# to read and select from, not with how much JSON it ultimately emits, and
# this schema (8 sections, several variable-length) is comparably heavy to
# report_ai.py's. Not empirically re-measured row-by-row the way MAX_MENTIONS
# was — reduced by the same proportion (100 -> 25) as a starting point; adjust
# from real generate_issue_report() runs if it still truncates or turns out to
# have real headroom to spare.
MAX_CANDIDATES = 25         # hard cap on rows sent to the model / token cost
PER_TYPE = MAX_CANDIDATES // 4

# Media types, in report order, with the model's outlet/platform column.
_MEDIA = [
    ('online', 'Online', 'source'),
    ('print', 'Print', 'source'),
    ('social', 'Social', 'platform'),
    ('broadcast', 'Broadcast', 'source'),
]

SYSTEM_PROMPT = (
    "You are a senior media-intelligence analyst producing a special, "
    "issue-focused edition. You are given a set of real media mentions, each with "
    "a stable reference id, and a specific issue (a 'saga'). You select ONLY the "
    "mentions that are genuinely about that issue and write a concise, structured "
    "analysis of them. Never invent mentions, outlets, figures or events: every "
    "claim must derive from the supplied mentions. Respond with ONLY a single JSON "
    "object — no markdown, no prose, no code fences."
)

# Short, low-value tokens dropped when deriving keyword filters from the saga text.
_STOPWORDS = {
    'the', 'and', 'for', 'with', 'that', 'this', 'from', 'about', 'into', 'over',
    'against', 'between', 'issue', 'saga', 'story', 'case', 'report', 'coverage',
    'media', 'news', 'claim', 'claims', 'alleged', 'dispute', 'all', 'any', 'related',
}
# Generic banking words that, like the org's own brand, don't discriminate one
# issue from another within a bank's coverage.
_GENERIC_BRAND = {'bank', 'banks', 'banking', 'group', 'holdings', 'limited',
                  'ltd', 'plc', 'company', 'co', 'financial'}

# How many matched rows to pull per type before ranking by relevance in Python.
_FETCH_CAP = 400


def _org_stopwords(org):
    """Tokens that identify the org itself and so match (almost) all of its
    coverage — its name words, common acronyms, and generic bank words. Stripping
    them leaves only the *distinctive* saga terms to filter on. Without this, an
    org routed by 'FNB' matches every record on the token 'fnb'."""
    words = [w for w in re.findall(r"[a-z]+", (org.name or '').lower())
             if w not in {'of', 'the', 'and'}]
    stops = set(words) | _GENERIC_BRAND
    if len(words) >= 2:
        stops.add(''.join(w[0] for w in words))       # full acronym, e.g. fnbb
        stops.add(''.join(w[0] for w in words[:-1]))  # drop trailing geo, e.g. fnb
    return stops


def _saga_terms(issue_query, org=None):
    """Distinctive keyword terms for the candidate pre-filter: meaningful tokens
    from the saga text (length >= 3 or containing a digit), with stopwords and the
    org's own (non-discriminating) brand tokens removed, de-duplicated."""
    org_stops = _org_stopwords(org) if org is not None else set()
    terms = []
    seen = set()
    for tok in re.findall(r"[a-z0-9]+", (issue_query or '').lower()):
        if tok in _STOPWORDS or tok in org_stops or tok in seen:
            continue
        if len(tok) >= 3 or any(c.isdigit() for c in tok):
            seen.add(tok)
            terms.append(tok)
    return terms


def _gather_candidates(org, date_from, date_to, issue_query):
    """Keyword-prefilter the period's relevant mentions into a bounded candidate
    set, ranked by how many distinct saga terms each mention hits (most on-topic
    first) — NOT by AVE, which would surface high-value off-topic noise. Returns
    ``(candidates, candidate_ids)``."""
    terms = _saga_terms(issue_query, org=org)
    kw = dict(date_published__gte=date_from, date_published__lte=date_to)
    candidates = []
    candidate_ids = {key: [] for key, _, _ in _MEDIA}

    rels = {'online': org.online_articles, 'print': org.print_articles,
            'social': org.social_posts, 'broadcast': org.broadcast_mentions}

    for key, label, src_field in _MEDIA:
        qs = filter_relevant(rels[key].filter(**kw))
        if not terms:
            continue  # nothing distinctive to match on → no candidates for this type
        term_q = Q()
        for t in terms:
            term_q |= Q(headline__icontains=t) | Q(summary__icontains=t)
        rows = qs.filter(term_q).order_by('-date_published').values(
            'id', 'headline', 'summary', 'sentiment', src_field, 'date_published')[:_FETCH_CAP]

        scored = []
        for r in rows:
            headline = (r['headline'] or '').strip()
            if not headline:
                continue
            blob = f"{headline} {r['summary'] or ''}".lower()
            score = sum(1 for t in terms if t in blob)  # distinct saga terms hit
            scored.append((score, r, headline))
        # Most on-topic first; recency (fetch order) breaks ties.
        scored.sort(key=lambda x: x[0], reverse=True)

        for score, r, headline in scored[:PER_TYPE]:
            text = headline
            if r['summary']:
                text = f"{text} — {r['summary'].strip()[:180]}"
            candidate_ids[key].append(r['id'])
            candidates.append({
                'ref': f"{key}-{r['id']}",
                'media': label,
                'source': (r.get(src_field) or '').strip() or label,
                'date': str(r['date_published']),
                'sentiment': r['sentiment'],
                'text': text[:260],
            })
    return candidates[:MAX_CANDIDATES], candidate_ids


# ── Generation ────────────────────────────────────────────────────────────────

def generate_issue_report(org, title, issue_query, date_from, date_to, created_by=None):
    """Create and persist an IssueReport for the saga. Raises ReportAIError on
    config/API errors or when no coverage matches the issue."""
    from .models import IssueReport

    candidates, candidate_ids = _gather_candidates(org, date_from, date_to, issue_query)
    if not candidates:
        raise ReportAIError(
            'No coverage matched this issue in the selected period. Try broadening '
            'the description or widening the date range.')

    result = _run(org, title, issue_query, date_from, date_to, candidates)
    selected_ids, payload = result

    report = IssueReport.objects.create(
        organization=org,
        title=title.strip() or issue_query.strip()[:120],
        issue_query=issue_query.strip(),
        date_from=date_from,
        date_to=date_to,
        candidate_ids=candidate_ids,
        selected_ids=selected_ids,
        payload=payload,
        created_by=created_by,
    )
    _log_report_event(report)
    return report


def _log_report_event(report):
    """Record an Event so alert digests notify recipients about a newly created
    issue/campaign report. Not called on regenerate_narrative — only a brand-new
    report counts as something worth a fresh notification."""
    from .models import Event
    base = (getattr(settings, 'SITE_URL', 'https://sociallight.africa') or '').rstrip('/')
    Event.objects.create(
        organization=report.organization, category='report', event_type='issue_report_created',
        title=f'New {report.type_label} report: {report.title}',
        summary=report.issue_query[:200],
        url=f"{base}{reverse('monitor:report_issue', args=[report.organization.id, report.id])}",
    )


def regenerate_narrative(report):
    """Regenerate the narrative for an existing IssueReport, honouring the current
    (possibly hand-edited) ``selected_ids`` — the AI re-selects only within that
    set, so a human's include/exclude edits are respected. Updates in place."""
    candidates, _ = _gather_candidates(
        report.organization, report.date_from, report.date_to, report.issue_query)
    # Restrict the candidate pool to what the human kept selected, if any.
    kept = {f"{mt}-{i}" for mt, ids in (report.selected_ids or {}).items() for i in ids}
    pool = [c for c in candidates if c['ref'] in kept] or candidates
    if not pool:
        raise ReportAIError('This report has no selected mentions to analyse.')

    selected_ids, payload = _run(
        report.organization, report.title, report.issue_query,
        report.date_from, report.date_to, pool)
    report.selected_ids = selected_ids
    report.payload = payload
    report.save(update_fields=['selected_ids', 'payload', 'updated_at'])
    return report


def _run(org, title, issue_query, date_from, date_to, candidates):
    """Shared generate path: validate config, call the API, normalise. Returns
    ``(selected_ids, payload)``."""
    api_keys = _groq_api_keys()
    if not api_keys:
        raise ReportAIError('AI is not configured — set GROQ_API_KEY.')

    try:
        import groq
    except ImportError:
        raise ReportAIError('The "groq" package is not installed (pip install groq).')

    try:
        data = _call_ai(api_keys, org, title, issue_query, date_from, date_to, candidates)
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
        logger.exception("issue report AI failed for org=%s", org.id)
        raise ReportAIError('Analysis failed to generate. Check the server logs for details.')

    valid_refs = {c['ref'] for c in candidates}
    return _normalise(data, valid_refs)


def _call_ai(api_keys, org, title, issue_query, date_from, date_to, candidates):
    cand_block = "\n".join(
        f"- [{c['ref']}] ({c['source']}, {c['date']}, {c['sentiment']}) {c['text']}"
        for c in candidates
    )

    user_prompt = f"""Organisation: {org.name}
Issue / saga to isolate: {title}
Issue description: {issue_query}
Reporting period: {date_from} to {date_to}

CANDIDATE MENTIONS (each prefixed with its reference id in square brackets):
{cand_block}

From the candidates above, select ONLY the mentions that are genuinely about the
issue "{title}". Ignore anything off-topic. Then analyse the SELECTED mentions.

Return a JSON object with EXACTLY these keys: selected, exec_summary, at_a_glance, timeline, framing, risks, stakeholders, recommendations.

- "selected": array of reference ids, ONLY ones that appear in the candidates above (e.g. "online-12"); never invent ids.
- "exec_summary": 3-5 sentences on the issue and its media footprint.
- "at_a_glance": array of {{"label","text","source"}} — short label/text pairs (e.g. Claim / Position / Latest turn), each "source" a ref from "selected", or empty.
- "timeline": array of {{"date","text","source","tone"}}, 4-8 events. Every event MUST cite a "source" ref drawn from your selected set; do not include events you cannot ground in a selected mention. "tone" is positive/neutral/negative. Give each a one-sentence "text".
- "framing": array of {{"title","text"}}, 3-5 themes. A 2-4 word "title" per theme (e.g. "The binary framing problem") with a 2-3 sentence "text" analysis.
- "risks": array of {{"title","note","score","media"}}, 2-5 items. A 2-4 word "title" per reputational risk with a one-sentence "note", "score" -100..0, "media" one of Social/Online/Print/Broadcast.
- "stakeholders": array of {{"dimension","score","note"}}, 4-7 items, e.g. Investors / Regulators / Government & Politics / Media, "score" 0-100, each a one-sentence "note".
- "recommendations": array of {{"title","text"}}, 3-6 items. A short action "title" with a 1-2 sentence "text".
- Base all figures and facts on the selected mentions only. Do NOT restate raw headlines as analysis — abstract and synthesise.
- Output ONLY the JSON object, no markdown, no commentary."""

    # 2026-08-29: measured against the real API — MAX_CANDIDATES=25 candidates
    # is ~1,800 input tokens; max_tokens must leave room under that within
    # Groq's 8,000-per-request ceiling (a 413, not a 429 — this is a hard
    # per-request size limit, not a "wait and retry" rate limit; see
    # _groq_json_call's docstring for that distinction). 6000 was too high
    # and got rejected outright before ever running.
    return _groq_json_call(api_keys, SYSTEM_PROMPT, user_prompt, max_tokens=5000)


# ── Normalisation (never trust the model's shape or its refs) ──────────────────

def _normalise(data, valid_refs):
    """Return ``(selected_ids, payload)``. Refs are intersected with the real
    candidate set; timeline events without a valid source ref are dropped."""
    selected = [r for r in _as_list(data.get('selected')) if r in valid_refs]
    selected_ids = {'online': [], 'print': [], 'social': [], 'broadcast': []}
    for ref in selected:
        mt, _, sid = ref.partition('-')
        if mt in selected_ids:
            try:
                selected_ids[mt].append(int(sid))
            except (TypeError, ValueError):
                pass

    at_a_glance = []
    for row in _as_list(data.get('at_a_glance'))[:8]:
        label = str(row.get('label') or '').strip()[:40]
        text = str(row.get('text') or '').strip()[:400]
        if not (label and text):
            continue
        src = str(row.get('source') or '').strip()
        at_a_glance.append({'label': label, 'text': text,
                            'source': src if src in valid_refs else ''})

    timeline = []
    for row in _as_list(data.get('timeline'))[:12]:
        src = str(row.get('source') or '').strip()
        text = str(row.get('text') or '').strip()[:300]
        if not text or src not in valid_refs:   # guardrail: events must be grounded
            continue
        tone = row.get('tone')
        timeline.append({
            'date': str(row.get('date') or '').strip()[:10],
            'text': text,
            'source': src,
            'tone': tone if tone in ('positive', 'neutral', 'negative') else 'neutral',
        })

    framing = []
    for row in _as_list(data.get('framing'))[:8]:
        title = str(row.get('title') or '').strip()[:80]
        text = str(row.get('text') or '').strip()[:600]
        if title and text:
            framing.append({'title': title, 'text': text})

    risks = []
    for row in _as_list(data.get('risks'))[:8]:
        title = str(row.get('title') or '').strip()[:60]
        if not title:
            continue
        risks.append({
            'title': title,
            'note': str(row.get('note') or row.get('description') or '').strip()[:300],
            'score': _sent(row.get('score')),
            'media': str(row.get('media') or '').strip()[:20],
        })

    stakeholders = []
    for row in _as_list(data.get('stakeholders'))[:10]:
        dim = str(row.get('dimension') or '').strip()[:40]
        if not dim:
            continue
        stakeholders.append({
            'dimension': dim,
            'score': _clamp100(row.get('score')),
            'note': str(row.get('note') or '').strip()[:200],
        })

    recommendations = []
    for row in _as_list(data.get('recommendations'))[:8]:
        title = str(row.get('title') or '').strip()[:80]
        text = str(row.get('text') or '').strip()[:500]
        if title and text:
            recommendations.append({'title': title, 'text': text})

    payload = {
        'exec_summary': str(data.get('exec_summary') or '').strip()[:2000],
        'at_a_glance': at_a_glance,
        'timeline': timeline,
        'framing': framing,
        'risks': risks,
        'stakeholders': stakeholders,
        'recommendations': recommendations,
    }
    return selected_ids, payload


def _clamp100(v):
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return 50
