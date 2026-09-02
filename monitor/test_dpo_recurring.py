"""Tests for subscription lapsing, recurring renewal, and the cancelled-checkout leg.

Covers the three gaps in the original DPO integration: a paid subscription that
never ended, no way to charge a saved card, and BackURL pointing at the payment
webhook instead of at a "you backed out" page.

As with the rest of the DPO suite, every HTTP call is mocked. Nothing here
touches the real gateway.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from monitor.models import Organization, Package, User, add_billing_period
from monitor.payment_models import Payment, PaymentMethod
from monitor.payments.dpo import DPOProvider
from monitor.tasks import renew_subscriptions
from monitor.test_dpo import CREATE_OK, DPO_SETTINGS, canned, verify_xml, xml


def subscription_xml(result='000', subscription='SUB-TOKEN-1', customer='CUST-TOKEN-1'):
    return xml(f'<Result>{result}</Result><ResultExplanation>Success</ResultExplanation>'
               f'<SubscriptionToken>{subscription}</SubscriptionToken>'
               f'<CustomerToken>{customer}</CustomerToken>')


def recurrent_xml(result='000', explanation='Transaction successful'):
    return xml(f'<Result>{result}</Result><ResultExplanation>{explanation}</ResultExplanation>')


class BillingPeriodTests(TestCase):
    """The period maths, which decides when people lose access."""

    def test_monthly_period_follows_the_calendar(self):
        start = timezone.make_aware(timezone.datetime(2026, 1, 15, 9, 0))
        self.assertEqual(add_billing_period(start, 'monthly').date().isoformat(), '2026-02-15')

    def test_month_end_does_not_overflow_into_the_next_month(self):
        """31 January + 1 month is 28 February, not 3 March. A timedelta of 30
        days would get this wrong and bill the customer early."""
        start = timezone.make_aware(timezone.datetime(2026, 1, 31, 9, 0))
        self.assertEqual(add_billing_period(start, 'monthly').date().isoformat(), '2026-02-28')

    def test_quarterly_and_annual(self):
        start = timezone.make_aware(timezone.datetime(2026, 1, 15, 9, 0))
        self.assertEqual(add_billing_period(start, 'quarterly').date().isoformat(), '2026-04-15')
        self.assertEqual(add_billing_period(start, 'annual').date().isoformat(), '2027-01-15')


class SubscriptionLapseTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Ministry of Health', email='it@gov.bw')
        self.monthly = Package.objects.create(
            name='Momentum', slug='momentum-lapse-test', price=299, billing_period='monthly')

    def test_activating_a_package_sets_the_period_end(self):
        self.org.activate_package(self.monthly)
        self.org.refresh_from_db()
        self.assertIsNotNone(self.org.current_period_end)
        self.assertGreater(self.org.current_period_end, timezone.now())

    def test_a_lapsed_paid_subscription_loses_access(self):
        """The gap this closes: before, an active org with a long-past period end
        kept full access forever."""
        self.org.activate_package(self.monthly)
        self.org.current_period_end = timezone.now() - timedelta(days=1)
        self.org.save(update_fields=['current_period_end'])

        self.assertEqual(self.org.effective_plan_status, 'past_due')
        self.assertFalse(self.org.has_platform_access)
        self.assertTrue(self.org.trial_has_expired)

    def test_a_current_paid_subscription_keeps_access(self):
        self.org.activate_package(self.monthly)
        self.assertEqual(self.org.effective_plan_status, 'active')
        self.assertTrue(self.org.has_platform_access)

    def test_a_subscription_with_no_period_end_never_lapses(self):
        """How a subscription activated by hand, on an off-platform term with no
        agreed end date, keeps working. Existing customers are in this state."""
        self.org.activate_package(self.monthly, period_end=None)
        self.org.current_period_end = None
        self.org.plan_status = 'active'
        self.org.save(update_fields=['current_period_end', 'plan_status'])

        self.assertFalse(self.org.paid_period_has_lapsed)
        self.assertEqual(self.org.effective_plan_status, 'active')
        self.assertTrue(self.org.has_platform_access)

    def test_renewing_extends_from_the_old_period_not_from_today(self):
        """A renewal charged early must not shorten what the customer paid for."""
        end = timezone.now() + timedelta(days=3)
        self.org.activate_package(self.monthly, period_end=end)

        self.org.activate_package(self.monthly, extend=True)
        self.org.refresh_from_db()
        self.assertEqual(self.org.current_period_end.date(),
                         add_billing_period(end, 'monthly').date())

    def test_an_explicit_period_end_wins(self):
        """An annual invoice against a monthly tier, agreed off-platform."""
        agreed = timezone.now() + timedelta(days=365)
        self.org.activate_package(self.monthly, period_end=agreed)
        self.org.refresh_from_db()
        self.assertEqual(self.org.current_period_end.date(), agreed.date())


@override_settings(**DPO_SETTINGS)
class MiddlewareGatingTests(TestCase):
    """The paywall must treat a lapsed paid subscription like any other unpaid
    organisation - it already reads has_platform_access, so this proves the
    computed lapse flows all the way through."""

    def setUp(self):
        self.org = Organization.objects.create(name='Botswana Post', email='it@bpost.bw')
        self.package = Package.objects.create(
            name='Spark', slug='spark-gate-test', price=49, billing_period='monthly')
        self.user = User.objects.create_user(
            username='t@bpost.bw', email='t@bpost.bw', password='correct-horse-9',
            organization=self.org, role='org_admin', email_verified=True)
        self.client.force_login(self.user)

    def test_paid_org_reaches_the_app(self):
        """Not "returns 200" — a single-org user is forwarded to their dashboard,
        so the thing that matters is that they are not sent to the paywall."""
        self.org.activate_package(self.package)
        response = self.client.get(reverse('monitor:organizations'))
        self.assertNotEqual(response.get('Location', ''), reverse('monitor:billing'))
        self.assertTrue(self.org.has_platform_access)

    def test_lapsed_org_is_redirected_to_billing(self):
        self.org.activate_package(self.package)
        self.org.current_period_end = timezone.now() - timedelta(days=1)
        self.org.save(update_fields=['current_period_end'])

        response = self.client.get(reverse('monitor:organizations'))
        self.assertRedirects(response, reverse('monitor:billing'))


@override_settings(**DPO_SETTINGS)
class SavedCardTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Debswana', email='it@debswana.bw')
        self.provider = DPOProvider()

    def test_subscription_token_is_stored_as_a_payment_method(self):
        with canned(subscription_xml()):
            method = self.provider.capture_subscription_token(self.org, 'buyer@debswana.bw')

        self.assertIsNotNone(method)
        self.assertEqual(method.provider, 'dpo')
        self.assertEqual(method.provider_token, 'SUB-TOKEN-1')
        self.assertEqual(method.provider_customer_id, 'CUST-TOKEN-1')
        # No card data may ever be stored, tokenised flow or not.
        self.assertEqual(method.last4, '')

    def test_no_saved_card_is_not_an_error(self):
        """999 means DPO has no customer on file. That is a normal answer - the
        org simply renews by paying again."""
        with canned(xml('<Result>999</Result><ResultExplanation>Customer not found</ResultExplanation>')):
            method = self.provider.capture_subscription_token(self.org, 'nobody@debswana.bw')
        self.assertIsNone(method)
        self.assertEqual(PaymentMethod.objects.count(), 0)

    def test_capturing_twice_does_not_duplicate_the_card(self):
        with canned(subscription_xml()):
            first = self.provider.capture_subscription_token(self.org, 'buyer@debswana.bw')
        with canned(subscription_xml()):
            second = self.provider.capture_subscription_token(self.org, 'buyer@debswana.bw')
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(PaymentMethod.objects.count(), 1)


@override_settings(**DPO_SETTINGS)
class RecurringChargeTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Choppies', email='it@choppies.bw')
        self.package = Package.objects.create(
            name='Momentum', slug='momentum-charge-test', price=299,
            currency='USD', billing_period='monthly')
        self.method = PaymentMethod.objects.create(
            organization=self.org, provider='dpo',
            provider_token='SUB-TOKEN-1', provider_customer_id='CUST-TOKEN-1')
        self.provider = DPOProvider()

    def test_a_renewal_creates_a_token_then_charges_it(self):
        with canned(CREATE_OK, recurrent_xml('000')) as post:
            result = self.provider.charge_recurring(
                self.org, self.method, Decimal('299.00'), 'USD', package=self.package)

        self.assertTrue(result.succeeded)
        # Two calls: a fresh transaction, then the charge against the saved card.
        self.assertEqual(post.call_count, 2)
        charge_body = post.call_args_list[1].kwargs['data'].decode()
        self.assertIn('<Request>chargeTokenRecurrent</Request>', charge_body)
        # DPO documents this field lower-cased on the request, unlike the
        # response field it comes from.
        self.assertIn('<subscriptionToken>SUB-TOKEN-1</subscriptionToken>', charge_body)

        payment = Payment.objects.get()
        self.assertEqual(payment.status, 'succeeded')
        self.assertIsNotNone(payment.settlement_available_at)

    def test_a_declined_renewal_records_a_failed_payment(self):
        with canned(CREATE_OK, recurrent_xml('999', 'Customer not found or unverified')):
            result = self.provider.charge_recurring(
                self.org, self.method, Decimal('299.00'), 'USD', package=self.package)

        self.assertFalse(result.succeeded)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, 'failed')
        self.assertIn('Customer not found', payment.failure_reason)

    def test_a_renewal_charge_sends_no_redirect_urls(self):
        """Nobody is browsing, so there is nothing to redirect - and omitting them
        keeps the renewal path clear of DPO's rejection of loopback URLs."""
        with canned(CREATE_OK, recurrent_xml('000')) as post:
            self.provider.charge_recurring(
                self.org, self.method, Decimal('299.00'), 'USD', package=self.package)
        create_body = post.call_args_list[0].kwargs['data'].decode()
        self.assertNotIn('<RedirectURL>', create_body)
        self.assertNotIn('<BackURL>', create_body)


@override_settings(**DPO_SETTINGS)
class RenewalTaskTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='BotswanaCraft', email='it@bcraft.bw')
        self.package = Package.objects.create(
            name='Spark', slug='spark-task-test', price=49,
            currency='USD', billing_period='monthly')
        self.org.activate_package(self.package)
        self.expire_period()

    def expire_period(self):
        self.org.current_period_end = timezone.now() - timedelta(hours=1)
        self.org.save(update_fields=['current_period_end'])

    def give_saved_card(self):
        return PaymentMethod.objects.create(
            organization=self.org, provider='dpo',
            provider_token='SUB-TOKEN-1', provider_customer_id='CUST-TOKEN-1')

    def test_a_due_subscription_is_renewed_and_the_period_extended(self):
        self.give_saved_card()
        before = self.org.current_period_end

        with canned(CREATE_OK, recurrent_xml('000')):
            renew_subscriptions()

        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'active')
        self.assertGreater(self.org.current_period_end, before)
        self.assertTrue(self.org.has_platform_access)
        self.assertEqual(Payment.objects.filter(status='succeeded').count(), 1)

    def test_a_declined_renewal_marks_the_org_past_due(self):
        self.give_saved_card()
        with canned(CREATE_OK, recurrent_xml('901', 'Declined')):
            renew_subscriptions()

        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'past_due')
        self.assertFalse(self.org.has_platform_access)

    def test_an_org_with_no_saved_card_is_marked_past_due_without_calling_dpo(self):
        with patch('requests.post') as post:
            renew_subscriptions()
        post.assert_not_called()
        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'past_due')

    def test_a_subscription_not_yet_due_is_left_alone(self):
        self.give_saved_card()
        self.org.current_period_end = timezone.now() + timedelta(days=5)
        self.org.save(update_fields=['current_period_end'])

        with patch('requests.post') as post:
            renew_subscriptions()
        post.assert_not_called()
        self.org.refresh_from_db()
        self.assertEqual(self.org.plan_status, 'active')

    def test_one_failing_org_does_not_stop_the_others(self):
        self.give_saved_card()
        other = Organization.objects.create(name='Sefalana', email='it@sefalana.bw')
        other.activate_package(self.package)
        other.current_period_end = timezone.now() - timedelta(hours=1)
        other.save(update_fields=['current_period_end'])
        PaymentMethod.objects.create(organization=other, provider='dpo',
                                     provider_token='SUB-TOKEN-2')

        import requests
        # First org's createToken blows up; the second must still be charged.
        with patch('requests.post', side_effect=[
                requests.ConnectionError('down'),
                type('R', (), {'content': CREATE_OK, 'raise_for_status': lambda s: None})(),
                type('R', (), {'content': recurrent_xml('000'), 'raise_for_status': lambda s: None})(),
        ]):
            summary = renew_subscriptions()

        self.assertIn('1 renewed', summary)
        self.assertIn('1 failed', summary)

    @override_settings(PAYMENTS_ENABLED=False, PAYMENT_PROVIDER='manual')
    def test_the_task_is_a_no_op_when_payments_are_disabled(self):
        self.give_saved_card()
        with patch('requests.post') as post:
            summary = renew_subscriptions()
        post.assert_not_called()
        self.assertIn('disabled', summary)


@override_settings(**DPO_SETTINGS)
class CancelledCheckoutTests(TestCase):
    """BackURL: the customer clicked back without paying."""

    def setUp(self):
        self.org = Organization.objects.create(name='Letshego', email='it@letshego.bw')
        self.org.start_trial()
        self.org.plan_status = 'expired'
        self.org.save()
        self.package = Package.objects.create(
            name='Spark', slug='spark-cancel-test', price=49, currency='USD')
        self.user = User.objects.create_user(
            username='t@letshego.bw', email='t@letshego.bw', password='correct-horse-9',
            organization=self.org, role='org_admin', email_verified=True)
        self.client.force_login(self.user)

    def start(self):
        return self.client.post(reverse('monitor:checkout_start'),
                                {'package': self.package.slug})

    def test_back_url_sent_to_dpo_is_the_cancelled_view_not_the_webhook(self):
        with canned(CREATE_OK) as post:
            self.start()
        body = post.call_args.kwargs['data'].decode()
        self.assertIn(reverse('monitor:checkout_cancelled'), body)
        self.assertNotIn(f'<BackURL>http://testserver{reverse("monitor:checkout_callback")}</BackURL>',
                         body)

    def test_allow_recurrent_is_requested_for_a_self_serve_tier(self):
        with canned(CREATE_OK) as post:
            self.start()
        self.assertIn('<AllowRecurrent>1</AllowRecurrent>',
                      post.call_args.kwargs['data'].decode())

    def test_cancelling_verifies_nothing_and_activates_nothing(self):
        with canned(CREATE_OK):
            self.start()
        payment = Payment.objects.get()

        with patch('requests.post') as post:
            response = self.client.get(reverse('monitor:checkout_cancelled'),
                                       {'CompanyRef': str(payment.id)})

        # There is nothing to verify: they did not pay.
        post.assert_not_called()
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=cancelled')

        payment.refresh_from_db()
        self.org.refresh_from_db()
        self.assertEqual(payment.status, 'pending')
        self.assertEqual(self.org.plan_status, 'expired')
        self.assertIsNone(self.org.package_id)

    def test_cancelling_with_an_unknown_reference_still_renders(self):
        response = self.client.get(reverse('monitor:checkout_cancelled'),
                                   {'CompanyRef': 'not-a-uuid'})
        self.assertRedirects(response, reverse('monitor:billing') + '?checkout=cancelled')

    def test_the_cancelled_banner_is_shown_on_billing(self):
        response = self.client.get(reverse('monitor:billing') + '?checkout=cancelled')
        self.assertContains(response, 'You left the payment page')
        self.assertContains(response, 'Nothing has been charged')
