"""Contextual, per-mention sentiment analysis via Groq.

Today, sentiment on a mention comes from one of three non-LLM sources
depending on how it was ingested — none of which actually reads and reasons
about the text:
  - media-monitor's crawler: VADER, a lexicon/word-polarity scorer (no real
    contextual understanding — can't tell "regulator fines COMPETITOR" apart
    from "regulator fines ORG", both just score on the word "fines").
  - MediaHost API clips: whatever sentiment code the vendor's own system
    already tagged the clip with — pure pass-through, no analysis here at all.
  - Manual entry / CSV upload: whatever a human typed in.

This module adds a real option: an LLM actually reads the headline+summary
and judges sentiment FROM THE MONITORED ORGANISATION'S PERSPECTIVE — not
"is this good news in general" but "is this good or bad for {org}" (a
competitor being fined is positive for org, not negative, even though
"fine" alone would read negative to a lexicon scorer). It also writes a
one-sentence rationale (sentiment_rationale on each model) — an editor
looking at the dashboard can see WHY, not just a bare label.

Provider: Groq (openai/gpt-oss-120b), not Anthropic — deliberately, because
this is a much better fit for Groq's free-tier rate limit than
report_ai.py's ESG analysis was (see that module's docstring for the full
story). A single mention here needs a few hundred input tokens and well
under 200 output tokens, comfortably inside the 8,000 TPM ceiling even
running as a batch — nothing like the ESG report's one giant multi-section
call. If Groq ever becomes the bottleneck, MODEL can be pointed at Anthropic
the same way report_ai.py is (see the REPORT_AI_MODEL-style override hook),
but there's no reason to pay for that here today.

Every call is best-effort: on any failure (no key, SDK missing, rate limit,
malformed response) analyze_sentiment returns None and the caller keeps
whatever sentiment the mention already had — this must never overwrite a
real value with a guess, and never raise into a request/task that calls it.
"""
import json
import logging
import re
import time

from django.conf import settings

logger = logging.getLogger(__name__)

MODEL = getattr(settings, 'SENTIMENT_AI_MODEL', 'openai/gpt-oss-120b')

SYSTEM_PROMPT = (
    "You are a media-monitoring analyst. Given a headline and summary, and the "
    "organisation whose media coverage is being monitored, judge the sentiment "
    "of the coverage FROM THAT ORGANISATION'S PERSPECTIVE — not sentiment in "
    "general. Coverage that is bad for a competitor, a critic, or an unrelated "
    "party is not automatically negative for the monitored organisation; read "
    "who the news actually reflects on. Respond with ONLY a single JSON object "
    "— no markdown, no prose, no code fences."
)

_VALID_SENTIMENTS = {'positive', 'negative', 'neutral'}

# See date_ai.py's identically-named helpers/constants for why this exists —
# a short "try again in Xms/Xs" TPM cooldown is worth one inline retry on
# the same key rather than immediately falling through the whole chain.
_RETRY_AFTER_RE = re.compile(r'try again in ([\d.]+)(ms|s)\b')
_MAX_SHORT_RETRY_SECONDS = 3.0


def _short_retry_after(exc) -> float | None:
    match = _RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    value, unit = match.groups()
    seconds = float(value) / 1000.0 if unit == 'ms' else float(value)
    return seconds if seconds <= _MAX_SHORT_RETRY_SECONDS else None


# Probed as GROQ_API_KEY, GROQ_API_KEY_2, ... GROQ_API_KEY_10 — raise this to
# add more without touching this module again; each just needs its own
# settings.py assignment (getattr's default handles an undefined one fine).
_MAX_GROQ_KEYS = 10


def _groq_api_keys():
    """Every configured Groq key, in fallback order: the primary
    GROQ_API_KEY, then GROQ_API_KEY_2.._MAX_GROQ_KEYS — additional accounts'
    keys to fall back through when one's daily token cap (200,000 TPD on the
    free tier; GROQ_API_KEY specifically is also shared with report_ai.py/
    sector_ai.py/issue_report_ai.py) runs out mid-day. Not all "additional
    accounts" are actually independent quota — Groq allows multiple keys per
    organization, and several supplied here have turned out to share one
    pool (confirmed via the org_... id in a 429 response) rather than each
    being a fresh account; the fallback still costs nothing extra to keep,
    it just doesn't multiply capacity the way a genuinely separate account
    would."""
    keys = []
    attrs = ['GROQ_API_KEY'] + [f'GROQ_API_KEY_{i}' for i in range(2, _MAX_GROQ_KEYS + 1)]
    for attr in attrs:
        key = (getattr(settings, attr, '') or '').strip().strip('"').strip("'")
        if key:
            keys.append(key)
    return keys


def analyze_sentiment(org_name, headline, summary=''):
    """Return {'sentiment': 'positive'|'negative'|'neutral', 'rationale': str}
    or None on any failure (no key, SDK missing, API error, malformed
    response) — callers should treat None as "leave sentiment as it was",
    never as a reason to fail the caller's own operation."""
    api_keys = _groq_api_keys()
    if not api_keys:
        return None

    headline = (headline or '').strip()
    summary = (summary or '').strip()
    if not headline and not summary:
        return None  # nothing to analyse

    try:
        import groq
    except ImportError:
        logger.warning('sentiment_ai: the "groq" package is not installed.')
        return None

    user_prompt = f"""Organisation being monitored: {org_name}

Headline: {headline}
Summary: {summary or '(none provided)'}

Return a JSON object with exactly these keys:
{{
  "sentiment": "positive" | "negative" | "neutral",
  "rationale": "<one sentence explaining why, from {org_name}'s perspective specifically>"
}}"""

    # Try each configured key in order, falling back to the next on ANY
    # request failure (rate limit, auth, network — no need to special-case
    # which, same reasoning as media-monitor's Tavily fallback). A malformed/
    # truncated response is a model-response-quality issue, not a key issue,
    # so those still return None immediately rather than burning a second
    # key's quota on a retry that wouldn't help.
    data = None
    for i, api_key in enumerate(api_keys):
        is_last_key = i == len(api_keys) - 1
        retried_after_wait = False
        while True:
            try:
                client = groq.Groq(api_key=api_key, timeout=15.0, max_retries=1)
                completion = client.chat.completions.create(
                    model=MODEL,
                    # gpt-oss models spend hidden reasoning tokens before the final JSON,
                    # counted against max_tokens — 200 was too tight and truncated the
                    # response before it ever reached valid JSON on ~60% of real
                    # headlines in testing. This is generous headroom, not a target;
                    # actual usage/cost only reflects what the model really generates.
                    max_tokens=600,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                )
                choice = completion.choices[0]
                if choice.finish_reason == 'length':
                    return None
                data = _parse_json(choice.message.content)
                break  # success — exit the retry-same-key loop
            except Exception as exc:
                wait = None if retried_after_wait else _short_retry_after(exc)
                if wait is not None:
                    retried_after_wait = True
                    time.sleep(wait)
                    continue  # one retry on the same key after its cooldown
                logger.warning(
                    "sentiment_ai.analyze_sentiment failed for org=%s (key #%d/%d)%s: %s",
                    org_name, i + 1, len(api_keys),
                    '' if is_last_key else ' — retrying with next key', exc,
                    exc_info=is_last_key,
                )
                if is_last_key:
                    return None
                break  # give up on this key — move to the next one
        if data is not None:
            break  # a key succeeded

    sentiment = (data.get('sentiment') or '').strip().lower()
    if sentiment not in _VALID_SENTIMENTS:
        return None
    rationale = (data.get('rationale') or '').strip()[:500]
    return {'sentiment': sentiment, 'rationale': rationale}


def _parse_json(text):
    """Groq's json_object mode guarantees syntactically valid JSON but not a
    particular schema — mirrors report_ai.py's belt-and-braces extraction
    from before it moved to Anthropic's schema-enforced structured outputs."""
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
