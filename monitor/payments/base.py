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

There are two shapes of gateway behind this interface, and ``is_redirect`` says
which one a provider is.

*Card-on-file* gateways (Stripe) tokenise a card in the browser, hand back a
reusable instrument, and let us charge it whenever a subscription falls due.
Those implement ``attach_payment_method`` and ``charge``.

*Hosted-redirect* gateways (DPO Pay) do not give us a reusable instrument at all.
Each payment is its own session: we ask the gateway to open one, send the
customer to the gateway's own page to pay, and ask the gateway afterwards what
happened. Those implement ``start_checkout`` and ``verify_checkout`` instead, and
the card never exists as far as this application is concerned — there is nothing
to store and nothing to re-charge.

Neither shape is the "real" one. A view asks ``provider.is_redirect`` and takes
the matching path, which is what lets one deployment run Stripe and another run
DPO without either flow knowing the other exists.
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


@dataclass
class CheckoutSession:
    """A hosted-redirect payment that has been opened at the gateway but not yet
    paid. ``redirect_url`` is where the customer's browser must be sent."""
    payment: object
    redirect_url: str
    reference: str = ''
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    """Base class. Subclasses override the operations they support."""

    key = 'base'
    label = 'Payment provider'

    #: Whether this deployment can actually take money. When False the whole
    #: onboarding flow still works — the payment step explains that no card is
    #: being collected and lets the user continue.
    is_enabled = False

    #: Whether the front end should render the provider's card-capture widget.
    collects_card = False

    #: Whether this provider works by sending the customer to a page it hosts.
    #: True selects the ``start_checkout`` / ``verify_checkout`` pair below;
    #: False selects ``attach_payment_method`` / ``charge``.
    is_redirect = False

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

    # ── Hosted-redirect flow ─────────────────────────────────────────────────
    # Only meaningful when ``is_redirect`` is True. Split into two calls because
    # the customer leaves the site in between: everything the gateway needs is
    # sent in the first, and the answer is only available in the second.

    def start_checkout(self, organization, amount, currency, *, package=None,
                       description='', user=None, return_url='', callback_url=''):
        """Open a payment at the gateway and return a :class:`CheckoutSession`.

        Creates the local :class:`~monitor.payment_models.Payment` row in
        ``pending`` so there is a record of the attempt even if the customer
        abandons the gateway's page and never comes back.
        """
        raise PaymentsDisabled('No payment gateway is configured.')

    def verify_checkout(self, payment):
        """Ask the gateway what became of a started checkout.

        This — not anything the browser carries back in a query string — is what
        decides whether a payment happened. Must be safe to call repeatedly and
        at any time: the customer will refresh the return page, the gateway may
        call the webhook more than once, and both land here.
        """
        raise PaymentsDisabled('No payment gateway is configured.')
