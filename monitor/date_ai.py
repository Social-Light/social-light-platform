"""Contextual publish-date extraction for social mentions, via Groq.

Why this exists: a meaningful slice of SocialMediaPost rows have
date_published set to their own ingestion date rather than a real parsed
publish date — the crawler/paste-backfill path falls back to "today" when
the source (Apify, Tavily, a manually-pasted OCR extraction, ...) didn't
supply a usable timestamp (see fetcher/bridge.py's _published_date()
docstring on this exact fallback). That silently mis-dates historical
coverage as happening "today."

This module reads a post's headline + summary and looks for a date the
TEXT ITSELF gives away — an explicit date ("issued 30 August 2026"), a
relative reference ("2 days ago", "yesterday"), or an event date embedded
in the copy. It cannot recover a date that simply isn't mentioned anywhere
in the text; those rows are correctly left unchanged (best-effort, not
magic — same honesty contract as sentiment_ai.py's analyze_sentiment).

Provider: Groq, same model/key-fallback pattern as monitor/sentiment_ai.py
(GROQ_API_KEY, falling back to GROQ_API_KEY_2 on any request failure) — see
that module's docstring for the shared-quota rationale. Reuses the same
account/quota pool as sentiment analysis and the AI reports, so a large
backfill run competes with those for the same daily token cap.

Every call is best-effort: on any failure (no key, SDK missing, rate limit,
malformed response, or the model finding no date in the text) extract_
published_date returns None and the caller keeps the mention's current
date_published — this must never overwrite a real value with a guess, and
never raise into a request/task that calls it.
"""
import json
import logging
import re
import time
from datetime import date, datetime

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'SENTIMENT_AI_MODEL', 'openai/gpt-oss-120b')

SYSTEM_PROMPT = (
    "You extract publish dates for a media-monitoring platform. Given a "
    "headline, summary, and the date this item was ingested (which may or "
    "may not be its real publish date), find the ACTUAL date the coverage "
    "was published or the event it describes happened, using ONLY date "
    "evidence present in the headline/summary text itself (an explicit "
    "date, 'yesterday', '2 days ago', 'issued <date>', an event date "
    "quoted in the copy, etc.) — never guess or infer from context you "
    "are not given. Respond with ONLY a single JSON object — no markdown, "
    "no prose, no code fences."
)

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# Groq's rate-limit error body includes "Please try again in Xms"/"in Xs" —
# a short one (TPM: tokens-per-MINUTE) is worth a brief sleep-and-retry on
# the SAME key, since it'll likely have recovered; a long one (TPD: tokens-
# per-DAY, often minutes) isn't — move on to the next key immediately rather
# than stall the whole backfill. Confirmed live 2026-09-01: two freshly-
# dedicated GROQ_API_KEY_DATE_* keys are TPM-limited (8000/min) and, tried
# first for every single row with no backoff, stayed pinned near their cap
# continuously — a 150-row run re-dated only 1, most of it spent cycling
# through 9 keys per row instead of giving the fast-recovering dedicated
# key a moment to actually recover.
# Was 3.0 until 2026-09 — too tight, per report_ai.py's own
# _MAX_SHORT_RETRY_SECONDS docstring: a real rolling-TPM wait has been
# observed as high as ~6s, so a 3s cutoff was rejecting a genuine wait a few
# more seconds would have carried through, and falling through to the next
# key instead. Matched to report_ai.py's 20.0s here too — comfortably above
# any real TPM wait, nowhere near the multi-minute TPD waits described above.
_RETRY_AFTER_RE = re.compile(r'try again in ([\d.]+)(ms|s)\b')
_MAX_SHORT_RETRY_SECONDS = 20.0


def _short_retry_after(exc) -> float | None:
    """Seconds to sleep before retrying the SAME key once, or None if the
    error carries no wait hint or the hint is too long to be worth waiting
    on inline (a multi-minute TPD cooldown — fall through to the next key
    instead)."""
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    value, unit = match.groups()
    seconds = float(value) / 1000.0 if unit == 'ms' else float(value)
    return seconds if seconds <= _MAX_SHORT_RETRY_SECONDS else None


_MAX_GROQ_KEYS = 10           # see sentiment_ai.py's identically-named constant
_MAX_DEDICATED_DATE_KEYS = 5  # GROQ_API_KEY_DATE_1.._MAX_DEDICATED_DATE_KEYS


def _groq_api_keys():
    """Every configured Groq key, in fallback order: GROQ_API_KEY_DATE_1,
    _DATE_2, ... first — accounts reserved exclusively for this module (see
    settings.py's comment on them; sentiment_ai.py never reads these) — then
    the shared pool (GROQ_API_KEY, GROQ_API_KEY_2.._MAX_GROQ_KEYS, also used
    by sentiment_ai.py/report_ai.py/etc.) as a fallback if the dedicated
    keys are also exhausted. Trying dedicated keys first means date re-
    labelling gets its own quota most of the time, without giving up the
    shared pool as a backstop."""
    keys = []
    dedicated = [f'GROQ_API_KEY_DATE_{i}' for i in range(1, _MAX_DEDICATED_DATE_KEYS + 1)]
    shared = ['GROQ_API_KEY'] + [f'GROQ_API_KEY_{i}' for i in range(2, _MAX_GROQ_KEYS + 1)]
    for attr in dedicated + shared:
        key = (getattr(settings, attr, '') or '').strip().strip('"').strip("'")
        if key:
            keys.append(key)
    return keys


def extract_published_date(headline, summary='', ingested_date=None):
    """Return {'published_date': date, 'evidence': str} for a post's real
    publish date, read out of its own text, or None if the text gives no
    date evidence (or on any failure) — callers should treat None as "leave
    date_published as it was." `evidence` is the model's short quote/
    reasoning from the text — mirrors sentiment_ai.py's analyze_sentiment()
    returning a rationale alongside its sentiment, for the same reason: an
    audit trail of WHY the AI set what it set (see SocialMediaPost.
    date_correction_note).

    Args:
        headline:       Post headline/title.
        summary:        Post body/summary text.
        ingested_date:  The date this row was ingested (datetime.date) —
                         given to the model as context for resolving
                         relative references ("yesterday" relative to
                         what?), not as something it should return
                         unchanged when no better evidence exists.
    """
    api_keys = _groq_api_keys()
    if not api_keys:
        return None

    headline = (headline or '').strip()
    summary = (summary or '').strip()
    if not headline and not summary:
        return None

    try:
        import groq
    except ImportError:
        logger.warning('date_ai: the "groq" package is not installed.')
        return None

    ingested_str = ingested_date.isoformat() if ingested_date else '(unknown)'
    user_prompt = f"""Ingested on: {ingested_str}

Headline: {headline}
Summary: {summary or '(none provided)'}

Return a JSON object with exactly these keys:
{{
  "published_date": "YYYY-MM-DD" or null (null if the text gives no date evidence at all),
  "confidence": "high" | "medium" | "low",
  "evidence": "<short quote or reasoning from the text, or empty string if published_date is null>"
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
                    # See sentiment_ai.py's identical comment: gpt-oss models
                    # spend hidden reasoning tokens before the final JSON.
                    # 400 was still too tight — confirmed live 2026-09-01,
                    # repeated "Failed to validate/generate JSON... max
                    # completion tokens reached before generating a valid
                    # document" across multiple keys on the same row (a
                    # response-quality failure, not a rate-limit one — no
                    # key fallback fixes it). Matched to sentiment_ai.py's
                    # 600, which resolved the same failure mode there.
                    max_tokens=600,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                )
            except Exception as exc:
                # Only the request itself lands here — a malformed response is
                # handled separately below, so a key/network problem is the
                # only thing that triggers the wait-or-next-key logic.
                wait = None if retried_after_wait else _short_retry_after(exc)
                if wait is not None:
                    retried_after_wait = True
                    time.sleep(wait)
                    continue  # one retry on the same key after its cooldown
                logger.warning(
                    "date_ai.extract_published_date failed (key #%d/%d)%s: %s",
                    i + 1, len(api_keys),
                    '' if is_last_key else ' — retrying with next key', exc,
                    exc_info=is_last_key,
                )
                if is_last_key:
                    return None
                break  # give up on this key — move to the next one

            choice = completion.choices[0]
            if choice.finish_reason == 'length':
                return None
            try:
                data = _parse_json(choice.message.content)
            except ValueError as exc:
                # A malformed/truncated response is a model-response-quality
                # issue, not a key issue — return None immediately rather
                # than burning through the rest of the shared key pool on a
                # retry that wouldn't help.
                logger.warning("date_ai.extract_published_date: unparseable response: %s", exc)
                return None
            break  # success — exit the retry-same-key loop
        if data is not None:
            break  # a key succeeded

    raw = data.get('published_date')
    if not raw or not isinstance(raw, str) or not _DATE_RE.match(raw):
        return None
    if data.get('confidence') not in ('high', 'medium'):
        return None
    try:
        parsed = datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return None
    # Never return a date in the future relative to ingestion — a
    # hallucinated/misread date is more likely than genuinely-precognitive
    # coverage.
    if ingested_date and parsed > ingested_date:
        return None
    evidence = (data.get('evidence') or '').strip()[:500]
    return {'published_date': parsed, 'evidence': evidence}


def _parse_json(text):
    """Mirrors sentiment_ai.py's _parse_json — Groq's json_object mode
    guarantees syntactically valid JSON but not a particular schema."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model response")
    candidate = text[start:end + 1]
    candidate = re.sub(r',(\s*[}\]])', r'\1', candidate)  # trailing commas
    return json.loads(candidate)
