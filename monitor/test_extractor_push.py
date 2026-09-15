"""Tests for the Newspaper Extractor "push to Social Light" webhook
(monitor/views.py:extractor_push_webhook) — the receiving half of
article-extractor's My Extracts panel. Modeled on the same conventions as
test_extractor_sso.py and the existing print_cover_webhook tests.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from monitor.models import Organization, PrintArticle

SECRET = 'test-extractor-push-secret'


@override_settings(EXTRACTOR_PUSH_WEBHOOK_SECRET=SECRET)
class ExtractorPushWebhookTests(TestCase):

    def setUp(self):
        self.org = Organization.objects.create(name='Push Target Org')
        self.url = reverse('monitor:extractor_push_webhook', args=[self.org.id])

    def _post(self, secret=SECRET, **fields):
        payload = {
            'headline': 'FNBB launches new savings product',
            'source': 'Mmegi',
            'section': 'Business',
            'author': 'A Reporter',
            'date_published': '2026-08-15',
            'sentiment': 'positive',
            'sentiment_rationale': 'Positive coverage of a new product launch.',
            'summary': 'A brief AI summary of the article.',
            'ave': '4500.50',
            'reach': '12000',
        }
        payload.update(fields)
        headers = {}
        if secret is not None:
            headers['HTTP_X_WEBHOOK_SECRET'] = secret
        return self.client.post(self.url, payload, **headers)

    def test_a_valid_push_creates_a_print_article(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

        article = PrintArticle.objects.get(organization=self.org)
        self.assertEqual(article.headline, 'FNBB launches new savings product')
        self.assertEqual(article.source, 'Mmegi')
        self.assertEqual(article.sentiment, 'positive')
        self.assertEqual(article.sentiment_rationale, 'Positive coverage of a new product launch.')
        self.assertEqual(article.summary, 'A brief AI summary of the article.')
        self.assertEqual(float(article.ave), 4500.50)
        self.assertEqual(article.reach, 12000)
        # Deliberately bypasses the relevancy gate print_cover_webhook uses —
        # the article was already keyword-matched on the extractor side.
        self.assertEqual(article.relevancy, 100)

    def test_wrong_secret_is_refused(self):
        response = self._post(secret='not-the-real-secret')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PrintArticle.objects.filter(organization=self.org).exists())

    def test_missing_secret_is_refused(self):
        response = self._post(secret=None)
        self.assertEqual(response.status_code, 403)

    def test_missing_headline_is_rejected(self):
        response = self._post(headline='')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PrintArticle.objects.filter(organization=self.org).exists())

    def test_an_invalid_sentiment_falls_back_to_neutral(self):
        self._post(sentiment='not-a-real-sentiment')
        article = PrintArticle.objects.get(organization=self.org)
        self.assertEqual(article.sentiment, 'neutral')

    def test_wrong_organisation_id_404s(self):
        import uuid
        bad_url = reverse('monitor:extractor_push_webhook', args=[uuid.uuid4()])
        response = self.client.post(bad_url, {'headline': 'x'},
                                    HTTP_X_WEBHOOK_SECRET=SECRET)
        self.assertEqual(response.status_code, 404)

    @override_settings(EXTRACTOR_PUSH_WEBHOOK_SECRET='')
    def test_an_empty_configured_secret_disables_the_check(self):
        """Matches print_cover_webhook/media_monitor_webhook's own behaviour
        — an unconfigured secret means the check is skipped, not that every
        request is rejected."""
        response = self._post(secret=None)
        self.assertEqual(response.status_code, 200)
