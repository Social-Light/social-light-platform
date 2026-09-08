"""Attribution, the cookie banner and the social meta tags.

The thing being protected here is the claim "this many leads came from Meta".
That claim is only worth making if the source survives the whole visit — the
landing page, a wander through pricing, and ten questions later — so most of
these tests walk a journey rather than posting straight to the endpoint.
"""
import json
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from . import attribution, meta_pixel
from .assessment_models import AssessmentSubmission


ANSWERS = {'monitoring': 0, 'coverage': 0, 'speed': 0}
CONTACT = {
    'first_name': 'Kagiso', 'last_name': 'Moeng', 'email': 'kagiso@example.com',
    'company': 'Ministry of Health', 'industry': 'Government',
}


def submit(client):
    return client.post(
        reverse('monitor:assessment_submit'),
        data=json.dumps({'contact': CONTACT, 'answers': ANSWERS}),
        content_type='application/json',
    )


class ChannelTests(TestCase):
    """The bucketing that turns a URL into a countable channel."""

    def test_explicit_utm_source_wins(self):
        self.assertEqual(attribution.channel('meta', 'https://google.com/'), 'meta')

    def test_facebook_referrer_is_meta(self):
        self.assertEqual(attribution.channel('', 'https://l.facebook.com/l.php?u=x'), 'meta')

    def test_instagram_referrer_is_meta(self):
        """The channel has to survive the platform not naming itself."""
        self.assertEqual(attribution.channel('', 'https://l.instagram.com/'), 'meta')

    def test_no_referrer_is_direct(self):
        self.assertEqual(attribution.channel('', ''), 'direct')

    def test_own_site_referrer_is_direct_not_referral(self):
        """An internal link must not be counted as a new acquisition."""
        self.assertEqual(
            attribution.channel('', 'https://sociallight.africa/pricing/', 'sociallight.africa'),
            'direct',
        )

    def test_unknown_referrer_is_referral(self):
        self.assertEqual(attribution.channel('', 'https://mmegi.bw/story'), 'referral')


class AttributionCaptureTests(TestCase):

    def test_campaign_survives_the_journey_to_submission(self):
        """The whole point: tags are on the landing URL, the lead is three pages later."""
        client = Client()
        client.get('/?utm_source=meta&utm_medium=cpc&utm_campaign=q3-awareness'
                   '&utm_content=carousel-a&fbclid=IwAR123')
        client.get(reverse('monitor:pricing'))
        client.get(reverse('monitor:assessment'))
        submit(client)

        lead = AssessmentSubmission.objects.get()
        self.assertEqual(lead.channel, 'meta')
        self.assertEqual(lead.utm_source, 'meta')
        self.assertEqual(lead.utm_medium, 'cpc')
        self.assertEqual(lead.utm_campaign, 'q3-awareness')
        self.assertEqual(lead.utm_content, 'carousel-a')
        self.assertEqual(lead.click_id, 'IwAR123')
        self.assertEqual(lead.landing_path, '/')

    def test_first_touch_is_not_overwritten_by_internal_navigation(self):
        client = Client()
        client.get('/?utm_source=meta&utm_campaign=q3-awareness')
        client.get(reverse('monitor:assessment'))       # no tags on this one
        submit(client)
        self.assertEqual(AssessmentSubmission.objects.get().utm_campaign, 'q3-awareness')

    def test_a_second_campaign_reattributes_the_visit(self):
        """A later ad click is credited to the later ad, not the earlier one."""
        client = Client()
        client.get('/?utm_source=meta&utm_campaign=first')
        client.get('/?utm_source=linkedin&utm_campaign=second')
        submit(client)
        lead = AssessmentSubmission.objects.get()
        self.assertEqual(lead.utm_campaign, 'second')
        self.assertEqual(lead.channel, 'linkedin')

    def test_untagged_facebook_click_is_still_attributed_to_meta(self):
        """Most real ad clicks arrive with fbclid and no utm tags at all."""
        client = Client()
        client.get('/?fbclid=IwAR999', HTTP_REFERER='https://l.facebook.com/')
        submit(client)
        lead = AssessmentSubmission.objects.get()
        self.assertEqual(lead.channel, 'meta')
        self.assertEqual(lead.click_id, 'IwAR999')

    def test_untagged_visit_is_direct(self):
        client = Client()
        client.get(reverse('monitor:assessment'))
        submit(client)
        self.assertEqual(AssessmentSubmission.objects.get().channel, 'direct')

    def test_absurdly_long_campaign_value_is_truncated(self):
        """Anything off a query string is typed by a stranger."""
        client = Client()
        client.get('/?utm_campaign=' + 'x' * 5000)
        submit(client)
        self.assertEqual(len(AssessmentSubmission.objects.get().utm_campaign),
                         attribution.MAX_VALUE_LENGTH)

    def test_source_label_reads_as_one_line(self):
        client = Client()
        client.get('/?utm_source=meta&utm_medium=cpc&utm_campaign=q3')
        submit(client)
        self.assertEqual(AssessmentSubmission.objects.get().source_label, 'meta · cpc · q3')


class ConsentTests(TestCase):
    """No third-party script reaches a visitor who has not agreed to one."""

    def test_no_banner_and_no_pixel_when_nothing_is_configured(self):
        page = self.client.get(reverse('monitor:assessment'))
        self.assertNotContains(page, 'sl-cookie-banner')
        self.assertNotContains(page, 'connect.facebook.net')

    def test_banner_shows_once_a_pixel_is_configured(self):
        with self.settings(META_PIXEL_ID='123456'):
            page = self.client.get(reverse('monitor:assessment'))
        self.assertContains(page, 'sl-cookie-banner')

    def test_pixel_does_not_load_before_consent(self):
        """Configured is not the same as allowed."""
        with self.settings(META_PIXEL_ID='123456'):
            page = self.client.get(reverse('monitor:assessment'))
        self.assertNotContains(page, 'connect.facebook.net')

    def test_pixel_loads_only_after_consent(self):
        self.client.cookies[meta_pixel.CONSENT_COOKIE] = 'all'
        with self.settings(META_PIXEL_ID='123456'):
            page = self.client.get(reverse('monitor:assessment'))
        self.assertContains(page, 'connect.facebook.net')
        self.assertContains(page, '123456')
        self.assertNotContains(page, 'sl-cookie-banner')

    def test_declining_keeps_the_pixel_out_and_stops_the_asking(self):
        self.client.cookies[meta_pixel.CONSENT_COOKIE] = 'essential'
        with self.settings(META_PIXEL_ID='123456'):
            page = self.client.get(reverse('monitor:assessment'))
        self.assertNotContains(page, 'connect.facebook.net')
        self.assertNotContains(page, 'sl-cookie-banner')

    def test_declining_does_not_break_attribution(self):
        """First-party measurement is the half that must never depend on consent."""
        client = Client()
        client.cookies[meta_pixel.CONSENT_COOKIE] = 'essential'
        client.get('/?utm_source=meta&utm_campaign=q3')
        submit(client)
        self.assertEqual(AssessmentSubmission.objects.get().channel, 'meta')

    def test_cookie_notice_is_published_and_reachable(self):
        """The banner links to it, so a 404 here makes the consent uninformed."""
        page = self.client.get(reverse('monitor:legal_document', args=['cookies']))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Cookie Notice')


class ConversionsApiTests(TestCase):

    def test_nothing_is_sent_when_capi_is_not_configured(self):
        with patch('requests.post') as post:
            submit(Client())
        post.assert_not_called()

    def test_lead_is_reported_with_a_hashed_email_and_never_a_plain_one(self):
        with self.settings(META_PIXEL_ID='123456', META_CAPI_ACCESS_TOKEN='tok'):
            with patch('requests.post') as post:
                post.return_value.status_code = 200
                submit(Client())

        self.assertTrue(post.called)
        body = post.call_args.kwargs['json']['data'][0]
        self.assertEqual(body['event_name'], 'Lead')
        self.assertNotIn(CONTACT['email'], json.dumps(body))
        self.assertEqual(len(body['user_data']['em'][0]), 64)

    def test_browser_and_server_share_one_event_id_so_meta_counts_one_lead(self):
        with self.settings(META_PIXEL_ID='123456', META_CAPI_ACCESS_TOKEN='tok'):
            with patch('requests.post') as post:
                post.return_value.status_code = 200
                response = submit(Client())

        sent = post.call_args.kwargs['json']['data'][0]['event_id']
        self.assertEqual(response.json()['event_id'], sent)

    def test_an_unreachable_meta_does_not_cost_the_lead(self):
        with self.settings(META_PIXEL_ID='123456', META_CAPI_ACCESS_TOKEN='tok'):
            with patch('requests.post', side_effect=OSError('network down')):
                response = submit(Client())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AssessmentSubmission.objects.count(), 1)

    def test_click_id_is_rebuilt_for_a_visitor_whose_pixel_never_ran(self):
        """Blocked in the browser, still attributable from the server."""
        client = Client()
        client.get('/?fbclid=IwAR777')
        with self.settings(META_PIXEL_ID='123456', META_CAPI_ACCESS_TOKEN='tok'):
            with patch('requests.post') as post:
                post.return_value.status_code = 200
                submit(client)
        fbc = post.call_args.kwargs['json']['data'][0]['user_data']['fbc']
        self.assertTrue(fbc.startswith('fb.1.'))
        self.assertTrue(fbc.endswith('.IwAR777'))


class SocialMetaTests(TestCase):
    """A shared link that renders as a blank rectangle is a wasted click."""

    def test_assessment_page_has_open_graph_tags(self):
        page = self.client.get(reverse('monitor:assessment'))
        self.assertContains(page, 'property="og:title"')
        self.assertContains(page, 'property="og:image"')
        self.assertContains(page, 'name="twitter:card"')

    def test_landing_page_has_open_graph_tags(self):
        page = self.client.get(reverse('monitor:home'))
        self.assertContains(page, 'property="og:title"')

    def test_og_image_is_absolute(self):
        """Crawlers do not resolve relative image paths."""
        with self.settings(SITE_URL='https://sociallight.africa'):
            page = self.client.get(reverse('monitor:assessment'))
        self.assertContains(page, 'content="https://sociallight.africa/static/')
