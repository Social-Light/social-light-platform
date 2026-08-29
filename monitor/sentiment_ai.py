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


def analyze_sentiment(org_name, headline, summary=''):
    """Return {'sentiment': 'positive'|'negative'|'neutral', 'rationale': str}
    or None on any failure (no key, SDK missing, API error, malformed
    response) — callers should treat None as "leave sentiment as it was",
    never as a reason to fail the caller's own operation."""
    api_key = (getattr(settings, 'GROQ_API_KEY', '') or '').strip().strip('"').strip("'")
    if not api_key:
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
    except Exception:
        # Deliberately broad — auth, rate limit, network, malformed response
        # all degrade the same way: no result, caller keeps today's value.
        logger.warning("sentiment_ai.analyze_sentiment failed for org=%s", org_name, exc_info=True)
        return None

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
