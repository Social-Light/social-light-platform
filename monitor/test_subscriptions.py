"""Tests for the free-trial lifecycle: public signup, the countdown, the paywall
that closes on day 14, package requests, and the organisation scoping that makes
public signup safe.

Everything here runs against the test database and the in-memory email backend,
so no Resend, Celery or real data is touched.
"""
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from importlib import import_module

from monitor.models import Organization, Package, SubscriptionRequest, User

# The migration module's leading digits make it un-importable with a plain
# `from ... import`, so pull the helper out by name.
retire_placeholders = import_module('monitor.migrations.0012_real_price_list').retire_placeholders

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'

SIGNUP_POST = {
    'contact_name': 'Naledi Mokgadi',
    'email': 'naledi@ministry.co.bw',
    'org_name': 'Ministry of Health',
    'password': 'correct-horse-9',
}


def make_package(**kwargs):
    """A package built from scratch. Migration 0012 writes the real price list
    into every fresh database, including the test one, so tests that care about
    what the price list contains clear it first (see `clear_price_list`)."""
    defaults = {'name': 'Professional', 'slug': 'professional', 'price': 9500,
                'features': ['Print and broadcast monitoring']}
    return Package.objects.create(**{**defaults, **kwargs})


def clear_price_list():
    Package.objects.all().delete()


@override_settings(EMAIL_BACKEND=LOCMEM)
class SignupTests(TestCase):

    def test_signup_creates_org_user_and_starts_a_14_day_trial(self):
        response = self.client.post(reverse('monitor:signup'), SIGNUP_POST)

        org = Organization.objects.get(name='Ministry of Health')
        user = User.objects.get(email='naledi@ministry.co.bw')
        self.assertRedirects(response, reverse('monitor:dashboard', args=[org.id]))
        self.assertEqual(user.organization, org)
        self.assertEqual(user.role, 'org_admin')
        self.assertEqual(user.first_name, 'Naledi')
        self.assertEqual(user.last_name, 'Mokgadi')
        self.assertEqual(org.plan_status, 'trial')
        self.assertEqual(org.trial_days_left, 14)
        self.assertTrue(org.has_platform_access)

    def test_signup_logs_the_new_user_in(self):
        self.client.post(reverse('monitor:signup'), SIGNUP_POST)
        self.assertIn('_auth_user_id', self.client.session)

    def test_signup_emails_the_new_user(self):
        self.client.post(reverse('monitor:signup'), SIGNUP_POST)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['naledi@ministry.co.bw'])
        self.assertIn('14-day', mail.outbox[0].subject)

    def test_password_must_be_ten_characters_with_a_number(self):
        for bad in ('short1', 'no-numbers-here'):
            response = self.client.post(reverse('monitor:signup'), {**SIGNUP_POST, 'password': bad})
            self.assertEqual(response.status_code, 200)
            self.assertIn('password', response.context['errors'])
            self.assertFalse(User.objects.filter(email=SIGNUP_POST['email']).exists())

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user(username='naledi@ministry.co.bw', email='naledi@ministry.co.bw',
                                 password='something-else-1')
        response = self.client.post(reverse('monitor:signup'), SIGNUP_POST)
        self.assertIn('email', response.context['errors'])
        self.assertFalse(Organization.objects.filter(name='Ministry of Health').exists())

    def test_duplicate_organisation_name_is_rejected(self):
        Organization.objects.create(name='ministry of health')
        response = self.client.post(reverse('monitor:signup'), SIGNUP_POST)
        self.assertIn('org_name', response.context['errors'])

    def test_signup_page_offers_a_sign_in_link(self):
        response = self.client.get(reverse('monitor:signup'))
        self.assertContains(response, 'Already have an account?')
        self.assertContains(response, reverse('monitor:login'))


class TrialExpiryTests(TestCase):
    """The clock: a trial flips to expired on read, with no scheduled job."""

    def setUp(self):
        self.org = Organization.objects.create(name='Trial Org')
        self.org.start_trial()
        self.org.save()

    def test_a_fresh_trial_has_access(self):
        self.assertEqual(self.org.effective_plan_status, 'trial')
        self.assertTrue(self.org.has_platform_access)

    def test_part_of_a_day_left_still_reads_as_one_day(self):
        self.org.trial_ends_at = timezone.now() + timedelta(hours=6)
        self.assertEqual(self.org.trial_days_left, 1)
        self.assertTrue(self.org.has_platform_access)

    def test_trial_expires_the_moment_the_window_closes(self):
        self.org.trial_ends_at = timezone.now() - timedelta(seconds=1)
        self.assertEqual(self.org.effective_plan_status, 'expired')
        self.assertEqual(self.org.trial_days_left, 0)
        self.assertFalse(self.org.has_platform_access)

    def test_activating_a_package_restores_access(self):
        clear_price_list()
        self.org.trial_ends_at = timezone.now() - timedelta(days=1)
        self.org.save()
        self.org.activate_package(make_package())
        self.assertEqual(self.org.effective_plan_status, 'active')
        self.assertTrue(self.org.has_platform_access)

    def test_organisations_created_before_self_signup_are_unaffected(self):
        legacy = Organization.objects.create(name='Legacy Client')
        self.assertEqual(legacy.plan_status, 'active')
        self.assertTrue(legacy.has_platform_access)


class PaywallTests(TestCase):
    """The gate: what an expired organisation can and cannot reach."""

    def setUp(self):
        self.org = Organization.objects.create(name='Expired Org')
        self.org.start_trial()
        self.org.trial_ends_at = timezone.now() - timedelta(days=1)
        self.org.save()
        self.user = User.objects.create_user(
            username='expired@org.bw', email='expired@org.bw', password='pw-for-tests-1',
            organization=self.org, role='org_admin')
        self.client.force_login(self.user)

    def test_dashboard_redirects_to_billing(self):
        response = self.client.get(reverse('monitor:dashboard', args=[self.org.id]))
        self.assertRedirects(response, reverse('monitor:billing'))

    def test_api_calls_return_402_with_the_billing_url(self):
        response = self.client.get(reverse('monitor:org_details', args=[self.org.id]))
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()['billing_url'], reverse('monitor:billing'))

    def test_billing_page_itself_stays_reachable(self):
        response = self.client.get(reverse('monitor:billing'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your free trial has ended')

    def test_logout_stays_reachable(self):
        self.assertEqual(self.client.get(reverse('monitor:logout')).status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_expiry_does_not_sign_the_user_out(self):
        """The trial ending is a paywall, not a logout — the session survives so
        the user reaches the billing page as themselves and we know who is
        asking for a package."""
        self.client.get(reverse('monitor:dashboard', args=[self.org.id]))
        self.assertIn('_auth_user_id', self.client.session)
        billing = self.client.get(reverse('monitor:billing'))
        self.assertEqual(billing.context['user'], self.user)

    def test_an_expired_user_can_still_log_in_and_lands_on_the_paywall(self):
        self.client.logout()
        response = self.client.post(reverse('monitor:login'),
                                    {'email': 'expired@org.bw', 'password': 'pw-for-tests-1'},
                                    follow=True)
        self.assertIn('_auth_user_id', self.client.session)
        # Login sends them to the org picker, which the paywall then bounces to
        # billing. Two hops — assert the chain settles rather than looping.
        self.assertEqual(response.status_code, 200)
        self.assertEqual([url for url, _ in response.redirect_chain],
                         ['/app/organizations/', reverse('monitor:billing')])
        self.assertContains(response, 'Your free trial has ended')

    def test_password_reset_stays_reachable_when_expired(self):
        self.assertEqual(self.client.get(reverse('password_reset')).status_code, 200)

    def test_a_trialing_organisation_is_not_gated(self):
        self.org.trial_ends_at = timezone.now() + timedelta(days=5)
        self.org.save()
        response = self.client.get(reverse('monitor:dashboard', args=[self.org.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '5 days left')

    def test_platform_admins_are_never_gated(self):
        admin = User.objects.create_user(username='admin@sl.africa', email='admin@sl.africa',
                                         password='pw-for-tests-1', role='platform_admin')
        self.client.force_login(admin)
        response = self.client.get(reverse('monitor:dashboard', args=[self.org.id]))
        self.assertEqual(response.status_code, 200)


@override_settings(EMAIL_BACKEND=LOCMEM, SALES_NOTIFICATION_EMAILS=['sales@sociallight.africa'])
class PackageRequestTests(TestCase):

    def setUp(self):
        clear_price_list()
        self.package = make_package()
        self.org = Organization.objects.create(name='Requesting Org')
        self.org.start_trial()
        self.org.trial_ends_at = timezone.now() - timedelta(days=1)
        self.org.save()
        self.user = User.objects.create_user(
            username='buyer@org.bw', email='buyer@org.bw', password='pw-for-tests-1',
            first_name='Kagiso', organization=self.org, role='org_admin')
        self.client.force_login(self.user)

    def test_choosing_a_package_records_a_request_and_alerts_sales(self):
        response = self.client.post(reverse('monitor:package_request'),
                                    {'package': 'professional', 'note': 'PO 4471'})
        self.assertRedirects(response, f"{reverse('monitor:billing')}?requested=1")

        req = SubscriptionRequest.objects.get()
        self.assertEqual(req.package, self.package)
        self.assertEqual(req.organization, self.org)
        self.assertEqual(req.status, 'pending')
        self.assertEqual(req.note, 'PO 4471')
        self.assertEqual(mail.outbox[0].to, ['sales@sociallight.africa'])

    def test_a_request_does_not_by_itself_restore_access(self):
        self.client.post(reverse('monitor:package_request'), {'package': 'professional'})
        self.org.refresh_from_db()
        self.assertEqual(self.org.effective_plan_status, 'pending')
        self.assertFalse(self.org.has_platform_access)
        self.assertRedirects(self.client.get(reverse('monitor:dashboard', args=[self.org.id])),
                             reverse('monitor:billing'))

    def test_access_returns_once_an_admin_activates_the_package(self):
        self.client.post(reverse('monitor:package_request'), {'package': 'professional'})
        self.org.refresh_from_db()
        self.org.activate_package(self.package)
        self.assertEqual(self.client.get(reverse('monitor:dashboard', args=[self.org.id])).status_code, 200)


class OrganizationScopingTests(TestCase):
    """Public signup means strangers hold accounts — they must not see each
    other's coverage."""

    def setUp(self):
        self.mine = Organization.objects.create(name='My Org')
        self.theirs = Organization.objects.create(name='Their Org')
        self.user = User.objects.create_user(
            username='me@org.bw', email='me@org.bw', password='pw-for-tests-1',
            organization=self.mine, role='org_admin')
        self.client.force_login(self.user)

    def test_another_organisations_dashboard_is_refused(self):
        response = self.client.get(reverse('monitor:dashboard', args=[self.theirs.id]))
        self.assertRedirects(response, reverse('monitor:organizations'),
                             target_status_code=302)

    def test_another_organisations_api_is_refused(self):
        response = self.client.get(reverse('monitor:org_details', args=[self.theirs.id]))
        self.assertEqual(response.status_code, 403)

    def test_own_organisation_is_reachable(self):
        self.assertEqual(
            self.client.get(reverse('monitor:dashboard', args=[self.mine.id])).status_code, 200)

    def test_the_picker_only_lists_your_own_organisation(self):
        response = self.client.get(reverse('monitor:organizations'))
        # A single-organisation user is sent straight into it rather than asked
        # to choose between one option.
        self.assertRedirects(response, reverse('monitor:dashboard', args=[self.mine.id]))

    def test_platform_admins_still_see_every_organisation(self):
        admin = User.objects.create_user(username='admin@sl.africa', email='admin@sl.africa',
                                         password='pw-for-tests-1', role='platform_admin')
        self.client.force_login(admin)
        response = self.client.get(reverse('monitor:organizations'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Their Org')


class PriceListTests(TestCase):

    def setUp(self):
        clear_price_list()

    def test_public_price_list_shows_active_packages_only(self):
        make_package()
        make_package(name='Retired', slug='retired', is_active=False)
        response = self.client.get(reverse('monitor:pricing'))
        self.assertContains(response, 'Professional')
        self.assertNotContains(response, 'Retired')

    def test_price_with_no_figure_reads_as_talk_to_us(self):
        self.assertEqual(make_package(name='Enterprise', slug='enterprise', price=0).price_display,
                         'Talk to us')

    def test_landing_page_links_to_the_trial_signup(self):
        response = self.client.get(reverse('monitor:home'))
        self.assertContains(response, reverse('monitor:signup'))
        self.assertContains(response, 'Try Our Free Trial')


class SeededPriceListTests(TestCase):
    """The published price list, as written by migration 0012. Deliberately does
    not clear the table — this is what the migration itself produced, and what a
    fresh deployment shows before anyone opens the admin."""

    def test_a_new_deployment_has_the_four_published_tiers(self):
        self.assertEqual(
            list(Package.objects.filter(is_active=True).values_list('name', flat=True)),
            ['Spark', 'Momentum', 'Scale', 'Enterprise'])

    def test_momentum_is_the_featured_tier(self):
        featured = Package.objects.get(is_featured=True)
        self.assertEqual(featured.name, 'Momentum')
        self.assertEqual(featured.price_display, '$299')

    def test_the_placeholder_tiers_are_gone(self):
        self.assertFalse(Package.objects.filter(slug__in=['essential', 'professional']).exists())

    def test_prices_render_in_dollars_with_the_period(self):
        spark = Package.objects.get(slug='spark')
        self.assertEqual(spark.price_display, '$49')
        self.assertTrue(spark.shows_period)
        self.assertEqual(spark.period_suffix, '/ month')
        self.assertEqual(spark.price_note, 'Billed annually · $588 per year')

    def test_enterprise_is_quoted_not_listed(self):
        enterprise = Package.objects.get(slug='enterprise-full-scope')
        self.assertEqual(enterprise.price_display, 'Custom')
        self.assertFalse(enterprise.shows_period)
        self.assertTrue(enterprise.contact_only)
        self.assertEqual(enterprise.button_label, 'Contact sales')
        self.assertIn('Broadcast monitoring', enterprise.highlight_body)

    def test_lower_tiers_exclude_broadcast_and_print(self):
        for slug in ('spark', 'momentum', 'scale'):
            package = Package.objects.get(slug=slug)
            self.assertEqual(package.exclusion_list, ['Broadcast and print monitoring'])
            self.assertEqual(package.exclusion_note, 'Enterprise only')

    def test_the_price_list_page_renders_every_tier(self):
        response = self.client.get(reverse('monitor:pricing'))
        for name in ('Spark', 'Momentum', 'Scale', 'Enterprise'):
            self.assertContains(response, name)
        self.assertContains(response, '$1,299')
        self.assertContains(response, 'Only on Enterprise')

    def test_an_in_use_placeholder_is_retired_rather_than_deleted(self):
        # The migration must not orphan an organisation that was put on a
        # placeholder tier before the real list landed.
        legacy = Package.objects.create(name='Essential', slug='essential', price=1)
        org = Organization.objects.create(name='Early Adopter', package=legacy)

        retire_placeholders(Package)

        legacy.refresh_from_db()
        org.refresh_from_db()
        self.assertFalse(legacy.is_active)
        self.assertEqual(org.package, legacy)
