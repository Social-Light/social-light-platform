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


class VisitCountTests(TestCase):
    """Counting everyone who arrived, not only the few who converted."""

    def setUp(self):
        from .visit_models import VisitCount
        self.VisitCount = VisitCount

    def test_a_visit_is_counted_against_its_channel(self):
        Client().get('/?utm_source=facebook&utm_medium=organic&utm_campaign=radio-post')
        row = self.VisitCount.objects.get()
        self.assertEqual(row.channel, 'meta')
        self.assertEqual(row.utm_source, 'facebook')
        self.assertEqual(row.visits, 1)

    def test_one_person_reading_five_pages_counts_once(self):
        """The question is how many people came, not how many pages they read."""
        client = Client()
        client.get('/?utm_source=facebook')
        client.get(reverse('monitor:assessment'))
        client.get(reverse('monitor:pricing'))
        client.get('/')
        self.assertEqual(self.VisitCount.objects.get().visits, 1)

    def test_separate_visitors_increment_the_same_bucket(self):
        for _ in range(3):
            Client().get('/?utm_source=facebook&utm_medium=organic')
        self.assertEqual(self.VisitCount.objects.count(), 1)
        self.assertEqual(self.VisitCount.objects.get().visits, 3)

    def test_different_sources_are_counted_separately(self):
        Client().get('/?utm_source=facebook&utm_medium=organic')
        Client().get('/?utm_source=instagram&utm_medium=bio')
        Client().get('/')
        self.assertEqual(self.VisitCount.objects.count(), 3)
        # Facebook and Instagram are different sources but one channel, which is
        # what lets "how many from Meta" be one number and still break down.
        meta = self.VisitCount.objects.filter(channel='meta')
        self.assertEqual(meta.count(), 2)
        self.assertEqual(sum(r.visits for r in meta), 2)

    def test_untagged_visit_counts_as_direct(self):
        Client().get('/')
        self.assertEqual(self.VisitCount.objects.get().channel, 'direct')

    def test_facebook_referrer_counts_as_meta_without_any_tag(self):
        """Links already posted cannot be re-tagged, so the referrer has to carry them."""
        Client().get('/', HTTP_REFERER='https://l.facebook.com/')
        self.assertEqual(self.VisitCount.objects.get().channel, 'meta')

    def test_crawlers_are_not_counted_as_people(self):
        Client().get('/', HTTP_USER_AGENT='facebookexternalhit/1.1')
        Client().get('/', HTTP_USER_AGENT='Mozilla/5.0 (compatible; Googlebot/2.1)')
        self.assertEqual(self.VisitCount.objects.count(), 0)

    def test_api_calls_are_not_counted(self):
        client = Client()
        submit(client)
        self.assertEqual(self.VisitCount.objects.count(), 0)

    def test_counting_never_costs_the_visitor_their_page(self):
        with patch('monitor.visit_models.VisitCount.objects.get_or_create',
                   side_effect=OSError('database gone')):
            page = Client().get('/')
        self.assertEqual(page.status_code, 200)

    def test_visits_and_leads_share_one_channel_vocabulary(self):
        """The two tables are only comparable if the buckets mean the same thing."""
        client = Client()
        client.get('/?utm_source=facebook&utm_medium=cpc&utm_campaign=q3')
        submit(client)
        self.assertEqual(self.VisitCount.objects.get().channel,
                         AssessmentSubmission.objects.get().channel)

    def test_summary_renders_for_an_admin(self):
        from django.contrib.auth import get_user_model

        Client().get('/?utm_source=facebook&utm_medium=organic')
        admin_user = get_user_model().objects.create_superuser(
            username='visits-admin', email='visits@example.com', password='pw-for-test-only')
        client = Client()
        client.force_login(admin_user)
        page = client.get('/admin/monitor/visitcount/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Where the traffic came from')


class VisitCountExclusionTests(TestCase):
    """What must never be counted as a marketing visit."""

    def setUp(self):
        from .visit_models import VisitCount
        self.VisitCount = VisitCount

    def test_the_application_area_is_not_counted(self):
        """A client working inside the platform is not traffic a campaign bought."""
        Client().get('/app/dashboard/')
        self.assertEqual(self.VisitCount.objects.count(), 0)

    def test_signed_in_users_are_not_counted(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username='client-user', email='client@example.com', password='pw-for-test-only')
        client = Client()
        client.force_login(user)
        client.get('/')
        self.assertEqual(self.VisitCount.objects.count(), 0)

    def test_an_anonymous_visitor_is_still_counted(self):
        Client().get('/?utm_source=facebook')
        self.assertEqual(self.VisitCount.objects.count(), 1)


# Real strings, shortened. The markers are what matter.
IG_APP = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 '
          '(KHTML, like Gecko) Mobile/15E148 Instagram 334.0.3.28.96 (iPhone14,3)')
FB_APP = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 '
          '(KHTML, like Gecko) Mobile/15E148 [FBAN/FBIOS;FBAV/456.0.0.32.108]')
CHROME = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


class InAppBrowserTests(TestCase):
    """Recovering the visits that arrive with no referrer at all.

    An Instagram bio click sends no referrer, so without this it is
    indistinguishable from someone typing the address — and the account looks
    like it sends nobody. Meta's apps name themselves in the User-Agent, which
    is the only signal left.
    """

    def setUp(self):
        from .visit_models import VisitCount
        self.VisitCount = VisitCount

    def test_instagram_app_is_recognised(self):
        self.assertEqual(attribution.in_app_source(IG_APP), 'instagram')

    def test_facebook_app_is_recognised(self):
        self.assertEqual(attribution.in_app_source(FB_APP), 'facebook')

    def test_an_ordinary_browser_is_not_an_app(self):
        self.assertEqual(attribution.in_app_source(CHROME), '')

    def test_untagged_instagram_visit_is_no_longer_lost_to_direct(self):
        Client().get('/', HTTP_USER_AGENT=IG_APP)
        row = self.VisitCount.objects.get()
        self.assertEqual(row.channel, 'meta')
        self.assertEqual(row.utm_source, 'instagram')
        self.assertEqual(row.utm_medium, 'in-app')

    def test_untagged_facebook_app_visit_is_attributed(self):
        Client().get('/', HTTP_USER_AGENT=FB_APP)
        self.assertEqual(self.VisitCount.objects.get().channel, 'meta')

    def test_an_ordinary_untagged_visit_is_still_direct(self):
        """Detection must not start claiming visitors it has no evidence for."""
        Client().get('/', HTTP_USER_AGENT=CHROME)
        self.assertEqual(self.VisitCount.objects.get().channel, 'direct')

    def test_a_tag_beats_the_user_agent(self):
        """The tag is the better evidence: it says which link, not just which app."""
        Client().get('/?utm_source=instagram&utm_medium=bio', HTTP_USER_AGENT=IG_APP)
        row = self.VisitCount.objects.get()
        self.assertEqual(row.utm_medium, 'bio')

    def test_a_referrer_beats_the_user_agent(self):
        Client().get('/', HTTP_REFERER='https://mmegi.bw/story', HTTP_USER_AGENT=IG_APP)
        self.assertEqual(self.VisitCount.objects.get().channel, 'referral')

    def test_internal_referrer_does_not_mask_the_app(self):
        """An in-app visitor moving between our own pages is still from the app."""
        self.assertEqual(
            attribution.channel('', 'https://sociallight.africa/pricing/',
                                'sociallight.africa', IG_APP),
            'meta',
        )

    def test_in_app_source_carries_through_to_the_lead(self):
        client = Client(HTTP_USER_AGENT=IG_APP)
        client.get('/')
        submit(client)
        lead = AssessmentSubmission.objects.get()
        self.assertEqual(lead.channel, 'meta')
        self.assertEqual(lead.utm_source, 'instagram')

    def test_metas_link_scraper_is_still_treated_as_a_bot(self):
        """The crawler that fetches share previews must not count as a visitor."""
        Client().get('/', HTTP_USER_AGENT='facebookexternalhit/1.1')
        self.assertEqual(self.VisitCount.objects.count(), 0)


class MarketingPageTests(TestCase):
    """The in-app page, so reading these numbers needs no Django admin account."""

    def setUp(self):
        from django.contrib.auth import get_user_model

        from .models import Organization
        self.User = get_user_model()
        self.org = Organization.objects.create(name='Social Light')
        self.url = reverse('monitor:marketing')

    def _user(self, role, **kwargs):
        user = self.User.objects.create_user(
            username=f'{role}-{self.User.objects.count()}',
            email=f'{role}@example.com', password='pw-for-test-only',
            role=role, organization=self.org, email_verified=True, **kwargs)
        return user

    def test_platform_admin_can_open_it(self):
        client = Client()
        client.force_login(self._user('platform_admin'))
        page = client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Where visitors come from')

    def test_a_client_user_cannot(self):
        """These are Social Light's own numbers, not a client's coverage."""
        client = Client()
        client.force_login(self._user('viewer'))
        page = client.get(self.url)
        self.assertEqual(page.status_code, 302)
        self.assertNotIn('marketing', page['Location'])

    def test_an_anonymous_visitor_cannot(self):
        page = Client().get(self.url)
        self.assertEqual(page.status_code, 302)

    def test_it_reports_visits_and_leads_together(self):
        visitor = Client()
        visitor.get('/?utm_source=facebook&utm_medium=cpc&utm_campaign=q3-awareness')
        submit(visitor)

        client = Client()
        client.force_login(self._user('platform_admin'))
        page = client.get(self.url)
        self.assertContains(page, 'meta')
        self.assertContains(page, 'q3-awareness')

    def test_the_admin_browsing_the_page_is_not_counted_as_a_visitor(self):
        """Reading the numbers must not change them."""
        from .visit_models import VisitCount

        client = Client()
        client.force_login(self._user('platform_admin'))
        client.get(self.url)
        self.assertEqual(VisitCount.objects.count(), 0)

    def test_an_empty_period_says_so_rather_than_breaking(self):
        client = Client()
        client.force_login(self._user('platform_admin'))
        page = client.get(self.url + '?days=7')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'No visits recorded')

    def test_a_nonsense_day_range_falls_back_instead_of_erroring(self):
        client = Client()
        client.force_login(self._user('platform_admin'))
        for value in ('abc', '-5', '99999', ''):
            page = client.get(self.url + f'?days={value}')
            self.assertEqual(page.status_code, 200, value)


class ReadOnlyAdminAccountTests(TestCase):
    """A staff account holding only the two 'view' permissions.

    Worth testing rather than assuming: the assessment list is configured with
    an editable status column, and a changelist that raises for a user without
    change permission would send whoever reads these numbers into a 500 rather
    than a page.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.contrib.auth.models import Group, Permission

        group = Group.objects.create(name='Marketing (read-only)')
        group.permissions.set(Permission.objects.filter(
            content_type__app_label='monitor',
            codename__in=['view_visitcount', 'view_assessmentsubmission']))

        self.user = get_user_model().objects.create_user(
            username='marketing-reader', email='marketing@example.com',
            password='pw-for-test-only', is_staff=True, email_verified=True)
        self.user.groups.add(group)

        self.client = Client()
        self.client.force_login(self.user)

    def test_they_can_read_the_visit_counts(self):
        page = self.client.get('/admin/monitor/visitcount/')
        self.assertEqual(page.status_code, 200)

    def test_they_can_read_the_leads(self):
        page = self.client.get('/admin/monitor/assessmentsubmission/')
        self.assertEqual(page.status_code, 200)

    def test_they_see_only_those_two_things_on_the_admin_index(self):
        page = self.client.get('/admin/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Visit counts')
        self.assertContains(page, 'Assessment submissions')
        self.assertNotContains(page, 'Organizations')
        self.assertNotContains(page, 'Legal documents')

    def test_they_cannot_change_a_lead(self):
        Client().get('/?utm_source=facebook')
        submit(Client())
        lead = AssessmentSubmission.objects.first()
        page = self.client.post(
            f'/admin/monitor/assessmentsubmission/{lead.pk}/change/', {'status': 'closed'})
        self.assertIn(page.status_code, (302, 403))
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'new')

    def test_they_cannot_delete_a_visit_count(self):
        from .visit_models import VisitCount

        Client().get('/?utm_source=facebook')
        row = VisitCount.objects.get()
        self.client.post(f'/admin/monitor/visitcount/{row.pk}/delete/', {'post': 'yes'})
        self.assertTrue(VisitCount.objects.filter(pk=row.pk).exists())
