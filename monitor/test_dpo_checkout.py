"""Tests for the gateway checkout flow: start, return, webhook, and activation.

The provider itself is covered in test_dpo.py. What is tested here is the wiring
around it — that the paywall lets a paying customer through, that a package is
activated only on a verified payment, and that the manual invoice path still
works untouched when no gateway is configured.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from monitor.models import Organization, Package, SubscriptionRequest, User
from monitor.payment_models import Payment
from monitor.test_dpo import CREATE_OK, DPO_SETTINGS, canned, verify_xml


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

    def test_gateway_failure_returns_the_user_to_billing_not_an_error_page(self):
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
