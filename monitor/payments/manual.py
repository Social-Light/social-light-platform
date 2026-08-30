"""The provider used when no payment gateway is switched on — which is the
default, and is how the platform has always worked: fees are settled by invoice
or EFT and a platform administrator activates the package.

This is a real provider implementation rather than a null object, because
"payments are off" is a supported production mode, not a broken configuration.
Onboarding completes, plans are assigned, entitlements apply and settlement dates
are still calculated — the only thing that does not happen is card capture.
"""
from django.utils import timezone

from ..payment_models import Payment
from .base import PaymentProvider, PaymentsDisabled


class ManualProvider(PaymentProvider):
    key = 'manual'
    label = 'Manual / off-platform settlement'
    is_enabled = False
    collects_card = False

    def attach_payment_method(self, organization, user, token, **extra):
        raise PaymentsDisabled(
            'Card payments are not enabled on this deployment. Your subscription is '
            'arranged with our team and settled by invoice or electronic transfer.')

    def charge(self, organization, amount, currency, payment_method, description=''):
        raise PaymentsDisabled('Card payments are not enabled on this deployment.')


def record_manual_payment(organization, *, amount, currency='USD', package=None,
                          description='', recorded_by=None, paid_at=None, hold_days=None):
    """Record a payment that was settled off-platform.

    Used by the admin when a bank transfer lands, so that an organisation on
    manual billing still has a payment history and a settlement eligibility date
    computed by exactly the same rule as a gateway charge — the seven-day holding
    period is a business rule about our funds, and it does not stop applying
    because the money arrived by EFT.
    """
    payment = Payment.objects.create(
        organization=organization,
        package=package,
        amount=amount,
        currency=currency,
        description=description or 'Off-platform settlement (invoice / EFT)',
        provider=ManualProvider.key,
        created_by=recorded_by,
        status='pending',
    )
    payment.mark_paid(when=paid_at or timezone.now(), hold_days=hold_days)
    return payment
