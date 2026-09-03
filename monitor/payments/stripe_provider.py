"""Stripe, as a worked example of a tokenising gateway.

Nothing here is imported unless ``PAYMENT_PROVIDER = 'stripe'``, and the ``stripe``
library is imported lazily inside the methods, so the package is not a dependency
of the application — a deployment that never turns Stripe on never needs it
installed.

The tokenisation path is the point of this file. The browser collects the card in
Stripe's own hosted Element, confirms a SetupIntent against it, and hands us back
a ``pm_…`` identifier. That identifier is the only thing that reaches this server
or the database. At no point does a card number, expiry-plus-CVV pair or any
other authentication value pass through our code, which is what keeps the
application out of PCI-DSS scope beyond SAQ-A.
"""
import logging

from django.conf import settings

from ..payment_models import PaymentMethod
from .base import (CardDetails, ChargeResult, PaymentConfigurationError,
                   PaymentError, PaymentProvider)

logger = logging.getLogger(__name__)


class StripeProvider(PaymentProvider):
    key = 'stripe'
    label = 'Stripe'
    collects_card = True

    @property
    def is_enabled(self):
        return bool(getattr(settings, 'STRIPE_SECRET_KEY', ''))

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _stripe(self):
        try:
            import stripe
        except ImportError as exc:                    # pragma: no cover - config error path
            raise PaymentConfigurationError(
                'PAYMENT_PROVIDER is set to "stripe" but the stripe library is not installed. '
                'Run: pip install stripe'
            ) from exc
        secret = getattr(settings, 'STRIPE_SECRET_KEY', '')
        if not secret:
            raise PaymentConfigurationError(
                'PAYMENT_PROVIDER is set to "stripe" but STRIPE_SECRET_KEY is not configured.')
        stripe.api_key = secret
        return stripe

    def _customer_id(self, organization):
        """Reuse the organisation's existing Stripe customer if we have already
        stored an instrument for it, so cards do not scatter across customers."""
        existing = organization.payment_methods.filter(
            provider=self.key, provider_customer_id__gt='').first()
        if existing:
            return existing.provider_customer_id

        stripe = self._stripe()
        customer = stripe.Customer.create(
            name=organization.name,
            email=organization.email or None,
            metadata={'organization_id': str(organization.id)},
        )
        return customer['id']

    # ── contract ─────────────────────────────────────────────────────────────
    def client_config(self, request, organization):
        """Publishable key plus a SetupIntent client secret. Both are safe to
        expose to the browser; the secret key never leaves the server."""
        stripe = self._stripe()
        customer_id = self._customer_id(organization)
        intent = stripe.SetupIntent.create(
            customer=customer_id,
            usage='off_session',
            metadata={'organization_id': str(organization.id)},
        )
        return {
            'provider': self.key,
            'publishable_key': getattr(settings, 'STRIPE_PUBLISHABLE_KEY', ''),
            'client_secret': intent['client_secret'],
            'customer_id': customer_id,
        }

    def attach_payment_method(self, organization, user, token, **extra):
        """`token` is a Stripe PaymentMethod id produced in the browser."""
        stripe = self._stripe()
        customer_id = extra.get('customer_id') or self._customer_id(organization)

        try:
            pm = stripe.PaymentMethod.attach(token, customer=customer_id)
            stripe.Customer.modify(
                customer_id, invoice_settings={'default_payment_method': token})
        except Exception as exc:                       # provider errors are user-facing
            logger.warning('Stripe attach failed for org %s: %s', organization.id, exc)
            raise PaymentError('We could not save that card. Please check the details and try again.') from exc

        card = (pm.get('card') or {}) if isinstance(pm, dict) else {}
        details = CardDetails(
            token=token,
            customer_id=customer_id,
            brand=card.get('brand', ''),
            last4=card.get('last4', ''),
            exp_month=card.get('exp_month'),
            exp_year=card.get('exp_year'),
            holder_name=((pm.get('billing_details') or {}).get('name') or '') if isinstance(pm, dict) else '',
        )
        return PaymentMethod.objects.create(
            organization=organization,
            added_by=user,
            provider=self.key,
            provider_customer_id=details.customer_id,
            provider_token=details.token,
            brand=details.brand,
            last4=details.last4,
            exp_month=details.exp_month,
            exp_year=details.exp_year,
            holder_name=details.holder_name,
            is_default=True,
        )

    def detach_payment_method(self, payment_method):
        try:
            self._stripe().PaymentMethod.detach(payment_method.provider_token)
        except Exception as exc:                       # pragma: no cover - best effort
            logger.warning('Stripe detach failed for %s: %s', payment_method.pk, exc)

    def charge(self, organization, amount, currency, payment_method, description=''):
        stripe = self._stripe()
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(round(float(amount) * 100)),   # smallest currency unit
                currency=(currency or 'USD').lower(),
                customer=payment_method.provider_customer_id or None,
                payment_method=payment_method.provider_token,
                off_session=True,
                confirm=True,
                description=description,
                metadata={'organization_id': str(organization.id)},
            )
        except Exception as exc:
            logger.warning('Stripe charge failed for org %s: %s', organization.id, exc)
            return ChargeResult(reference='', succeeded=False, failure_reason=str(exc)[:300])

        return ChargeResult(
            reference=intent.get('id', ''),
            succeeded=intent.get('status') == 'succeeded',
            failure_reason='' if intent.get('status') == 'succeeded' else str(intent.get('status')),
        )
