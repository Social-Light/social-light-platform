"""Payment records.

Two hard rules shape this module.

**No sensitive authentication data is ever stored.** There is no field here for a
card number, a CVV, a PIN, a magnetic-stripe track or a 3-D Secure value, and
there never should be. What is stored is the *token* the payment provider issues
in exchange for the card — an opaque reference that is useless to anyone who does
not also hold our provider credentials — plus the non-sensitive display fragments
(brand, last four digits, expiry) that let a user recognise which card they saved.
The card details themselves are entered directly into the provider's own hosted
field or SDK in the browser and never reach this application.

**Settlement is a business rule, not a payment-provider fact.** The requirement is
that funds become eligible for extraction seven days after payment.
``Payment.settlement_available_at`` records when *our* holding period elapses,
computed from a configurable ``PAYMENT_SETTLEMENT_HOLD_DAYS``. It says nothing
about when the provider will actually release the money: acquirers, card schemes
and the provider's own risk rules govern that, and they are outside this
application's control. Both dates matter, so both are stored — ours as the
business rule, the provider's as whatever it reports.
"""
import uuid
from datetime import timedelta

from django.conf import settings as django_settings
from django.db import models
from django.utils import timezone


def settlement_hold_days():
    """The configured holding period before funds are treated as eligible for
    extraction. Seven days is the business default; deployments override it with
    PAYMENT_SETTLEMENT_HOLD_DAYS."""
    return int(getattr(django_settings, 'PAYMENT_SETTLEMENT_HOLD_DAYS', 7))


class PaymentMethod(models.Model):
    """A tokenised payment instrument belonging to an organisation.

    ``provider_token`` is the provider's identifier for the stored card (a Stripe
    ``pm_…``, for example). It is meaningless without our provider API key, and
    it is the only thing about the card this system holds.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('monitor.Organization', on_delete=models.CASCADE,
                                     related_name='payment_methods')
    added_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='payment_methods_added')

    provider = models.CharField(max_length=40, help_text='Which payment provider issued the token.')
    provider_customer_id = models.CharField(max_length=200, blank=True)
    provider_token = models.CharField(
        max_length=200,
        help_text="The provider's token for the stored card. Never a card number.")

    # Display fragments only. Everything here is safe to show a user and is what
    # the provider itself returns for exactly that purpose.
    brand = models.CharField(max_length=40, blank=True, help_text='e.g. "visa", "mastercard".')
    last4 = models.CharField(max_length=4, blank=True)
    exp_month = models.PositiveSmallIntegerField(null=True, blank=True)
    exp_year = models.PositiveSmallIntegerField(null=True, blank=True)
    holder_name = models.CharField(max_length=200, blank=True)

    is_default = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return self.display_label

    @property
    def display_label(self):
        brand = (self.brand or 'Card').title()
        return f'{brand} ending {self.last4}' if self.last4 else f'{brand} (tokenised)'

    @property
    def expiry_display(self):
        if self.exp_month and self.exp_year:
            return f'{self.exp_month:02d}/{str(self.exp_year)[-2:]}'
        return ''

    @property
    def is_expired(self):
        if not (self.exp_month and self.exp_year):
            return False
        now = timezone.now()
        return (self.exp_year, self.exp_month) < (now.year, now.month)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            # One default per organisation.
            PaymentMethod.objects.filter(organization=self.organization) \
                                 .exclude(pk=self.pk).update(is_default=False)


PAYMENT_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('succeeded', 'Succeeded'),
    ('failed', 'Failed'),
    ('refunded', 'Refunded'),
]

SETTLEMENT_STATUS_CHOICES = [
    ('holding', 'Within holding period'),
    ('eligible', 'Eligible for extraction'),
    ('settled', 'Settled'),
    ('not_applicable', 'Not applicable'),
]


class Payment(models.Model):
    """One payment attempt against an organisation's subscription.

    Created for real gateway charges and, when the gateway is disabled, for
    manually recorded off-platform settlements (invoice/EFT) so the subscription
    and settlement history is complete either way.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey('monitor.Organization', on_delete=models.CASCADE,
                                     related_name='payments')
    package = models.ForeignKey('monitor.Package', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='payments')
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='payments')
    created_by = models.ForeignKey(django_settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='payments_created')

    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default='USD')
    description = models.CharField(max_length=300, blank=True)

    provider = models.CharField(max_length=40, blank=True)
    provider_reference = models.CharField(max_length=200, blank=True,
                                          help_text="The provider's own id for this charge.")
    status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    failure_reason = models.CharField(max_length=300, blank=True)

    # ── Settlement ───────────────────────────────────────────────────────────
    paid_at = models.DateTimeField(null=True, blank=True)
    settlement_hold_days = models.PositiveSmallIntegerField(
        default=settlement_hold_days,
        help_text='The holding period applied to this payment, captured at the time it was taken '
                  'so a later change to the configured default does not rewrite history.')
    settlement_available_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When OUR holding period elapses. The payment provider’s own settlement '
                  'timetable is separate and is not controlled by this application.')
    provider_settlement_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the provider reports the funds will actually be, or were, released.')
    settled_at = models.DateTimeField(null=True, blank=True,
                                      help_text='When the funds were actually extracted.')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['organization', '-created_at'])]

    def __str__(self):
        return f'{self.organization.name} {self.currency} {self.amount} ({self.status})'

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def mark_paid(self, when=None, hold_days=None, provider_reference='', save=True):
        """Record a successful payment and compute when our holding period ends.

        The holding period is stamped onto the row rather than read back from
        settings later, so changing PAYMENT_SETTLEMENT_HOLD_DAYS affects future
        payments only and never retroactively moves an existing eligibility date.
        """
        self.paid_at = when or timezone.now()
        self.settlement_hold_days = settlement_hold_days() if hold_days is None else int(hold_days)
        self.settlement_available_at = self.paid_at + timedelta(days=self.settlement_hold_days)
        self.status = 'succeeded'
        if provider_reference:
            self.provider_reference = provider_reference
        if save:
            self.save(update_fields=['paid_at', 'settlement_hold_days', 'settlement_available_at',
                                     'status', 'provider_reference', 'updated_at'])
        return self

    @property
    def is_settlement_eligible(self):
        """True once our own holding period has elapsed. Says nothing about
        whether the provider has released the funds."""
        if self.status != 'succeeded' or not self.settlement_available_at:
            return False
        return timezone.now() >= self.settlement_available_at

    @property
    def settlement_status(self):
        if self.status != 'succeeded':
            return 'not_applicable'
        if self.settled_at:
            return 'settled'
        return 'eligible' if self.is_settlement_eligible else 'holding'

    @property
    def settlement_days_remaining(self):
        if self.settlement_available_at is None:
            return 0
        remaining = (self.settlement_available_at - timezone.now()).total_seconds()
        return max(0, int(-(-remaining // 86400)))      # ceiling, never negative
