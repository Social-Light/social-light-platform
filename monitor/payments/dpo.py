"""DPO Pay (Direct Pay Online, by Network International) — a hosted-redirect gateway.

Unlike Stripe, DPO never hands this application a reusable card token. Every
payment is its own short-lived session at DPO's own checkout page:

    1. ``createToken``  — we describe the payment (amount, currency, reference,
       where to send the customer afterwards) and DPO returns a ``TransToken``.
    2. The browser is sent to ``.../payv3.php?ID=<TransToken>`` where the customer
       pays. Card details are entered on DPO's page and never touch this server.
    3. ``verifyToken``  — we ask DPO what became of that token.

Step 3 is the only thing that decides whether money moved. DPO redirects the
customer back with ``TransID``, ``CCDapproval``, ``TransactionToken`` and
``CompanyRef`` in the query string, and none of that is trustworthy: it is
attacker-controlled text arriving in a GET from a browser. It is used to find the
local :class:`Payment` row and for nothing else. The verdict always comes from a
server-to-server ``verifyToken`` call.

The API is XML over HTTPS POST to a single endpoint, versioned in the path. There
is no separate sandbox host — DPO issues *test company tokens* that work against
the same live endpoint, which is why ``DPO_COMPANY_TOKEN`` must never be
committed: the only thing separating a test integration from a live one is the
value of that setting.
"""
import logging
from decimal import Decimal
from xml.etree import ElementTree

from django.conf import settings
from django.utils import timezone

from ..payment_models import Payment
from .base import (ChargeResult, CheckoutSession, PaymentConfigurationError,
                   PaymentError, PaymentProvider)

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = 'https://secure.3gdirectpay.com/API/v6/'
DEFAULT_PAYMENT_URL = 'https://secure.3gdirectpay.com/payv3.php'

#: DPO returns a three-digit ``Result`` on every call. ``000`` is the only one
#: that means the call did what was asked.
RESULT_OK = '000'

#: ``verifyToken`` outcomes, grouped by what we should *do* rather than by DPO's
#: numbering. Anything not listed is treated as a failure, which is the safe
#: default: an unrecognised code must never activate a subscription.
VERIFY_PAID = {'000'}                                  # Transaction paid
VERIFY_PENDING = {
    '001',    # Authorised, not yet captured or settled
    '003',    # Pending at bank
    '005',    # Queued for authorisation
    '007',    # Partially paid / split payment pending
    '900',    # Not yet paid — the customer is still on the checkout page
}
VERIFY_REVIEW = {'002'}                                # Overpaid / underpaid
VERIFY_FAILED = {
    '901',    # Declined
    '903',    # Payment time limit exceeded
    '904',    # Cancelled
}

#: Codes that mean *we* sent something wrong, not that the customer's payment
#: failed. Surfaced as a configuration error so a broken integration is loud in
#: staging instead of looking like a customer's card being declined.
CONFIG_RESULTS = {
    '801': 'Missing CompanyToken.',
    '802': 'The configured DPO company token does not exist.',
    '803': 'Invalid request name.',
    '804': 'DPO could not parse the XML we sent.',
    '902': 'Data mismatch.',
    '950': 'A mandatory field was missing from the request.',
}


class DPOProvider(PaymentProvider):
    key = 'dpo'
    label = 'DPO Pay'
    collects_card = False        # the card is entered on DPO's page, not ours
    is_redirect = True

    # ── Configuration ────────────────────────────────────────────────────────
    @property
    def company_token(self):
        return getattr(settings, 'DPO_COMPANY_TOKEN', '') or ''

    @property
    def endpoint(self):
        return getattr(settings, 'DPO_ENDPOINT', '') or DEFAULT_ENDPOINT

    @property
    def payment_url(self):
        """Base URL of DPO's hosted checkout page.

        Configurable because DPO's own material is inconsistent about it — the
        integration email specifies ``payv3.php``, the createToken reference page
        says ``payv2.php`` and the hosted-checkout page says ``pay.asp``. The
        default follows the email, since that is what was issued alongside our
        company token, but a deployment can correct it without a code change.
        """
        return getattr(settings, 'DPO_PAYMENT_URL', '') or DEFAULT_PAYMENT_URL

    @property
    def service_type(self):
        """The DPO service type this subscription is billed under. Issued by DPO
        together with the company token; the two are a matched pair and cannot be
        mixed between accounts."""
        return str(getattr(settings, 'DPO_SERVICE_TYPE', '') or '')

    @property
    def is_enabled(self):
        return bool(self.company_token and self.service_type)

    @property
    def payment_time_limit(self):
        """How long the customer has to complete payment before DPO expires the
        token, in hours. DPO defaults to 96, which is far too long for a
        subscription checkout: a token left open for four days is four days in
        which the price list may change underneath it."""
        return int(getattr(settings, 'DPO_PAYMENT_TIME_LIMIT_HOURS', 2))

    # ── Transport ────────────────────────────────────────────────────────────
    def _require_config(self):
        if not self.company_token:
            raise PaymentConfigurationError(
                'PAYMENT_PROVIDER is set to "dpo" but DPO_COMPANY_TOKEN is not configured.')
        if not self.service_type:
            raise PaymentConfigurationError(
                'PAYMENT_PROVIDER is set to "dpo" but DPO_SERVICE_TYPE is not configured. '
                'DPO issues a service type alongside each company token.')

    def _post(self, root):
        """POST an ``<API3G>`` document and return the parsed response element.

        Network and parse failures become :class:`PaymentError` so callers have
        exactly one exception type to handle. The request body is never logged —
        it carries the company token, which is the credential for the whole
        merchant account.
        """
        import requests

        body = (b'<?xml version="1.0" encoding="utf-8"?>'
                + ElementTree.tostring(root, encoding='utf-8', xml_declaration=False))

        try:
            response = requests.post(
                self.endpoint, data=body,
                headers={'Content-Type': 'application/xml; charset=utf-8'},
                timeout=getattr(settings, 'DPO_TIMEOUT_SECONDS', 30),
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning('DPO request failed: %s', exc)
            raise PaymentError(
                'We could not reach the payment gateway. Please try again in a moment.') from exc

        try:
            return ElementTree.fromstring(response.content)
        except ElementTree.ParseError as exc:
            logger.warning('DPO returned unparseable XML (%s bytes)', len(response.content))
            raise PaymentError('The payment gateway returned an unreadable response.') from exc

    @staticmethod
    def _text(element, tag, default=''):
        found = element.find(tag)
        return (found.text or '').strip() if found is not None and found.text else default

    def _check_result(self, element, operation):
        """Return ``(result_code, explanation)``, raising for our own mistakes.

        A configuration-class code is raised rather than returned because no
        amount of retrying by a customer will fix it — it needs a developer.
        """
        result = self._text(element, 'Result')
        explanation = self._text(element, 'ResultExplanation')
        if result in CONFIG_RESULTS:
            logger.error('DPO %s rejected our request: %s %s (%s)',
                         operation, result, CONFIG_RESULTS[result], explanation)
            raise PaymentConfigurationError(
                f'DPO rejected the {operation} request ({result}): '
                f'{CONFIG_RESULTS[result]} {explanation}'.strip())
        return result, explanation

    # ── API calls ────────────────────────────────────────────────────────────
    def create_token(self, *, amount, currency, company_ref, description,
                     redirect_url='', back_url='', customer=None, metadata=''):
        """``createToken`` — open a transaction and get the checkout token back."""
        self._require_config()

        root = ElementTree.Element('API3G')
        ElementTree.SubElement(root, 'CompanyToken').text = self.company_token
        ElementTree.SubElement(root, 'Request').text = 'createToken'

        transaction = ElementTree.SubElement(root, 'Transaction')
        # DPO rejects more than two decimal places outright.
        ElementTree.SubElement(transaction, 'PaymentAmount').text = f'{Decimal(str(amount)):.2f}'
        ElementTree.SubElement(transaction, 'PaymentCurrency').text = (currency or 'USD').upper()
        ElementTree.SubElement(transaction, 'CompanyRef').text = str(company_ref)
        # Our CompanyRef is the Payment row's UUID, so it is unique by
        # construction. Declaring that to DPO turns an accidental double submit
        # into a rejection at the gateway rather than two charges.
        ElementTree.SubElement(transaction, 'CompanyRefUnique').text = '1'
        ElementTree.SubElement(transaction, 'PTL').text = str(self.payment_time_limit)
        ElementTree.SubElement(transaction, 'PTLtype').text = 'hours'
        ElementTree.SubElement(transaction, 'TransactionSource').text = 'API'
        if redirect_url:
            ElementTree.SubElement(transaction, 'RedirectURL').text = redirect_url
        if back_url:
            ElementTree.SubElement(transaction, 'BackURL').text = back_url
        if metadata:
            ElementTree.SubElement(transaction, 'MetaData').text = str(metadata)[:2000]
        for field, value in (customer or {}).items():
            if value:
                ElementTree.SubElement(transaction, field).text = str(value)

        services = ElementTree.SubElement(root, 'Services')
        service = ElementTree.SubElement(services, 'Service')
        ElementTree.SubElement(service, 'ServiceType').text = self.service_type
        ElementTree.SubElement(service, 'ServiceDescription').text = description or 'Subscription'
        # Mandatory, and DPO is strict about the format.
        ElementTree.SubElement(service, 'ServiceDate').text = timezone.now().strftime('%Y/%m/%d %H:%M')

        response = self._post(root)
        result, explanation = self._check_result(response, 'createToken')
        if result != RESULT_OK:
            raise PaymentError(
                explanation or 'The payment gateway could not start this payment.')

        token = self._text(response, 'TransToken')
        if not token:
            raise PaymentError('The payment gateway did not return a payment token.')
        return {
            'token': token,
            'reference': self._text(response, 'TransRef'),
            'result': result,
            'explanation': explanation,
        }

    def verify_token(self, token):
        """``verifyToken`` — the authoritative answer on whether a token was paid."""
        self._require_config()

        root = ElementTree.Element('API3G')
        ElementTree.SubElement(root, 'CompanyToken').text = self.company_token
        ElementTree.SubElement(root, 'Request').text = 'verifyToken'
        ElementTree.SubElement(root, 'TransactionToken').text = str(token)

        response = self._post(root)
        result, explanation = self._check_result(response, 'verifyToken')
        return {
            'result': result,
            'explanation': explanation,
            'amount': self._text(response, 'TransactionAmount'),
            'currency': self._text(response, 'TransactionCurrency'),
            'reference': self._text(response, 'TransactionRef'),
            'approval': self._text(response, 'ApprovalNumber'),
            'customer_name': self._text(response, 'CustomerName'),
            'company_ref': self._text(response, 'CompanyRef'),
            'fraud_alert': self._text(response, 'TransactionFraudAlert'),
            'fraud_explanation': self._text(response, 'TransactionFraudExplanation'),
        }

    def checkout_url(self, token):
        separator = '&' if '?' in self.payment_url else '?'
        return f'{self.payment_url}{separator}ID={token}'

    # ── Contract ─────────────────────────────────────────────────────────────
    def start_checkout(self, organization, amount, currency, *, package=None,
                       description='', user=None, return_url='', callback_url=''):
        """Create the local Payment row first, then open the transaction at DPO.

        The row is written before the API call so that its UUID can be the
        ``CompanyRef`` — which is what lets the return leg find the payment
        again, and what makes DPO's own duplicate-reference check line up with
        ours. A row whose ``provider_reference`` stays empty is an attempt that
        never reached the gateway, and reads that way in the admin.
        """
        payment = Payment.objects.create(
            organization=organization,
            package=package,
            amount=amount,
            currency=(currency or 'USD').upper(),
            description=description or (package.name if package else 'Subscription'),
            provider=self.key,
            created_by=user,
            status='pending',
        )

        created = self.create_token(
            amount=payment.amount,
            currency=payment.currency,
            company_ref=payment.id,
            description=payment.description,
            redirect_url=return_url,
            back_url=callback_url,
            customer=self._customer_fields(organization, user),
            metadata=f'org={organization.id}; package={package.slug if package else "-"}',
        )

        payment.provider_reference = created['token']
        payment.save(update_fields=['provider_reference', 'updated_at'])

        return CheckoutSession(
            payment=payment,
            redirect_url=self.checkout_url(created['token']),
            reference=created['token'],
            raw=created,
        )

    @staticmethod
    def _customer_fields(organization, user):
        """Prefill what DPO's checkout page would otherwise ask for again.

        All optional — a missing field costs the customer a moment of typing, so
        nothing here is worth failing a payment over.
        """
        fields = {}
        if user is not None:
            fields['customerFirstName'] = user.first_name
            fields['customerLastName'] = user.last_name
            fields['customerEmail'] = user.email
            fields['customerPhone'] = getattr(user, 'phone', '')
        if organization is not None:
            fields.setdefault('customerEmail', organization.email or '')
            fields['customerCountry'] = getattr(organization, 'country', '') or ''
        return fields

    def verify_checkout(self, payment):
        """Ask DPO about ``payment`` and record the answer.

        Idempotent on purpose. The customer refreshes the return page, DPO calls
        the webhook, and a support agent re-checks from the admin — all three
        arrive here, and only the first to see a paid result writes anything.
        """
        if payment.status == 'succeeded':
            # Already settled. Do not call out again, and above all do not
            # recompute the settlement date — it is stamped once, when paid.
            return ChargeResult(reference=payment.provider_reference, succeeded=True)

        if not payment.provider_reference:
            return ChargeResult(reference='', succeeded=False,
                                failure_reason='This payment was never opened at the gateway.')

        verified = self.verify_token(payment.provider_reference)
        result = verified['result']

        if result in VERIFY_PAID and self._amount_matches(payment, verified):
            payment.mark_paid()
            return ChargeResult(reference=payment.provider_reference, succeeded=True,
                                raw=verified)

        if result in VERIFY_PAID:
            # Paid, but not for what we asked. Deliberately not marked
            # succeeded: activating a subscription on an amount we did not
            # charge is worse than making a human look at it.
            reason = (f'Paid {verified["currency"]} {verified["amount"]}, expected '
                      f'{payment.currency} {payment.amount}.')
            self._record_failure(payment, reason, status='pending')
            logger.error('DPO amount mismatch on payment %s: %s', payment.id, reason)
            return ChargeResult(reference=payment.provider_reference, succeeded=False,
                                failure_reason=reason, raw=verified)

        if result in VERIFY_REVIEW:
            reason = verified['explanation'] or 'Overpaid or underpaid.'
            self._record_failure(payment, reason, status='pending')
            return ChargeResult(reference=payment.provider_reference, succeeded=False,
                                failure_reason=reason, raw=verified)

        if result in VERIFY_PENDING:
            # Left pending, not failed: the customer may still be on DPO's page,
            # or the bank may still be deciding. A failed row here would close
            # off a payment that is about to succeed.
            return ChargeResult(reference=payment.provider_reference, succeeded=False,
                                failure_reason=verified['explanation'] or 'Payment not completed yet.',
                                raw=verified)

        reason = verified['explanation'] or f'The payment was not completed (code {result}).'
        self._record_failure(payment, reason)
        return ChargeResult(reference=payment.provider_reference, succeeded=False,
                            failure_reason=reason, raw=verified)

    @staticmethod
    def _amount_matches(payment, verified):
        """Guard against a token being paid for the wrong amount.

        DPO reports the amount it actually took. Comparing it to what we asked
        for is what stops a tampered or reused token activating a package it did
        not pay for. An unreadable amount fails closed.
        """
        try:
            paid = Decimal(verified['amount'])
        except Exception:
            return False
        currency_ok = not verified['currency'] or \
            verified['currency'].upper() == (payment.currency or '').upper()
        return currency_ok and paid >= Decimal(payment.amount)

    @staticmethod
    def _record_failure(payment, reason, status='failed'):
        payment.status = status
        payment.failure_reason = reason[:300]
        payment.save(update_fields=['status', 'failure_reason', 'updated_at'])
