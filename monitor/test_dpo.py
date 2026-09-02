"""Tests for the DPO Pay provider.

Every test here runs against canned XML. Nothing reaches DPO: there is no
sandbox host to reach — test and live share one endpoint and differ only by
company token — so a test that made a real call would be a live call with a test
credential, and would fail in CI the moment the credential rotated.

What is worth testing about this provider is not the happy path, which is three
lines of XML. It is the refusals: an unrecognised result code, an amount that
does not match what we asked for, a second verification of an already-paid
transaction. Those are the paths where a bug quietly gives away a subscription.
"""
import re
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from monitor.models import Organization, Package, User
from monitor.payment_models import Payment
from monitor.payments import get_provider
from monitor.payments.base import PaymentConfigurationError, PaymentError
from monitor.payments.dpo import DPOProvider

# Deliberately not a real DPO credential. Every HTTP call in these tests is
# mocked, so the token is only ever a fixture string — and a real one, even a
# test one, has no business being in a git history.
DPO_SETTINGS = dict(
    PAYMENTS_ENABLED=True,
    PAYMENT_PROVIDER='dpo',
    DPO_COMPANY_TOKEN='00000000-0000-4000-8000-000000000000',
    DPO_SERVICE_TYPE='54841',
)


def xml(body):
    return f'<?xml version="1.0" encoding="UTF-8"?><API3G>{body}</API3G>'.encode()


CREATE_OK = xml('<Result>000</Result><ResultExplanation>Transaction created</ResultExplanation>'
                '<TransToken>6253BFCA-848B-4884-A5FA-68E425643BD1</TransToken>'
                '<TransRef>R4981192</TransRef>')


def verify_xml(result, amount='299.00', currency='USD', explanation='Transaction Paid'):
    return xml(f'<Result>{result}</Result><ResultExplanation>{explanation}</ResultExplanation>'
               f'<TransactionAmount>{amount}</TransactionAmount>'
               f'<TransactionCurrency>{currency}</TransactionCurrency>'
               f'<TransactionRef>R4981192</TransactionRef>'
               f'<ApprovalNumber>400309113213</ApprovalNumber>')


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None


def canned(*responses):
    """Patch the provider's HTTP call to return the given payloads in order."""
    return patch('requests.post', side_effect=[FakeResponse(r) for r in responses])


@override_settings(**DPO_SETTINGS)
class DPOProviderTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Ministry of Health', email='it@gov.bw')
        self.user = User.objects.create_user(
            username='naledi@gov.bw', email='naledi@gov.bw', password='correct-horse-9',
            first_name='Naledi', last_name='Mokgadi', organization=self.org)
        self.package = Package.objects.create(name='Scale', slug='scale-dpo-test', price=299)

    def start(self, provider):
        return provider.start_checkout(
            self.org, Decimal('299.00'), 'USD', package=self.package, user=self.user,
            return_url='https://app.sociallightbw.com/app/billing/return/',
            callback_url='https://app.sociallightbw.com/app/billing/callback/')

    # ── Selection & configuration ────────────────────────────────────────────
    def test_get_provider_returns_dpo(self):
        provider = get_provider()
        self.assertIsInstance(provider, DPOProvider)
        self.assertTrue(provider.is_redirect)
        self.assertFalse(provider.collects_card)
        self.assertTrue(provider.is_enabled)

    @override_settings(DPO_COMPANY_TOKEN='')
    def test_missing_credentials_disable_the_provider(self):
        """An unconfigured DPO must read as disabled rather than blowing up when
        a page asks whether it can take a payment."""
        self.assertFalse(DPOProvider().is_enabled)
        with self.assertRaises(PaymentConfigurationError):
            DPOProvider().verify_token('anything')

    # ── createToken ──────────────────────────────────────────────────────────
    def test_start_checkout_sends_expected_xml_and_returns_redirect(self):
        with canned(CREATE_OK) as post:
            session = self.start(DPOProvider())

        body = post.call_args.kwargs['data'].decode()
        self.assertIn('<Request>createToken</Request>', body)
        self.assertIn('<PaymentAmount>299.00</PaymentAmount>', body)
        self.assertIn('<PaymentCurrency>USD</PaymentCurrency>', body)
        self.assertIn('<ServiceType>54841</ServiceType>', body)
        self.assertIn('<CompanyRefUnique>1</CompanyRefUnique>', body)
        # The CompanyRef is the local Payment id — that link is what the return
        # leg relies on to find the row again.
        self.assertIn(f'<CompanyRef>{session.payment.id}</CompanyRef>', body)

        self.assertEqual(
            session.redirect_url,
            'https://secure.3gdirectpay.com/payv3.php?ID=6253BFCA-848B-4884-A5FA-68E425643BD1')
        self.assertEqual(session.payment.status, 'pending')
        self.assertEqual(session.payment.provider, 'dpo')

    def test_amount_is_never_sent_with_more_than_two_decimals(self):
        with canned(CREATE_OK) as post:
            DPOProvider().start_checkout(self.org, Decimal('49.999'), 'usd', user=self.user)
        self.assertIn('<PaymentAmount>50.00</PaymentAmount>', post.call_args.kwargs['data'].decode())

    # ── customerCountry ──────────────────────────────────────────────────────
    # DPO rejects the whole transaction with "902 Data mismatch" unless this is
    # an ISO 3166-1 alpha-2 code, but the application stores full country names.
    def country_sent(self, post):
        """The customerCountry actually present in the XML, or None."""
        body = post.call_args.kwargs['data'].decode()
        match = re.search(r'<customerCountry>([^<]*)</customerCountry>', body)
        return match.group(1) if match else None

    def test_full_country_name_is_converted_to_an_iso_code(self):
        self.org.country = 'Botswana'
        self.org.save(update_fields=['country'])
        with canned(CREATE_OK) as post:
            self.start(DPOProvider())
        self.assertEqual(self.country_sent(post), 'BW')

    def test_country_is_never_sent_as_anything_but_two_letters(self):
        """The invariant that matters: whatever ends up in the request, it is
        either absent or exactly two letters. Checked across the whole country
        list rather than one example, so a name added without a code is caught
        here instead of by a declined payment."""
        from monitor.onboarding import COUNTRY_CHOICES

        for name in COUNTRY_CHOICES:
            self.org.country = name
            self.org.save(update_fields=['country'])
            with canned(CREATE_OK) as post:
                self.start(DPOProvider())
            sent = self.country_sent(post)
            with self.subTest(country=name):
                if sent is not None:
                    self.assertRegex(sent, r'^[A-Z]{2}$')

    def test_every_selectable_country_except_other_has_a_code(self):
        """'Other' is a real dropdown choice but not a country, so it alone is
        allowed to have no code."""
        from monitor.onboarding import COUNTRY_CHOICES, country_alpha2

        missing = [c for c in COUNTRY_CHOICES if c != 'Other' and not country_alpha2(c)]
        self.assertEqual(missing, [], f'No ISO alpha-2 code for: {missing}')

    def test_an_unmappable_country_is_omitted_rather_than_guessed(self):
        """An invalid code fails the payment; a missing optional field does not."""
        self.org.country = 'Other'
        self.org.save(update_fields=['country'])
        with canned(CREATE_OK) as post:
            self.start(DPOProvider())
        self.assertIsNone(self.country_sent(post))

    def test_a_country_already_stored_as_a_code_passes_through(self):
        self.org.country = 'BW'
        self.org.save(update_fields=['country'])
        with canned(CREATE_OK) as post:
            self.start(DPOProvider())
        self.assertEqual(self.country_sent(post), 'BW')

    def test_create_token_failure_leaves_an_unopened_payment_row(self):
        """A refused createToken must still leave a trace, with no gateway
        reference on it — that is how an attempt that never reached DPO is told
        apart from one the customer abandoned."""
        with canned(xml('<Result>904</Result><ResultExplanation>Unsupported currency</ResultExplanation>')):
            with self.assertRaises(PaymentError):
                DPOProvider().start_checkout(self.org, Decimal('299.00'), 'BWP', user=self.user)

        payment = Payment.objects.get()
        self.assertEqual(payment.provider_reference, '')
        self.assertEqual(payment.status, 'pending')

    def test_our_own_mistakes_are_configuration_errors(self):
        """802 means our company token is wrong. That is not a declined card and
        must not be shown to a customer as one."""
        with canned(xml('<Result>802</Result><ResultExplanation>Company token does not exist</ResultExplanation>')):
            with self.assertRaises(PaymentConfigurationError):
                DPOProvider().start_checkout(self.org, Decimal('299.00'), 'USD', user=self.user)

    # ── verifyToken ──────────────────────────────────────────────────────────
    def test_paid_transaction_is_marked_paid_with_a_settlement_date(self):
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with canned(verify_xml('000')):
            result = provider.verify_checkout(payment)

        payment.refresh_from_db()
        self.assertTrue(result.succeeded)
        self.assertEqual(payment.status, 'succeeded')
        self.assertIsNotNone(payment.paid_at)
        # The seven-day holding rule applies to a gateway charge exactly as it
        # does to an EFT recorded by hand.
        self.assertEqual(payment.settlement_hold_days, 7)
        self.assertIsNotNone(payment.settlement_available_at)

    def test_unpaid_transaction_stays_pending(self):
        """900 means the customer has not finished paying. Recording that as a
        failure would close off a payment that is still in progress."""
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with canned(verify_xml('900', explanation='Transaction not paid yet')):
            result = provider.verify_checkout(payment)

        payment.refresh_from_db()
        self.assertFalse(result.succeeded)
        self.assertEqual(payment.status, 'pending')

    def test_declined_transaction_fails_with_a_reason(self):
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with canned(verify_xml('901', explanation='Transaction declined')):
            result = provider.verify_checkout(payment)

        payment.refresh_from_db()
        self.assertFalse(result.succeeded)
        self.assertEqual(payment.status, 'failed')
        self.assertEqual(payment.failure_reason, 'Transaction declined')

    def test_underpayment_is_not_treated_as_payment(self):
        """The whole point of re-reading the amount from DPO: a token paid for
        less than we asked must not activate the package."""
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with canned(verify_xml('000', amount='1.00')):
            result = provider.verify_checkout(payment)

        payment.refresh_from_db()
        self.assertFalse(result.succeeded)
        self.assertNotEqual(payment.status, 'succeeded')

    def test_payment_in_the_wrong_currency_is_not_treated_as_payment(self):
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with canned(verify_xml('000', amount='299.00', currency='TZS')):
            result = provider.verify_checkout(payment)

        payment.refresh_from_db()
        self.assertFalse(result.succeeded)
        self.assertNotEqual(payment.status, 'succeeded')

    def test_unknown_result_code_fails_closed(self):
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with canned(verify_xml('777', explanation='')):
            result = provider.verify_checkout(payment)

        payment.refresh_from_db()
        self.assertFalse(result.succeeded)
        self.assertEqual(payment.status, 'failed')

    def test_verifying_twice_does_not_call_dpo_again_or_move_the_settlement_date(self):
        """The return page gets refreshed and the webhook fires independently.
        Both land here, and the second must be a no-op."""
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with canned(verify_xml('000')):
            provider.verify_checkout(payment)
        payment.refresh_from_db()
        first_settlement = payment.settlement_available_at

        with patch('requests.post') as post:
            result = provider.verify_checkout(payment)

        post.assert_not_called()
        self.assertTrue(result.succeeded)
        payment.refresh_from_db()
        self.assertEqual(payment.settlement_available_at, first_settlement)

    def test_unreachable_gateway_is_a_payment_error_not_a_crash(self):
        import requests
        provider = DPOProvider()
        with canned(CREATE_OK):
            payment = self.start(provider).payment
        with patch('requests.post', side_effect=requests.ConnectionError('boom')):
            with self.assertRaises(PaymentError):
                provider.verify_checkout(payment)

    def test_company_token_is_never_written_to_the_logs(self):
        with canned(xml('<Result>802</Result><ResultExplanation>bad token</ResultExplanation>')):
            with self.assertLogs('monitor.payments.dpo', level='ERROR') as logs:
                with self.assertRaises(PaymentConfigurationError):
                    DPOProvider().start_checkout(self.org, Decimal('299.00'), 'USD', user=self.user)
        self.assertNotIn(DPO_SETTINGS['DPO_COMPANY_TOKEN'], '\n'.join(logs.output))
