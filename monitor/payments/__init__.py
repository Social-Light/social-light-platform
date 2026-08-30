"""Payment provider selection.

``get_provider()`` is the only way the rest of the application obtains a
provider. Two settings decide what it returns:

    PAYMENTS_ENABLED = False        # the master switch
    PAYMENT_PROVIDER = 'manual'     # which gateway, when payments are on

With ``PAYMENTS_ENABLED`` off — the default, and the platform's current
behaviour — the manual provider is returned no matter what ``PAYMENT_PROVIDER``
says. That ordering is deliberate: turning payments off is a single switch that
cannot be defeated by a stale provider setting, and a deployment with no gateway
credentials cannot crash on start-up because the provider is never constructed.

``PAYMENT_PROVIDER`` also accepts a dotted path to a ``PaymentProvider`` subclass,
so a local acquirer can be plugged in from outside this package.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from .base import (CardDetails, ChargeResult, PaymentConfigurationError, PaymentError,
                   PaymentProvider, PaymentsDisabled)
from .manual import ManualProvider, record_manual_payment

#: Providers shipped with the platform, by key.
BUILTIN_PROVIDERS = {
    'manual': 'monitor.payments.manual.ManualProvider',
    'stripe': 'monitor.payments.stripe_provider.StripeProvider',
}

__all__ = [
    'CardDetails', 'ChargeResult', 'ManualProvider', 'PaymentConfigurationError',
    'PaymentError', 'PaymentProvider', 'PaymentsDisabled', 'get_provider',
    'payments_enabled', 'record_manual_payment',
]


def payments_enabled():
    return bool(getattr(settings, 'PAYMENTS_ENABLED', False))


def get_provider():
    """The payment provider for this deployment.

    Never raises for a missing gateway: an unconfigured deployment gets the
    manual provider and keeps working. It does raise for a *misconfigured* one —
    a PAYMENT_PROVIDER naming a class that cannot be imported is a mistake worth
    surfacing rather than silently falling back from.
    """
    if not payments_enabled():
        return ManualProvider()

    name = getattr(settings, 'PAYMENT_PROVIDER', 'manual') or 'manual'
    path = BUILTIN_PROVIDERS.get(name, name)
    try:
        provider_class = import_string(path)
    except ImportError as exc:
        raise ImproperlyConfigured(
            f'PAYMENT_PROVIDER={name!r} could not be resolved. Use one of '
            f'{sorted(BUILTIN_PROVIDERS)} or a dotted path to a PaymentProvider subclass.'
        ) from exc

    provider = provider_class()
    if not isinstance(provider, PaymentProvider):
        raise ImproperlyConfigured(f'{path} is not a PaymentProvider subclass.')
    return provider
