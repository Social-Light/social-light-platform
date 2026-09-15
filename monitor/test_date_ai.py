"""
Tests for monitor/date_ai.py — mocks the groq SDK entirely (via
sys.modules), no real API key or network access needed. Mirrors
test_sentiment_ai.py's mocking approach.
"""
import sys
import types
from datetime import date
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from monitor.date_ai import extract_published_date

# See test_sentiment_ai.py's identically-defined dict for why this exists —
# isolates a test from whatever real keys are configured in the environment.
# Includes the GROQ_API_KEY_DATE_* dedicated keys (date_ai.py-only, tried
# before the shared pool — see that module's _groq_api_keys()) alongside the
# shared GROQ_API_KEY_3.._10 slots.
_BLANK_GROQ_3_10 = {f'GROQ_API_KEY_{i}': '' for i in range(3, 11)}
_BLANK_GROQ_3_10.update({f'GROQ_API_KEY_DATE_{i}': '' for i in range(1, 6)})


def _completion(content, finish_reason='stop'):
    choice = MagicMock()
    choice.finish_reason = finish_reason
    choice.message.content = content
    completion = MagicMock()
    completion.choices = [choice]
    return completion


def _fake_groq_module(create_mock, used_keys=None):
    """See test_sentiment_ai.py's identically-named helper — `used_keys`,
    if given, records every api_key a Groq(...) is constructed with, for
    asserting fallback order."""
    module = types.ModuleType('groq')

    class FakeGroq:
        def __init__(self, api_key=None, timeout=None, max_retries=None):
            if used_keys is not None:
                used_keys.append(api_key)
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create_mock))

    module.Groq = FakeGroq
    return module


class ExtractPublishedDateTests(TestCase):
    @override_settings(GROQ_API_KEY='', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_returns_none_when_no_keys_configured(self):
        self.assertIsNone(extract_published_date('Headline', 'Summary'))

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_returns_none_when_no_text(self):
        self.assertIsNone(extract_published_date('', ''))

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_extracts_explicit_date_high_confidence(self):
        body = '{"published_date": "2026-08-30", "confidence": "high", "evidence": "issued 30 August 2026"}'
        create_mock = MagicMock(return_value=_completion(body))
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock)}):
            result = extract_published_date(
                'Planned Maintenance Notice', 'Issued 30 August 2026.',
                ingested_date=date(2026, 9, 1))
        self.assertEqual(result, {'published_date': date(2026, 8, 30),
                                  'evidence': 'issued 30 August 2026'})

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_no_date_evidence_returns_none(self):
        body = '{"published_date": null, "confidence": "low", "evidence": ""}'
        create_mock = MagicMock(return_value=_completion(body))
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock)}):
            result = extract_published_date(
                'Generic headline with no date cues', 'No date mentioned anywhere.',
                ingested_date=date(2026, 9, 1))
        self.assertIsNone(result)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_low_confidence_rejected(self):
        body = '{"published_date": "2026-08-15", "confidence": "low", "evidence": "vague reference"}'
        create_mock = MagicMock(return_value=_completion(body))
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock)}):
            result = extract_published_date(
                'Ambiguous headline', 'Some vague date-ish text.',
                ingested_date=date(2026, 9, 1))
        self.assertIsNone(result)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_future_date_rejected(self):
        # A "published" date after the ingestion date is more likely a
        # misread than genuine — reject rather than trust it blindly.
        body = '{"published_date": "2026-09-15", "confidence": "high", "evidence": "..."}'
        create_mock = MagicMock(return_value=_completion(body))
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock)}):
            result = extract_published_date(
                'Headline', 'Summary', ingested_date=date(2026, 9, 1))
        self.assertIsNone(result)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='key2', **_BLANK_GROQ_3_10)
    def test_falls_back_to_second_key_on_failure(self):
        body = '{"published_date": "2026-08-20", "confidence": "high", "evidence": "2 days ago"}'
        create_mock = MagicMock(side_effect=[Exception('rate limited'), _completion(body)])
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock)}):
            result = extract_published_date(
                'Headline', 'Posted 2 days ago', ingested_date=date(2026, 8, 22))
        self.assertEqual(result['published_date'], date(2026, 8, 20))
        self.assertEqual(create_mock.call_count, 2)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_malformed_date_string_returns_none(self):
        body = '{"published_date": "not-a-date", "confidence": "high", "evidence": "x"}'
        create_mock = MagicMock(return_value=_completion(body))
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock)}):
            result = extract_published_date('Headline', 'Summary', ingested_date=date(2026, 9, 1))
        self.assertIsNone(result)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    @patch('monitor.date_ai.time.sleep')
    def test_short_tpm_wait_retries_same_key(self, mock_sleep):
        # See test_sentiment_ai.py's identically-purposed test.
        body = '{"published_date": "2026-08-20", "confidence": "high", "evidence": "..."}'
        rate_limit_exc = Exception(
            "Rate limit reached ... tokens per minute (TPM): Limit 8000, Used 7781, "
            "Requested 585. Please try again in 2.745s. Need more tokens?")
        create_mock = MagicMock(side_effect=[rate_limit_exc, _completion(body)])
        used_keys = []
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock, used_keys=used_keys)}):
            result = extract_published_date('Headline', 'Summary', ingested_date=date(2026, 9, 1))
        self.assertEqual(result['published_date'], date(2026, 8, 20))
        self.assertEqual(used_keys, ['key1', 'key1'])
        mock_sleep.assert_called_once_with(2.745)

    @override_settings(GROQ_API_KEY='key1', GROQ_API_KEY_2='', **_BLANK_GROQ_3_10)
    def test_long_tpd_wait_not_retried_inline(self):
        rate_limit_exc = Exception("... Please try again in 5m42.5s. ...")
        create_mock = MagicMock(side_effect=rate_limit_exc)
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock)}):
            with patch('monitor.date_ai.time.sleep') as mock_sleep:
                result = extract_published_date('Headline', 'Summary', ingested_date=date(2026, 9, 1))
        self.assertIsNone(result)
        self.assertEqual(create_mock.call_count, 1)
        mock_sleep.assert_not_called()

    @override_settings(GROQ_API_KEY='shared1', GROQ_API_KEY_2='shared2',
                       GROQ_API_KEY_DATE_1='dedicated1', GROQ_API_KEY_DATE_2='dedicated2',
                       **{k: v for k, v in _BLANK_GROQ_3_10.items()
                          if k not in ('GROQ_API_KEY_DATE_1', 'GROQ_API_KEY_DATE_2')})
    def test_dedicated_date_keys_tried_before_shared_pool(self):
        # date_ai.py is meant to draw on its own reserved account(s) before
        # ever touching the pool sentiment_ai.py/report_ai.py also share —
        # so GROQ_API_KEY_DATE_1/_2 must be attempted first, GROQ_API_KEY/
        # _2 only as a fallback if both dedicated keys fail.
        body = '{"published_date": "2026-08-20", "confidence": "high", "evidence": "..."}'
        create_mock = MagicMock(side_effect=[
            Exception('dedicated1 down'), Exception('dedicated2 down'), _completion(body)])
        used_keys = []
        with patch.dict(sys.modules, {'groq': _fake_groq_module(create_mock, used_keys=used_keys)}):
            result = extract_published_date('Headline', 'Summary', ingested_date=date(2026, 9, 1))
        self.assertEqual(result['published_date'], date(2026, 8, 20))
        self.assertEqual(used_keys, ['dedicated1', 'dedicated2', 'shared1'])
