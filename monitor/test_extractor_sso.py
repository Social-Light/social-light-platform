"""Tests for the Newspaper Extractor single sign-on handoff
(monitor/extractor_sso.py). The other half — token verification, account
provisioning, the upload cap — lives in article-extractor's own
users/tests.py; this side only ever needs to prove it mints a correctly
scoped, correctly classified token and nothing else.
"""
from django.conf import settings
from django.core import signing
from django.test import TestCase
from django.urls import reverse

from monitor.extractor_sso import SSO_SIGNING_SALT, _signer
from monitor.models import Organization, Package, User


def _decode(redirect_url):
    token = redirect_url.rstrip('/').rsplit('/sso/', 1)[1]
    return _signer().unsign_object(token, max_age=60)


class ExtractorHandoffTests(TestCase):

    def setUp(self):
        self.org = Organization.objects.create(name='Handoff Org', plan_status='active')
        self.user = User.objects.create_user(
            username='member@handoff.bw', email='member@handoff.bw', password='pw-for-tests-1',
            organization=self.org, role='org_admin')

    def test_an_unauthenticated_request_is_sent_to_login(self):
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('monitor:login'), response.url)

    def test_a_users_own_organisation_mints_a_token(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(f"{settings.ARTICLE_EXTRACTOR_URL.rstrip('/')}/sso/"))

        payload = _decode(response.url)
        self.assertEqual(payload['email'], self.user.email)
        self.assertEqual(payload['org_id'], str(self.org.id))
        self.assertEqual(payload['org_name'], self.org.name)

    def test_another_organisations_handoff_is_refused(self):
        """OrganizationAccessMiddleware's own org-scoping check (present on
        every <uuid:org_id> view) catches this before the view body ever
        runs — the view's own matching check is a defensive backstop for the
        staff/superuser path, which the middleware skips entirely."""
        other_org = Organization.objects.create(name='Someone Elses Org')
        self.client.force_login(self.user)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[other_org.id]))
        self.assertRedirects(response, reverse('monitor:organizations'), fetch_redirect_response=False)

    def test_a_platform_admin_may_mint_a_token_for_any_org(self):
        admin = User.objects.create_user(
            username='admin@sl.africa', email='admin@sl.africa',
            password='pw-for-tests-1', role='platform_admin')
        self.client.force_login(admin)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        self.assertEqual(response.status_code, 302)

    def test_a_legacy_org_with_no_package_is_classified_as_paid(self):
        """Predates self-signup — plan_status='active', package=None — and has
        always had unrestricted access (see entitlements.py)."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        self.assertEqual(_decode(response.url)['plan'], 'active')

    def test_a_real_paid_package_is_classified_as_paid(self):
        self.org.package = Package.objects.create(name='Scale', slug='scale-handoff-test', price=15000)
        self.org.save(update_fields=['package'])
        self.client.force_login(self.user)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        self.assertEqual(_decode(response.url)['plan'], 'active')

    def test_the_free_package_is_classified_as_demo_not_paid(self):
        """'active' isn't 'paid' by itself: an org that self-selected the Free
        tier during onboarding is 'active' too, but gets the same limited
        entitlements a trial does — the extractor handoff must agree."""
        # Seeded into every fresh database by migration 0024 — not created here.
        self.org.package = Package.objects.get(slug='free')
        self.org.save(update_fields=['package'])
        self.client.force_login(self.user)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        self.assertEqual(_decode(response.url)['plan'], 'trial')

    def test_a_live_trial_is_classified_as_demo(self):
        self.org.plan_status = 'trial'
        self.org.save(update_fields=['plan_status'])
        self.client.force_login(self.user)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        self.assertEqual(_decode(response.url)['plan'], 'trial')

    def test_the_token_cannot_be_verified_with_the_wrong_secret(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('monitor:extractor_handoff', args=[self.org.id]))
        token = response.url.rstrip('/').rsplit('/sso/', 1)[1]
        wrong_signer = signing.TimestampSigner(key='not-the-real-secret', salt=SSO_SIGNING_SALT)
        with self.assertRaises(signing.BadSignature):
            wrong_signer.unsign_object(token, max_age=60)
