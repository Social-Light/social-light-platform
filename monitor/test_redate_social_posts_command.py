"""
Tests for monitor/management/commands/redate_social_posts.py's --month
scoping. Uses the real local SQLite test DB (Organization/SocialMediaPost
are native models here, not platform_sync mirrors) — no AI calls needed
since these only exercise the queryset filtering, with extract_published_date
mocked to a no-op.
"""
from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from monitor.models import Organization, SocialMediaPost


def _make_post(org, headline, created_at, date_published=None):
    post = SocialMediaPost.objects.create(
        organization=org, headline=headline, summary='',
        date_published=date_published or created_at.date(),
    )
    # created_at is auto_now_add — overwrite directly, same trick tests_bridge.py
    # style fixtures would use for a fixed timestamp.
    SocialMediaPost.objects.filter(pk=post.pk).update(created_at=created_at)
    post.refresh_from_db()
    return post


class RedateMonthScopingTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Test Org')

    def test_invalid_month_format_raises(self):
        with self.assertRaises(CommandError):
            call_command('redate_social_posts', month='not-a-month')

    def test_invalid_month_number_raises(self):
        with self.assertRaises(CommandError):
            call_command('redate_social_posts', month='2026-13')

    @patch('monitor.management.commands.redate_social_posts.extract_published_date',
          return_value=None)
    def test_month_filters_to_that_months_ingested_rows(self, mock_extract):
        from django.utils import timezone
        tz = timezone.get_current_timezone()
        _make_post(self.org, 'August post', timezone.datetime(2026, 8, 15, tzinfo=tz))
        _make_post(self.org, 'September post', timezone.datetime(2026, 9, 1, tzinfo=tz))

        call_command('redate_social_posts', month='2026-08', dry_run=True)
        # Only the August post should have been processed — assert via the
        # mock's call args rather than stdout, since dry_run prints nothing
        # when extract_published_date returns None (nothing changed).
        processed_headlines = [c.args[0] for c in mock_extract.call_args_list]
        self.assertEqual(processed_headlines, ['August post'])
