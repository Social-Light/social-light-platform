"""
Tests for monitor/management/commands/analyze_sentiment.py's _split_limit —
the weighted per-media-type budget split (2026-09-01: Social/Broadcast get
3x Online/Print/Competitor's share). Pure function, no AI/DB needed.
"""
from django.test import TestCase

from monitor.management.commands.analyze_sentiment import MEDIA_TYPES, _split_limit


class SplitLimitTests(TestCase):
    def test_social_and_broadcast_get_larger_share(self):
        shares = _split_limit(300, MEDIA_TYPES.keys())
        self.assertGreater(shares['social'], shares['online'])
        self.assertGreater(shares['social'], shares['print'])
        self.assertGreater(shares['social'], shares['competitor'])
        self.assertGreater(shares['broadcast'], shares['online'])

    def test_social_and_broadcast_roughly_equal(self):
        shares = _split_limit(300, MEDIA_TYPES.keys())
        self.assertAlmostEqual(shares['social'], shares['broadcast'], delta=1)

    def test_online_print_competitor_roughly_equal(self):
        shares = _split_limit(300, MEDIA_TYPES.keys())
        self.assertAlmostEqual(shares['online'], shares['print'], delta=1)
        self.assertAlmostEqual(shares['online'], shares['competitor'], delta=2)

    def test_shares_sum_to_limit(self):
        shares = _split_limit(300, MEDIA_TYPES.keys())
        self.assertEqual(sum(shares.values()), 300)

    def test_every_type_gets_at_least_one(self):
        # A tiny limit shouldn't starve any type to zero.
        shares = _split_limit(5, MEDIA_TYPES.keys())
        for key in MEDIA_TYPES:
            self.assertGreaterEqual(shares[key], 1)

    def test_single_type_gets_full_limit(self):
        shares = _split_limit(50, ['online'])
        self.assertEqual(shares['online'], 50)

    def test_unknown_key_defaults_to_weight_one(self):
        # _split_limit is defensive against a key with no TYPE_WEIGHTS entry
        # (shouldn't happen via the command's own --media-type validation,
        # but the function itself shouldn't KeyError).
        shares = _split_limit(90, ['online', 'made_up_type'])
        self.assertIn('made_up_type', shares)
        self.assertGreaterEqual(shares['made_up_type'], 1)
