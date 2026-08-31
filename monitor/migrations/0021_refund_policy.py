"""Add the Refund & Cancellation Policy document type and seed version 1.0.

⚠️  THE WORDING BELOW IS A PRODUCT DRAFT, NOT LEGAL ADVICE, AND IT CONTAINS
    COMMERCIAL TERMS NOBODY HAS SIGNED OFF.

A published refund and cancellation policy is a card-scheme and acquirer
requirement — DPO asked for one before issuing live credentials — so this exists
to give the site a real, coherent policy page rather than a dead footer link.

What is written here was derived from what the platform *actually does*: a
14-day free trial that takes no card, packages billed monthly or annually, and
access that runs to the end of a paid period. Those parts are factual.

The parts that are **commercial decisions and need Tony's sign-off** are called
out in ``review_note`` and are deliberately conservative in the meantime. The row
is stored with ``review_status='draft'``, so the public page carries a visible
"Draft — pending legal review" chip until someone approves it in the admin.

Changing this text does not need a code change: edit the version in the Django
admin while it is a draft, or publish a new row with a new version number.
"""
from django.db import migrations, models
from django.utils import timezone


REFUND_SUMMARY = (
    'How subscriptions are billed, how to cancel, and when we refund. In short: '
    'the trial is free and takes no card, you can cancel at any time, and you keep '
    'access until the period you have paid for ends.'
)

REFUND_BODY = """1. What this policy covers

This policy explains how Social Light subscriptions are billed, how you cancel
one, and when you are entitled to a refund. It applies to subscriptions bought
through the Social Light platform. Where you hold a separately signed agreement
with us, that agreement takes precedence over this policy.

2. The free trial

New organisations receive a 14-day free trial. We do not ask for card details to
start it and nothing is charged during it. If you do not choose a package before
the trial ends, access simply stops. There is nothing to cancel and nothing to
refund.

3. How subscriptions are billed

Packages are billed in advance, either monthly or annually, at the price shown on
our published price list at the time of purchase. The same price list applies to
every client. Payment is taken through our payment provider; we do not hold your
card details.

4. Cancelling a subscription

You may cancel at any time by writing to admin@sociallightbw.com from the email
address associated with your account, or by asking your account contact. We will
confirm the cancellation in writing.

Cancellation takes effect at the end of the period you have already paid for. You
keep full access until then, and you are not billed again. We do not pro-rate a
partly used month or year, because access and the monitoring behind it continue
for the whole of it.

5. Refunds

We refund a payment in full where:

- the payment was taken in error, was duplicated, or was for an amount other than
  the price of the package you selected;
- we are unable to provide the service you paid for; or
- you cancel within 14 days of your first payment for a new subscription and have
  not downloaded reports or exported data during that period.

We do not usually refund a subscription period that has already begun and been
used, or a renewal you did not cancel before it was taken. Where a renewal was
taken very shortly before you asked to cancel, tell us and we will look at it on
its facts rather than applying this rule mechanically.

6. Failed and disputed payments

If a payment fails, your access continues and we will contact you to arrange
settlement before anything is suspended. If you believe a charge is wrong, please
contact us before raising a dispute with your bank — we can almost always resolve
it faster directly, and a chargeback raised in error costs both of us time.

7. How to ask for a refund

Write to admin@sociallightbw.com with the organisation name, the date and amount
of the payment, and what went wrong. We aim to acknowledge within two working
days and to resolve within ten.

Approved refunds are made to the original payment method. The time it then takes
to appear on your statement is set by your bank and by the card scheme, and is
outside our control.

8. Changes to this policy

We may update this policy. The version in force is the one published on this page
at the time of your payment, and we will not apply a later change retrospectively
to a payment already taken.

9. Contact

Social Light (Pty) Ltd
Plot 59065, Gaborone, Botswana
admin@sociallightbw.com"""

REVIEW_NOTE = """NOT YET APPROVED. The following are commercial decisions taken as
conservative placeholders and need sign-off from the business before this is
presented to DPO or to clients as settled:

  * Clause 5, first bullet list — the 14-day cooling-off refund on a first
    payment, and the condition that no reports were downloaded in that window.
    Confirm both the window and the condition, or remove the bullet.
  * Clause 4 — no pro-rating of a partly used period. Confirm this is the
    intended commercial position for annual plans as well as monthly.
  * Clause 7 — the two working day acknowledgement and ten working day
    resolution targets. Confirm these are achievable before publishing.
  * Clause 9 — confirm the registered company name and the full registered
    address, including any plot/unit detail beyond "Plot 59065".

Legal review should also confirm this reads correctly against Botswana consumer
protection law and against the card scheme rules DPO operates under."""


def add_refund_policy(apps, schema_editor):
    LegalDocument = apps.get_model('monitor', 'LegalDocument')
    LegalDocument.objects.update_or_create(
        doc_type='refund',
        version='1.0',
        defaults={
            'title': 'Refund & Cancellation Policy',
            'summary': REFUND_SUMMARY,
            'body': REFUND_BODY,
            # Published for reference, never put in front of a user to tick —
            # this states what we will do, it is not a permission they grant.
            'consent_label': '',
            'requires_acceptance': False,
            'jurisdiction': 'Botswana',
            'review_status': 'draft',
            'review_note': REVIEW_NOTE,
            'is_published': True,
            'effective_from': timezone.now(),
        },
    )


def remove_refund_policy(apps, schema_editor):
    apps.get_model('monitor', 'LegalDocument').objects.filter(doc_type='refund').delete()


class Migration(migrations.Migration):

    dependencies = [('monitor', '0020_assessmentsubmission')]

    operations = [
        migrations.AlterField(
            model_name='legaldocument',
            name='doc_type',
            field=models.CharField(
                choices=[('terms', 'Terms & Conditions'),
                         ('privacy', 'Privacy & Personal Data Consent'),
                         ('disclaimer', 'Disclaimer'),
                         ('refund', 'Refund & Cancellation Policy')],
                max_length=30),
        ),
        migrations.RunPython(add_refund_policy, remove_refund_policy),
    ]
