"""
Tests for the event capture layer: emitters write Event rows, gather_events reads
them back with the right scoping, and build_and_send folds them into the digest.

The email assertions run against Django's in-memory backend, so nothing here
touches Resend, Celery or the development database.
"""
import json
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from monitor.alert_email import build_and_send, gather, gather_events
from monitor.models import Alert, Event, Organization, User

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
