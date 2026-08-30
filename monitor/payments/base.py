"""The payment provider contract.

Every provider-specific detail in this application lives behind this interface.
The rest of the codebase — the onboarding payment step, the billing page, the
admin — knows only about ``get_provider()``, the four methods below, and the
``PaymentMethod`` / ``Payment`` models. Swapping Stripe for a local acquirer means
writing one new subclass; it does not mean touching a view.

The interface is deliberately narrow, and deliberately never sees a card number.
The browser hands card details straight to the provider's own hosted field or
SDK, which returns an opaque token; that token is all
:meth:`PaymentProvider.attach_payment_method` ever receives.
"""
from dataclasses import dataclass, field


class PaymentError(Exception):
    """A payment operation failed in a way worth telling the user about."""


class PaymentsDisabled(PaymentError):
    """No payment gateway is configured for this deployment.

    Raised rather than returned so that a caller which forgot to check
    ``provider.is_enabled`` fails loudly in development instead of silently
    recording a payment that never happened.
    """


class PaymentConfigurationError(PaymentError):
    """A gateway is switched on but is missing credentials or its client library."""


@dataclass
class CardDetails:
    """The non-sensitive fragments a provider returns about a stored card.

    This is the complete list of what may be persisted about an instrument.
    There is no field here for a PAN, a CVV or any other authentication value,
    and adding one would be a defect.
    """
    token: str
    customer_id: str = ''
    brand: str = ''
    last4: str = ''
    exp_month: int = None
    exp_year: int = None
    holder_name: str = ''


@dataclass
class ChargeResult:
    reference: str
    succeeded: bool = True
    failure_reason: str = ''
    provider_settlement_at: object = None
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    """Base class. Subclasses override the four operations they support."""

    key = 'base'
    label = 'Payment provider'

    #: Whether this deployment can actually take money. When False the whole
    #: onboarding flow still works — the payment step explains that no card is
    #: being collected and lets the user continue.
    is_enabled = False

    #: Whether the front end should render the provider's card-capture widget.
    collects_card = False

    def client_config(self, request, organization):
        """Public configuration the browser needs to talk to the provider —
        publishable key, client secret for a setup intent, and so on. Never
        secret keys."""
        return {}

    def attach_payment_method(self, organization, user, token, **extra):
        """Exchange a provider token for a stored :class:`PaymentMethod`.

        `token` is whatever the provider's browser SDK produced. Implementations
        may call the provider to expand it into card metadata, but must never
        accept or forward raw card data.
        """
        raise PaymentsDisabled('No payment gateway is configured.')

    def detach_payment_method(self, payment_method):
        """Remove the stored instrument at the provider. Best-effort: local
        deactivation is the caller's job and must not depend on this succeeding."""
        return None

    def charge(self, organization, amount, currency, payment_method, description=''):
        """Take a payment. Returns a :class:`ChargeResult`."""
        raise PaymentsDisabled('No payment gateway is configured.')
