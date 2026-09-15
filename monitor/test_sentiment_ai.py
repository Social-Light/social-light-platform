"""
Tests for monitor/sentiment_ai.py — mocks the groq SDK entirely (via
sys.modules), no real API key or network access needed.
"""
import sys
import types
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from monitor.sentiment_ai import _groq_api_keys, analyze_sentiment

GOOD_JSON = '{"sentiment": "positive", "rationale": "Good news for the org."}'

# Blanks GROQ_API_KEY_3 through _10 so a test that only names GROQ_API_KEY/_2
# is isolated from whatever real keys are actually configured in the
# environment (the key list keeps growing — see sentiment_ai.py's
# _MAX_GROQ_KEYS) rather than needing every test updated each time one is
# added. Spread into override_settings via **, never alongside an explicit
# GROQ_API_KEY_3/_4/etc. in the same call (duplicate-kwarg conflict).
_BLANK_GROQ_3_10 = {f'GROQ_API_KEY_{i}': '' for i in range(3, 11)}


def _completion(content, finish_reason='stop'):
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message.content = content
    completion = MagicMock()
    completion.choices = [choice]
    return completion


def _fake_groq_module(create_mock, used_keys=None):
    """
    Build a fake `groq` module whose Groq(api_key=...).chat.completions.create
    is `create_mock` — the SAME mock object across every Groq(...) instance
    analyze_sentiment constructs (once per fallback attempt), so a
    side_effect list on create_mock advances correctly across key attempts.
    If `used_keys` (a list) is given, every api_key a Groq(...) is
    constructed with is appended to it, for asserting fallback order.
    """
    module = types.ModuleType('groq')

    class FakeGroq:
        def __init__(self, api_key=None, timeout=None, max_retries=None):
            if used_keys is not None:
                used_keys.append(api_key)
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create_mock))

    module.Groq = FakeGroq
    return module


# ── _groq_api_keys ───────────────────────────────────────────────────────────

class GroqApiKeysTests(TestCase):
    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='key2', **_BLANK_GROQ_3_10)
    def test_both_keys_returned_in_order(self):
        self.assertEqual(_groq_api_keys(), ['key1', 'key2'])

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='key2',
                       GROQ_API_KEY_3='key3', GROQ_API_KEY_4='key4',
                       GROQ_API_KEY_5='key5', GROQ_API_KEY_6='key6',
                       GROQ_API_KEY_7='key7', GROQ_API_KEY_8='', GROQ_API_KEY_9='',
                       GROQ_API_KEY_10='')
    def test_all_seven_keys_returned_in_order(self):
        self.assertEqual(_groq_api_keys(),
                         ['key1', 'key2', 'key3', 'key4', 'key5', 'key6', 'key7'])

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_blank_second_key_omitted(self):
        self.assertEqual(_groq_api_keys(), ['key1'])

    @override_settings(GROQ_API_KEY='', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_no_keys_returns_empty_list(self):
        self.assertEqual(_groq_api_keys(), [])


# ── analyze_sentiment ────────────────────────────────────────────────────────

class AnalyzeSentimentTests(TestCase):
    @override_settings(GROQ_API_KEY='', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_returns_none_when_no_keys_configured(self):
        self.assertIsNone(analyze_sentiment('Org', 'Headline'))

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_returns_none_when_no_text(self):
        self.assertIsNone(analyze_sentiment('Org', '', ''))

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_happy_path_single_key(self):
        create_mock = MagicMock(return_value=_completion(GOOD_JSON))
        fake = _fake_groq_module(create_mock)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline', 'Summary')
        self.assertEqual(result, {'sentiment': 'positive', 'rationale': 'Good news for the org.'})
        self.assertEqual(create_mock.call_count, 1)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_single_key_configured_only_tries_once_and_returns_none_on_failure(self):
        create_mock = MagicMock(side_effect=Exception('rate limited'))
        fake = _fake_groq_module(create_mock)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline')
        self.assertIsNone(result)
        self.assertEqual(create_mock.call_count, 1)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='key2', **_BLANK_GROQ_3_10)
    def test_falls_back_to_second_key_on_failure(self):
        # First call (primary key) raises — e.g. Groq's 429 rate-limit error
        # once the shared daily token cap is hit — second call (key2) succeeds.
        create_mock = MagicMock(side_effect=[Exception('rate limited'), _completion(GOOD_JSON)])
        used_keys = []
        fake = _fake_groq_module(create_mock, used_keys=used_keys)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline')
        self.assertEqual(result['sentiment'], 'positive')
        self.assertEqual(create_mock.call_count, 2)
        self.assertEqual(used_keys, ['key1', 'key2'])

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='key2', **_BLANK_GROQ_3_10)
    def test_returns_none_when_both_keys_fail(self):
        create_mock = MagicMock(side_effect=[Exception('rate limited'), Exception('also down')])
        fake = _fake_groq_module(create_mock)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline')
        self.assertIsNone(result)
        self.assertEqual(create_mock.call_count, 2)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='key2', **_BLANK_GROQ_3_10)
    def test_truncated_response_returns_none_without_trying_second_key(self):
        # finish_reason == 'length' is a response-quality issue, not a key
        # issue — retrying with a different key wouldn't help, so this
        # should NOT fall back (unlike a request-level exception).
        create_mock = MagicMock(return_value=_completion('', finish_reason='length'))
        fake = _fake_groq_module(create_mock)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline')
        self.assertIsNone(result)
        self.assertEqual(create_mock.call_count, 1)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    @patch('monitor.sentiment_ai.time.sleep')
    def test_short_tpm_wait_retries_same_key(self, mock_sleep):
        # A short "try again in Xms/Xs" (TPM-style) cooldown is worth one
        # inline retry on the SAME key rather than immediately falling
        # through — confirmed necessary live 2026-09-01 (freshly-dedicated
        # date_ai.py keys stayed pinned near their TPM cap because every
        # row fell through the whole chain instead of giving a fast-
        # recovering key a moment to actually recover).
        rate_limit_exc = Exception(
            "Rate limit reached ... on tokens per minute (TPM): Limit 8000, "
            "Used 7781, Requested 585. Please try again in 2.745s. Need more tokens?")
        create_mock = MagicMock(side_effect=[rate_limit_exc, _completion(GOOD_JSON)])
        used_keys = []
        fake = _fake_groq_module(create_mock, used_keys=used_keys)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline')
        self.assertEqual(result['sentiment'], 'positive')
        self.assertEqual(create_mock.call_count, 2)
        # Same key both times — no fallback to a second key needed.
        self.assertEqual(used_keys, ['key1', 'key1'])
        mock_sleep.assert_called_once_with(2.745)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='key2', **_BLANK_GROQ_3_10)
    @patch('monitor.sentiment_ai.time.sleep')
    def test_only_retries_same_key_once_then_falls_through(self, mock_sleep):
        rate_limit_exc = Exception("... Please try again in 1s. ...")
        create_mock = MagicMock(side_effect=[rate_limit_exc, rate_limit_exc, _completion(GOOD_JSON)])
        used_keys = []
        fake = _fake_groq_module(create_mock, used_keys=used_keys)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline')
        self.assertEqual(result['sentiment'], 'positive')
        # One retry on key1 (still fails), then falls through to key2.
        self.assertEqual(used_keys, ['key1', 'key1', 'key2'])
        self.assertEqual(mock_sleep.call_count, 1)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_long_tpd_wait_not_retried_inline(self):
        # A multi-minute TPD cooldown shouldn't stall the loop with a sleep —
        # only short (<=3s) waits get an inline retry.
        rate_limit_exc = Exception("... Please try again in 5m42.5s. ...")
        create_mock = MagicMock(side_effect=rate_limit_exc)
        fake = _fake_groq_module(create_mock)
        with patch.dict(sys.modules, {'groq': fake}):
            with patch('monitor.sentiment_ai.time.sleep') as mock_sleep:
                result = analyze_sentiment('Org', 'Headline')
        self.assertIsNone(result)
        self.assertEqual(create_mock.call_count, 1)
        mock_sleep.assert_not_called()

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_invalid_sentiment_value_returns_none(self):
        create_mock = MagicMock(
            return_value=_completion('{"sentiment": "ecstatic", "rationale": "x"}'))
        fake = _fake_groq_module(create_mock)
        with patch.dict(sys.modules, {'groq': fake}):
            result = analyze_sentiment('Org', 'Headline')
        self.assertIsNone(result)
