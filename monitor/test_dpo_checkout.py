"""Tests for the gateway checkout flow: start, return, webhook, and activation.

The provider itself is covered in test_dpo.py. What is tested here is the wiring
around it — that the paywall lets a paying customer through, that a package is
activated only on a verified payment, and that the manual invoice path still
works untouched when no gateway is configured.
"""
import re
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from monitor.models import (EmailVerificationToken, OnboardingProgress, Organization, Package,
                            SubscriptionRequest, User)
from monitor.payment_models import Payment
from monitor.test_dpo import CREATE_OK, DPO_SETTINGS, canned, verify_xml, xml


@override_settings(**DPO_SETTINGS)
class CheckoutFlowTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Ministry of Health', email='it@gov.bw')
        self.org.start_trial()
        self.org.save()
        self.user = User.objects.create_user(
            username='naledi@gov.bw', email='naledi@gov.bw', password='correct-horse-9',
            first_name='Naledi', last_name='Mokgadi', organization=self.user_org(),
            role='org_admin', email_verified=True)
        self.package = Package.objects.create(
            name='Scale', slug='scale-checkout-test', price=299, currency='USD')
        self.client.force_login(self.user)

    def user_org(self):
        return self.org

    def expire_trial(self):
        """Put the organisation in the state that actually reaches checkout."""
        self.org.plan_status = 'expired'
        self.org.save(update_fields=['plan_status'])

    def start(self):
        return self.client.post(reverse('monitor:checkout_start'), {'package': self.package.slug})

    # ── Starting a checkout ──────────────────────────────────────────────────
    def test_checkout_redirects_to_the_gateway(self):
        with canned(CREATE_OK):
            response = self.start()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('https://secure.3gdirectpay.com/payv3.php?ID='))
        self.assertEqual(Payment.objects.count(), 1)

    def test_an_expired_organisation_can_still_reach_checkout(self):
        """The paywall redirects /app/ requests for expired organisations — and
        an expired organisation is the only kind that needs to pay. Without the
        middleware exemption this redirects to billing and nobody can ever buy."""
        self.expire_trial()
        with canned(CREATE_OK):
            response = self.start()
        self.assertEqual(response.status_code, 302)
        self.assertIn('3gdirectpay.com', response['Location'])

    def test_a_second_rapid_submit_reuses_the_pending_checkout(self):
        """A double-click, or a second POST fired before the first request's
        redirect lands, must not open a second transaction at the gateway: the
        second submit should be sent straight back to the token the first one
        already opened. Only one createToken call, only one Payment row."""
        with canned(CREATE_OK) as post:
            first = self.start()
            second = self.start()

        self.assertEqual(post.call_count, 1)
        self.assertEqual(Payment.objects.filter(organization=self.org).count(), 1)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first['Location'], second['Location'])

    def test_an_expired_pending_checkout_opens_a_fresh_one(self):
        """The reuse guard is windowed by the gateway's own token lifetime — once
        that has elapsed the old token is dead at DPO regardless of our status,
        so a new submit must open a new one rather than reusing it forever."""
        with canned(CREATE_OK):
            self.start()
        stale_cutoff = timezone.now() - timedelta(hours=3)
        Payment.objects.filter(organization=self.org).update(created_at=stale_cutoff)

        with canned(CREATE_OK) as post:
            self.start()

        self.assertEqual(post.call_count, 1)
        self.assertEqual(Payment.objects.filter(organization=self.org).count(), 2)

    # ── Return URLs ──────────────────────────────────────────────────────────
    # DPO rejects a loopback return URL with 403 on the whole createToken call,
    # so what goes in these fields decides whether anyone can pay at all.
    def urls_sent(self, post):
        body = post.call_args.kwargs['data'].decode()
        return re.findall(r'<(?:RedirectURL|BackURL)>([^<]*)<', body)

    @override_settings(PAYMENT_RETURN_BASE_URL='')
    def test_return_urls_come_from_the_request_host_by_default(self):
        # Pinned blank rather than left to the environment: a developer's own
        # .env sets this to their tunnel, which would otherwise make this test
        # pass or fail depending on whose machine it runs on.
        with canned(CREATE_OK) as post:
            self.start()
        for url in self.urls_sent(post):
            self.assertTrue(url.startswith('http://testserver'), url)

    @override_settings(PAYMENT_RETURN_BASE_URL='https://pay.example.com')
    def test_configured_base_url_overrides_the_request_host(self):
        """The setting exists so a tunnelled or proxied development machine can
        send a public URL regardless of the host the request arrived on."""
        with canned(CREATE_OK) as post:
            self.start()
        urls = self.urls_sent(post)
        self.assertEqual(len(urls), 2)
        for url in urls:
            self.assertTrue(url.startswith('https://pay.example.com/'), url)
            self.assertNotIn('testserver', url)

    @override_settings(PAYMENT_RETURN_BASE_URL='https://pay.example.com/')
    def test_a_trailing_slash_on_the_base_url_does_not_double_up(self):
        with canned(CREATE_OK) as post:
            self.start()
        for url in self.urls_sent(post):
            self.assertNotIn('.com//', url)

    @override_settings(PAYMENT_RETURN_BASE_URL='https://pay.example.com')
    def test_no_loopback_address_reaches_the_gateway(self):
        """The invariant that matters: whatever the request host was, DPO must
        never be sent a loopback URL."""
        with canned(CREATE_OK) as post:
            self.start()
        body = post.call_args.kwargs['data'].decode()
        for loopback in ('127.0.0.1', 'localhost'):
            self.assertNotIn(loopback, body)

    def test_gateway_failure_returns_the_user_to_billing_not_an_error_page(self):
        import requests
        with patch('requests.post', side_effect=requests.ConnectionError('down')):
            response = self.start()
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=unavailable')

    def test_a_refused_amount_is_not_reported_as_an_unreachable_gateway(self):
        """DPO's test account caps the transaction amount, and a refusal reads
        very differently from an outage: retrying will be refused identically, so
        telling the customer to try again in a moment is wrong."""
        refused = xml('<Result>904</Result><ResultExplanation>The transaction amount has '
                      'exceeded your allowed transaction limit, please contact: '
                      'support@directpay.online</ResultExplanation>')
        with canned(refused):
            response = self.start()
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=rejected')

    def test_the_gateways_own_support_address_is_never_shown_to_the_customer(self):
        """The refusal wording names DPO's support desk. Our customers must be
        pointed at us, not at our payment provider."""
        refused = xml('<Result>904</Result><ResultExplanation>please contact: '
                      'support@directpay.online</ResultExplanation>')
        with canned(refused):
            self.start()
        page = self.client.get(reverse('monitor:billing') + '?checkout=rejected')
        self.assertNotContains(page, 'directpay.online')
        self.assertContains(page, "We couldn't start that payment")

    def test_an_inactive_company_token_does_not_invite_retries(self):
        """802 means our own credential is wrong or not activated. Retrying can
        never succeed, so the customer must not be told to try again shortly —
        which is exactly what happened when this fell through to the generic
        PaymentError branch it subclasses."""
        inactive = xml('<Result>802</Result>'
                       '<ResultExplanation>Company is not active</ResultExplanation>')
        with canned(inactive):
            response = self.start()
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=rejected')

    def test_a_misconfiguration_is_logged_as_an_error_not_a_warning(self):
        """It needs a developer, so it must not sit at the same level as a
        customer's card being declined."""
        inactive = xml('<Result>802</Result>'
                       '<ResultExplanation>Company is not active</ResultExplanation>')
        with canned(inactive):
            with self.assertLogs('monitor.subscription_views', level='ERROR'):
                self.start()

    def test_an_unreachable_gateway_still_reads_as_temporary(self):
        import requests
        with patch('requests.post', side_effect=requests.ConnectionError('down')):
            response = self.start()
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=unavailable')

    def test_contact_only_package_falls_back_to_the_manual_request(self):
        enterprise = Package.objects.create(
            name='Enterprise', slug='enterprise-checkout-test', price=0, contact_only=True)
        with patch('requests.post') as post:
            self.client.post(reverse('monitor:checkout_start'), {'package': enterprise.slug})
        post.assert_not_called()
        self.assertEqual(SubscriptionRequest.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 0)

    # ── Returning from the gateway ───────────────────────────────────────────
    def return_for(self, payment):
        return self.client.get(reverse('monitor:checkout_return'),
                               {'CompanyRef': str(payment.id)})

    def test_a_paid_return_activates_the_package(self):
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()

        with canned(verify_xml('000')):
            response = self.return_for(payment)

        self.org.refresh_from_db()
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=paid')
        self.assertEqual(self.org.plan_status, 'active')
        self.assertEqual(self.org.package_id, self.package.id)

    def test_an_unpaid_return_does_not_activate_anything(self):
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()

        with canned(verify_xml('901', explanation='Transaction declined')):
            response = self.return_for(payment)

        self.org.refresh_from_db()
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=failed')
        self.assertEqual(self.org.plan_status, 'expired')
        self.assertIsNone(self.org.package_id)

    def test_return_works_without_a_session(self):
        """A customer whose session cookie did not survive the round trip must
        still have their payment confirmed — otherwise they have paid and the
        platform has not noticed."""
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()
        self.client.logout()

        with canned(verify_xml('000')):
            self.return_for(payment)

        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'active')

    def test_a_forged_reference_changes_nothing(self):
        response = self.client.get(reverse('monitor:checkout_return'),
                                   {'CompanyRef': 'not-a-uuid'})
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=unknown')

    def test_query_string_claiming_success_is_ignored(self):
        """DPO redirects with CCDapproval and TransID in the URL. They are
        attacker-controlled and must never be what decides a payment."""
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()

        with canned(verify_xml('900', explanation='Transaction not paid yet')):
            self.client.get(reverse('monitor:checkout_return'),
                            {'CompanyRef': str(payment.id), 'CCDapproval': '123456',
                             'TransID': '999', 'Result': '000'})

        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'expired')

    # ── Webhook ──────────────────────────────────────────────────────────────
    def test_callback_activates_and_acknowledges_in_dpo_format(self):
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()

        with canned(verify_xml('000')):
            response = self.client.post(reverse('monitor:checkout_callback'),
                                        {'CompanyRef': str(payment.id)})

        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'active')
        # DPO treats anything but this as a failed delivery and retries.
        self.assertIn(b'<Response>OK</Response>', response.content)

    def test_callback_needs_no_csrf_token_or_session(self):
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()
        self.client.logout()

        with canned(verify_xml('000')):
            response = self.client.post(reverse('monitor:checkout_callback'),
                                        {'CompanyRef': str(payment.id)})
        self.assertEqual(response.status_code, 200)

    def test_callback_for_an_unknown_payment_is_still_acknowledged(self):
        with patch('requests.post') as post:
            response = self.client.post(reverse('monitor:checkout_callback'),
                                        {'CompanyRef': 'nonsense'})
        post.assert_not_called()
        self.assertIn(b'<Response>OK</Response>', response.content)

    def test_webhook_and_return_together_activate_exactly_once(self):
        """Both legs fire for a normal payment. The second must be a no-op — not
        a second charge, and not a moved settlement date."""
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()

        with canned(verify_xml('000')):
            self.client.post(reverse('monitor:checkout_callback'), {'CompanyRef': str(payment.id)})
        payment.refresh_from_db()
        settlement = payment.settlement_available_at

        with patch('requests.post') as post:
            self.return_for(payment)
        post.assert_not_called()

        payment.refresh_from_db()
        self.assertEqual(payment.settlement_available_at, settlement)
        self.assertEqual(Payment.objects.count(), 1)

    def test_settling_a_payment_does_not_create_an_onboarding_record_for_a_legacy_account(self):
        """This fixture's user predates onboarding — never had onboarding.start()
        called for it, so it has no OnboardingProgress row, same as a real account
        that existed before onboarding was introduced. Settling a payment must not
        conjure one into existence and drag a grandfathered account into a wizard
        it was never shown."""
        self.expire_trial()
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()

        with canned(verify_xml('000')):
            self.return_for(payment)

        self.assertFalse(OnboardingProgress.objects.filter(user=self.user).exists())


@override_settings(PAYMENTS_ENABLED=False, PAYMENT_PROVIDER='manual',
                   DPO_COMPANY_TOKEN='', DPO_SERVICE_TYPE='')
class ManualPathUnaffectedTests(TestCase):
    """With no gateway configured the platform must behave exactly as it did
    before this integration existed.

    The settings are pinned explicitly rather than left to the environment. A
    developer's own .env now switches DPO on, and without this the class would
    quietly stop testing the thing it is named after — which is exactly what
    happened the first time this ran after the real credentials were added.
    """

    def setUp(self):
        self.org = Organization.objects.create(name='Botswana Post', email='it@bpost.bw')
        self.org.start_trial()
        self.org.save()
        self.user = User.objects.create_user(
            username='t@bpost.bw', email='t@bpost.bw', password='correct-horse-9',
            organization=self.org, role='org_admin', email_verified=True)
        self.package = Package.objects.create(name='Spark', slug='spark-manual-test', price=49)
        self.client.force_login(self.user)

    def test_billing_page_offers_the_request_flow_not_a_pay_button(self):
        response = self.client.get(reverse('monitor:billing'))
        self.assertFalse(response.context['gateway_checkout'])
        # The modal copy also says "and activate", so assert on the thing that
        # only exists when a gateway is on: a form posting to checkout.
        self.assertNotContains(response, reverse('monitor:checkout_start'))
        self.assertContains(response, reverse('monitor:package_request'))

    def test_checkout_start_falls_back_to_raising_a_subscription_request(self):
        with patch('requests.post') as post:
            self.client.post(reverse('monitor:checkout_start'), {'package': self.package.slug})
        post.assert_not_called()
        self.assertEqual(SubscriptionRequest.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 0)


@override_settings(**DPO_SETTINGS)
class SkipTrialPayNowTests(TestCase):
    """"Skip the trial, pay now" — the second signup button, through to the
    plan step's Pay now button and a settled DPO payment.

    Walks the same sequence PaymentsDisabledTests does (signup → verify →
    profile → agency → terms/privacy/disclaimer → payment), just with DPO
    switched on and the "pay now" intent set at signup, since the plan step's
    Pay now button is only reachable once everything ahead of it is done.
    """
    REGISTRATION = {
        'first_name': 'Naledi', 'last_name': 'Mokgadi', 'email': 'naledi@paynow-test.bw',
        'phone': '+267 71 234 567', 'job_title': 'Communications Manager',
        'country': 'Botswana', 'org_name': 'Pay Now Test Org',
        'password': 'correct-horse-9', 'confirm_password': 'correct-horse-9',
    }

    def setUp(self):
        self.package = Package.objects.create(
            name='Scale', slug='scale-paynow-test', price=1299, currency='USD')
        self.client.post(reverse('monitor:signup'), {**self.REGISTRATION, 'intent': 'pay_now'})
        self.user = User.objects.get(email=self.REGISTRATION['email'])
        self.org = self.user.organization
        token = EmailVerificationToken.objects.get(user=self.user)
        self.client.get(reverse('monitor:onboarding_verify_confirm', args=[token.token]))
        self.client.post(reverse('monitor:onboarding_profile'), {
            'first_name': 'Naledi', 'last_name': 'Mokgadi',
            'job_title': 'Communications Manager', 'country': 'Botswana'})
        self.client.post(reverse('monitor:onboarding_agency'), {'account_type': 'individual'})
        for name in ('onboarding_terms', 'onboarding_privacy', 'onboarding_disclaimer'):
            self.client.post(reverse(f'monitor:{name}'), {'accept': 'on'})
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})

    def test_signup_records_the_skip_trial_intent(self):
        self.assertTrue(self.user.onboarding.skip_trial_requested)

    def test_the_ordinary_create_account_button_does_not_set_the_flag(self):
        # setUp's own signup left the client logged in, and signup() redirects
        # an already-authenticated request straight past the form.
        self.client.logout()
        other = {**self.REGISTRATION, 'email': 'someoneelse@paynow-test.bw',
                'org_name': 'Someone Elses Org'}
        self.client.post(reverse('monitor:signup'), other)
        user = User.objects.get(email=other['email'])
        self.assertFalse(user.onboarding.skip_trial_requested)

    def test_the_plan_step_offers_a_pay_now_button_for_a_priced_package(self):
        response = self.client.get(reverse('monitor:onboarding_plan'))
        self.assertContains(response, reverse('monitor:checkout_start'))
        self.assertContains(response, 'Pay now')

    def test_the_plan_step_leads_with_the_skip_trial_banner(self):
        response = self.client.get(reverse('monitor:onboarding_plan'))
        self.assertContains(response, 'You chose to skip the trial')

    def test_paying_from_the_plan_step_activates_the_package_immediately(self):
        with canned(CREATE_OK):
            response = self.client.post(
                reverse('monitor:checkout_start'), {'package': self.package.slug})
        self.assertEqual(response.status_code, 302)
        self.assertIn('3gdirectpay.com', response['Location'])

        payment = Payment.objects.get(organization=self.org)
        with canned(verify_xml('000', amount=f'{self.package.price:.2f}')):
            self.client.get(reverse('monitor:checkout_return'), {'CompanyRef': str(payment.id)})

        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'active')
        self.assertEqual(self.org.package_id, self.package.id)

    def test_paying_from_the_plan_step_completes_onboarding_without_a_bounce_back(self):
        """Without _advance_onboarding_past_plan, this user would land back on
        the plan step (or earlier) after paying, since plan_assigned is never
        marked along the checkout_start path."""
        with canned(CREATE_OK):
            self.client.post(reverse('monitor:checkout_start'), {'package': self.package.slug})
        payment = Payment.objects.get(organization=self.org)
        with canned(verify_xml('000', amount=f'{self.package.price:.2f}')):
            self.client.get(reverse('monitor:checkout_return'), {'CompanyRef': str(payment.id)})

        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding.is_complete)

        response = self.client.get(reverse('monitor:dashboard', args=[self.org.id]))
        self.assertEqual(response.status_code, 200)
