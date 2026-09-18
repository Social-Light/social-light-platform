"""Contextual, per-mention relevancy disambiguation via Groq.

monitor/relevancy.py's compute_relevancy() is a deterministic, non-AI score:
it's a literal \\b-bounded (plus squashed-text fuzzy) substring match against
an organisation's tracked keywords/competitors, computed at ingest time with
no external services so it stays fast for bulk imports. That means it has no
way to tell two entities apart when they share a tracked term — Botswana
Power Corporation's keyword "BPC" also matches the peptide "BPC-157", the
"British Psychoanalytic Council", and the "Building and Plumbing Commission",
and every one of those scores exactly as high as real BPC coverage (a single
brand-weighted hit already clears most thresholds). Confirmed 2026-09-18:
11 of 14 Social rows that passed the keyword filter for BPC on one day were
this kind of false positive.

This module adds the read compute_relevancy can't do: an LLM actually reads
the headline+summary(+body) and the specific term(s) that matched, and judges
whether the coverage is genuinely about the monitored organisation. It runs
as a separate rate-limited background pass (see the analyze_relevancy
management command / Celery task), never inline in the ingest path — same
reasoning as sentiment_ai.py vs. the ingest-time sentiment sources.

Provider: Groq (openai/gpt-oss-120b), same choice and same shared free-tier
key pool as sentiment_ai.py/report_ai.py/sector_ai.py/issue_report_ai.py —
see sentiment_ai.py's docstring for the token-budget reasoning; a single call
here is a similarly small headline+summary+term-list read.

Every call is best-effort: on any failure (no key, SDK missing, rate limit,
malformed response) disambiguate_relevancy returns None and the caller must
leave relevancy_ai_relevant as it was (NULL = "not yet checked", so the row
keeps passing on its keyword score alone) — this must never raise into a
request/task that calls it, and never be treated as a false-positive verdict.
"""
import json
import logging
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'RELEVANCY_AI_MODEL', 'openai/gpt-oss-120b')

SYSTEM_PROMPT = (
    "You are a media-monitoring analyst. A keyword-matching system has flagged "
    "a piece of coverage as possibly about a monitored organisation, based on a "
    "literal text match on one of its tracked terms. Tracked terms are often "
    "short names, abbreviations or acronyms, and can collide with completely "
    "unrelated entities that happen to share the same letters or words (for "
    "example, a tracked term \"BPC\" could genuinely mean \"Botswana Power "
    "Corporation\", or it could be an unrelated match like the peptide "
    "\"BPC-157\", the \"British Psychoanalytic Council\", or the \"Building and "
    "Plumbing Commission\"). Read the coverage and the matched term(s) in "
    "context and judge whether this is ACTUALLY about the monitored "
    "organisation, or a false-positive collision on the same term. When the "
    "text gives too little context to tell, prefer relevant=true — this check "
    "only exists to catch confident false positives, not to second-guess "
    "genuinely ambiguous cases. Respond with ONLY a single JSON object — no "
    "markdown, no prose, no code fences."
)

_VALID = {True, False}

# See sentiment_ai.py's identically-named helpers/constants for the reasoning
# behind the short-retry-then-next-key fallback and the 20s cutoff.
_RETRY_AFTER_RE = re.compile(r'try again in ([\d.]+)(ms|s)\b')
_MAX_SHORT_RETRY_SECONDS = 20.0


def _short_retry_after(exc) -> float | None:
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    value, unit = match.groups()
    seconds = float(value) / 1000.0 if unit == 'ms' else float(value)
    return seconds if seconds <= _MAX_SHORT_RETRY_SECONDS else None


# See sentiment_ai.py's _groq_api_keys for why this goes up to 10 and why the
# "extra" keys don't always mean extra quota.
_MAX_GROQ_KEYS = 10


def _groq_api_keys():
    keys = []
    attrs = ['GROQ_API_KEY'] + [f'GROQ_API_KEY_{i}' for i in range(2, _MAX_GROQ_KEYS + 1)]
    for attr in attrs:
        key = (getattr(settings, attr, '') or '').strip().strip('"').strip("'")
        if key:
            keys.append(key)
    return keys


def disambiguate_relevancy(org_name, matched_terms, headline, summary='', body=''):
    """Return {'relevant': bool, 'rationale': str} or None on any failure (no
    key, SDK missing, API error, malformed response, or nothing to check) —
    callers should treat None as "leave relevancy_ai_relevant as it was",
    never as a false-positive verdict.

    matched_terms: the tracked keyword/competitor terms that triggered this
    row's keyword match (see relevancy.matched_terms()) — telling the model
    which term(s) matched, rather than making it re-derive that itself, is
    what lets it reason precisely about the collision instead of guessing.
    """
    api_keys = _groq_api_keys()
    if not api_keys:
        return None

    headline = (headline or '').strip()
    summary = (summary or '').strip()
    body = (body or '').strip()
    terms = [t for t in (matched_terms or []) if t and t.strip()]
    if not terms or (not headline and not summary and not body):
        return None  # nothing to disambiguate

    try:
        import groq
    except ImportError:
        logger.warning('relevancy_ai: the "groq" package is not installed.')
        return None

    # Body can be long (a full crawled article) — cap it well above what a
    # disambiguation judgment needs, same order of magnitude as sentiment_ai's
    # headline+summary-only budget, without spending unbounded input tokens
    # on a multi-thousand-word article the model needs only a slice of to
    # judge who it's actually about.
    body_excerpt = body[:2000]

    user_prompt = f"""Organisation being monitored: {org_name}
Tracked term(s) that matched: {', '.join(terms)}

Headline: {headline or '(none)'}
Summary: {summary or '(none provided)'}
Body excerpt: {body_excerpt or '(none provided)'}

Return a JSON object with exactly these keys:
{{
  "relevant": true | false,
  "rationale": "<one sentence explaining why, naming what the matched term actually refers to here>"
}}"""

    data = None
    for i, api_key in enumerate(api_keys):
        is_last_key = i == len(api_keys) - 1
        retried_after_wait = False
        while True:
            try:
                client = groq.Groq(api_key=api_key, timeout=15.0, max_retries=1)
                completion = client.chat.completions.create(
                    model=MODEL,
                    # See sentiment_ai.py's identical comment — gpt-oss spends hidden
                    # reasoning tokens before the JSON, so this is headroom, not a target.
                    max_tokens=600,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                )
            except Exception as exc:
                wait = None if retried_after_wait else _short_retry_after(exc)
                if wait is not None:
                    retried_after_wait = True
                    time.sleep(wait)
                    continue
                logger.warning(
                    "relevancy_ai.disambiguate_relevancy failed for org=%s (key #%d/%d)%s: %s",
                    org_name, i + 1, len(api_keys),
                    '' if is_last_key else ' — retrying with next key', exc,
                    exc_info=is_last_key,
                )
                if is_last_key:
                    return None
                break
            choice = completion.choices[0]
            if choice.finish_reason == 'length':
                return None
            try:
                data = _parse_json(choice.message.content)
            except ValueError as exc:
                logger.warning(
                    "relevancy_ai.disambiguate_relevancy: unparseable response for org=%s: %s",
                    org_name, exc)
                return None
            break
        if data is not None:
            break

    relevant = data.get('relevant')
    if relevant not in _VALID:
        return None
    rationale = (data.get('rationale') or '').strip()[:500]
    return {'relevant': relevant, 'rationale': rationale}


def _parse_json(text):
    """See sentiment_ai.py's identically-named helper — same belt-and-braces
    extraction for Groq's json_object mode."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model response")
    candidate = text[start:end + 1]
    candidate = re.sub(r',(\s*[}\]])', r'\1', candidate)
    return json.loads(candidate)
