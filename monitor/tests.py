"""
Tests for the event capture layer: emitters write Event rows, gather_events reads
them back with the right scoping, and build_and_send folds them into the digest.

The email assertions run against Django's in-memory backend, so nothing here
touches a real mail server, Celery or the development database.
"""
import json
from datetime import datetime, time, timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from monitor.alert_email import (
    build_and_send, daily_alert_due, gather, gather_events, start_of_today,
)
from monitor.models import Alert, Event, Organization, OnlineArticle, User

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def make_event(org, **kwargs):
    """An Event with sensible defaults, so each test only states what it cares about."""
    defaults = {
        'category': 'report',
        'event_type': 'report_generated',
        'title': 'New report ready',
        'summary': 'A report was generated.',
    }
    return Event.objects.create(organization=org, **{**defaults, **kwargs})


class EventCaptureTests(TestCase):
    """The emit side: generating a report records an Event."""

    def setUp(self):
        self.org = Organization.objects.create(name='Test Org', country='Botswana')
        self.user = User.objects.create_user(
            username='tester', email='tester@example.com', password='pw-for-tests',
            role='platform_admin',
        )
        self.client.force_login(self.user)

    def test_saving_a_report_captures_an_event(self):
        url = reverse('monitor:report_save', args=[self.org.id])
        response = self.client.post(
            url,
            data=json.dumps({'report_type': 'Custom Report', 'modules': ['sentiment']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        event = Event.objects.get(organization=self.org)
        self.assertEqual(event.category, 'report')
        self.assertEqual(event.event_type, 'report_generated')
        self.assertIn('New report ready', event.title)
        self.assertTrue(event.url, 'the event should deep-link back to the reports page')

    def test_report_save_captures_exactly_one_event_per_report(self):
        url = reverse('monitor:report_save', args=[self.org.id])
        payload = json.dumps({'report_type': 'Custom Report', 'modules': []})
        for _ in range(3):
            self.client.post(url, data=payload, content_type='application/json')

        self.assertEqual(Event.objects.filter(organization=self.org).count(), 3)


class GatherEventsTests(TestCase):
    """The read side: gather_events scopes by org, watermark and category."""

    def setUp(self):
        self.org = Organization.objects.create(name='Ours', country='Botswana')
        self.other_org = Organization.objects.create(name='Theirs', country='Botswana')
        self.now = timezone.now()
        self.since = self.now - timedelta(minutes=5)

    def test_returns_events_for_the_organization_only(self):
        mine = make_event(self.org, title='Mine')
        make_event(self.other_org, title='Theirs')

        events = gather_events(self.org, self.since)

        self.assertEqual([e.id for e in events], [mine.id])

    def test_excludes_events_older_than_the_watermark(self):
        stale = make_event(self.org, title='Stale')
        Event.objects.filter(pk=stale.pk).update(created_at=self.now - timedelta(days=2))
        fresh = make_event(self.org, title='Fresh')

        events = gather_events(self.org, self.since)

        self.assertEqual([e.id for e in events], [fresh.id])

    def test_filters_by_category_when_given(self):
        report = make_event(self.org, category='report', title='Report')
        make_event(self.org, category='system', event_type='maintenance', title='System')

        events = gather_events(self.org, self.since, categories=['report'])

        self.assertEqual([e.id for e in events], [report.id])

    def test_empty_categories_means_every_category(self):
        make_event(self.org, category='report', title='Report')
        make_event(self.org, category='system', event_type='maintenance', title='System')

        self.assertEqual(len(gather_events(self.org, self.since, categories=[])), 2)
        self.assertEqual(len(gather_events(self.org, self.since, categories=None)), 2)

    def test_newest_event_comes_first(self):
        first = make_event(self.org, title='First')
        Event.objects.filter(pk=first.pk).update(created_at=self.now - timedelta(minutes=1))
        second = make_event(self.org, title='Second')

        self.assertEqual([e.id for e in gather_events(self.org, self.since)],
                         [second.id, first.id])


@override_settings(EMAIL_BACKEND=LOCMEM)
class DigestIncludesEventsTests(TestCase):
    """The delivery side: captured events reach the digest email."""

    def setUp(self):
        self.org = Organization.objects.create(name='Digest Org', country='Botswana')
        self.alert = Alert.objects.create(
            organization=self.org, name='Daily digest',
            recipients='someone@example.com', frequency='immediate',
        )
        self.since = timezone.now() - timedelta(minutes=5)

    def test_an_event_alone_is_enough_to_send_an_immediate_digest(self):
        """With no mentions at all, a single captured event still triggers delivery —
        this is the behaviour the whole layer exists to provide."""
        make_event(self.org, title='Quarterly analysis ready')

        result = build_and_send(self.alert, since=self.since)

        self.assertTrue(result['sent'])
        self.assertEqual(result['total'], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Quarterly analysis ready', mail.outbox[0].alternatives[0][0])

    def test_immediate_digest_is_skipped_when_nothing_was_captured(self):
        result = build_and_send(self.alert, since=self.since)

        self.assertFalse(result['sent'])
        self.assertEqual(result['reason'], 'no new records')
        self.assertEqual(len(mail.outbox), 0)

    def test_alert_categories_filter_which_events_are_delivered(self):
        self.alert.categories = ['system']
        self.alert.save(update_fields=['categories'])
        make_event(self.org, category='report', title='Report event')

        result = build_and_send(self.alert, since=self.since)

        self.assertFalse(result['sent'], 'a report event should not satisfy a system-only alert')
        self.assertEqual(len(mail.outbox), 0)

    def test_a_successful_send_advances_the_watermark(self):
        make_event(self.org, title='Watermark event')

        build_and_send(self.alert, since=self.since)

        self.alert.refresh_from_db()
        self.assertIsNotNone(self.alert.last_sent_at)


class AlertCategoryTests(TestCase):
    """Alert.wants_category and its effect on raw mention collection."""

    def setUp(self):
        self.org = Organization.objects.create(name='Category Org', country='Botswana')
        self.alert = Alert.objects.create(
            organization=self.org, name='Reports only',
            recipients='someone@example.com', frequency='daily',
        )

    def test_empty_categories_wants_everything(self):
        for category in ('mention', 'report', 'system'):
            self.assertTrue(self.alert.wants_category(category))

    def test_listed_categories_are_exclusive(self):
        self.alert.categories = ['report']

        self.assertTrue(self.alert.wants_category('report'))
        self.assertFalse(self.alert.wants_category('mention'))

    def test_excluding_mentions_empties_the_raw_coverage_lists(self):
        self.alert.categories = ['report']
        since = timezone.now() - timedelta(minutes=5)

        online, print_arts, social, broadcast = gather(self.org, since, self.alert)

        self.assertEqual([online, print_arts, social, broadcast], [[], [], [], []])


@override_settings(MENTION_RELEVANCY_THRESHOLD=0)
class GatherPublishAgeGraceTests(TestCase):
    """gather()'s max_publish_age_days guard, exercised directly. 0 is still a
    special case (exact date_published match, used to be the 'daily' default —
    see DailySlotTests below for what daily uses now); a positive value is a
    grace window, not a strict bound, so a several-day-old publish date can
    still pass. Callers choose the value; gather() itself is unchanged."""

    def setUp(self):
        self.org = Organization.objects.create(name='Scope Org', country='Botswana')
        self.alert = Alert.objects.create(
            organization=self.org, name='Daily digest',
            recipients='someone@example.com', frequency='daily',
        )
        self.today = timezone.localdate()
        self.yesterday = self.today - timedelta(days=1)

    def _article(self, date_published, headline):
        return OnlineArticle.objects.create(
            organization=self.org, headline=headline, url='https://example.com/' + headline,
            date_published=date_published, relevancy=100,
        )

    def test_exact_zero_excludes_yesterdays_article(self):
        self._article(self.yesterday, 'Yesterday piece')
        self._article(self.today, 'Today piece')

        online, _, _, _ = gather(self.org, start_of_today(), self.alert, max_publish_age_days=0)

        self.assertEqual([a.headline for a in online], ['Today piece'])

    def test_grace_window_includes_yesterdays_article(self):
        self._article(self.yesterday, 'Yesterday piece')
        self._article(self.today, 'Today piece')

        online, _, _, _ = gather(self.org, start_of_today() - timedelta(days=1), self.alert,
                                  max_publish_age_days=3)

        self.assertEqual({a.headline for a in online}, {'Yesterday piece', 'Today piece'})


@override_settings(MENTION_RELEVANCY_THRESHOLD=0)
class DailySlotTests(TestCase):
    """Daily alerts fire twice a day at fixed times, 08:00 + 15:00 CAT
    (DAILY_SLOT_TIMES), each covering everything since the previous slot via
    the last_sent_at watermark — replacing the old exact-date_published,
    always-since-midnight design (2026-09-14, Tony): that design silently and
    permanently dropped relevant coverage whenever a source's own
    date_published lagged its ingestion date (see the 2026-09-14 audit). A
    missed slot now self-heals: the next due slot's `since` still reaches back
    to the last successful send, so nothing in the gap is lost."""

    def setUp(self):
        self.org = Organization.objects.create(name='Slot Org', country='Botswana')
        self.alert = Alert.objects.create(
            organization=self.org, name='Daily digest',
            recipients='someone@example.com', frequency='daily',
        )
        self.today = timezone.localdate()

    def _at(self, hour, minute=0):
        return timezone.make_aware(datetime.combine(self.today, time(hour, minute)))

    def _article(self, date_published, headline):
        return OnlineArticle.objects.create(
            organization=self.org, headline=headline, url='https://example.com/' + headline,
            date_published=date_published, relevancy=100,
        )

    def test_not_due_before_the_morning_slot(self):
        self.assertFalse(daily_alert_due(self.alert, self._at(7, 59)))

    def test_due_at_the_morning_slot(self):
        self.assertTrue(daily_alert_due(self.alert, self._at(8, 0)))

    def test_not_due_again_between_slots_once_the_morning_one_was_sent(self):
        self.alert.last_sent_at = self._at(8, 0)
        self.assertFalse(daily_alert_due(self.alert, self._at(12, 0)))

    def test_due_at_the_afternoon_slot_after_the_morning_one_was_sent(self):
        self.alert.last_sent_at = self._at(8, 0)
        self.assertTrue(daily_alert_due(self.alert, self._at(15, 0)))

    def test_missed_morning_slot_is_still_due_and_caught_up_by_the_afternoon_run(self):
        """If the 08:00 run never sent (e.g. mail outage), last_sent_at is still
        stuck at yesterday afternoon's send — the 15:00 run is due, and one email
        covers the whole gap back to that watermark, rather than losing the
        missed slot's coverage."""
        yesterday_afternoon = self._at(15, 0) - timedelta(days=1)
        self.alert.last_sent_at = yesterday_afternoon
        self.alert.save()
        self._article(self.today - timedelta(days=1), 'Late last night')
        self._article(self.today, 'This morning')

        self.assertTrue(daily_alert_due(self.alert, self._at(15, 0)))

        online, _, _, _ = gather(self.org, yesterday_afternoon, self.alert, max_publish_age_days=3)
        self.assertEqual({a.headline for a in online}, {'Late last night', 'This morning'})

    def test_send_daily_alerts_catches_up_a_stale_watermark_instead_of_dropping_it(self):
        """The inverse of the old exact-day behavior: a stale last_sent_at (a
        previous send that failed) now means the next real send reports
        everything back to that watermark, not just the current day.

        Uses --alert (targets this one alert directly) rather than --frequency
        daily, since the latter's due-check depends on the real wall clock
        relative to the fixed 08:00/15:00 slots — daily_alert_due()'s own slot
        logic is covered deterministically by the tests above."""
        self._article(self.today - timedelta(days=1), 'Old backlog piece')
        self._article(self.today, 'Today piece')
        self.alert.last_sent_at = timezone.now() - timedelta(days=1, hours=1)
        self.alert.save()

        with override_settings(EMAIL_BACKEND=LOCMEM):
            from django.core.management import call_command
            call_command('send_daily_alerts', alert=str(self.alert.id))

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].alternatives[0][0]
        self.assertIn('Today piece', body)
        self.assertIn('Old backlog piece', body)
