"""Seed version 1.0 of the three legal documents onboarding asks users to accept.

⚠️  THE WORDING BELOW IS A PRODUCT DRAFT, NOT LEGAL ADVICE.

It was written to give the consent flow real, coherent content to work with and
to show what each document needs to cover. It has **not** been settled, reviewed
or approved by a legal practitioner, and every row it creates is stored with
``review_status='draft'`` so that fact travels with the document and is visible
in the admin. The statutes named in the text are named as the frameworks the
final wording should be reviewed against — the citations themselves need
verifying by counsel admitted in Botswana.

Replacing this text does **not** require a code change. Either edit the version
in the Django admin while it is still a draft, or — once anyone has accepted it —
publish a new ``LegalDocument`` row with the approved wording and a new version
number. Existing ConsentRecords keep pointing at the version they were given, and
users are asked to accept the new one.
"""
from django.db import migrations
from django.utils import timezone


TERMS_BODY = """1. About these terms

These Terms and Conditions govern access to and use of the Social Light media
monitoring and market intelligence platform ("the Platform"), operated by Social
Light ("we", "us"). By creating an account, accepting these terms during
onboarding, or using the Platform, you agree to be bound by them. If you do not
agree, you must not use the Platform.

2. Who may use the Platform

You must be at least 18 years of age and legally capable of entering into a
binding agreement. You may register either as an individual or on behalf of an
organisation or agency.

3. Accounts and account security

You are responsible for the accuracy of the information you give us, for keeping
your access credentials confidential, and for all activity carried out under your
account. You must notify us without undue delay if you become aware of any
unauthorised use of your account. We may require you to verify control of the
email address associated with your account before granting full access.

4. Organisations, agencies and authority to bind

Where you register on behalf of an organisation or agency, you confirm that you
are duly authorised to act for that organisation and to accept these terms on its
behalf, and that the organisation is bound by them. You accept responsibility for
the accuracy of that confirmation. We may suspend an account where we have
reasonable grounds to believe the confirmation was inaccurate, and may in future
require documentary evidence of authority.

5. Subscriptions, plans and entitlements

Access to particular features depends on the subscription plan applying to your
organisation. Plans, their contents and the published price list are set out on
the Platform and may be varied on reasonable notice. Features not included in
your plan are unavailable to you, whether or not they are visible in the
interface, and attempting to obtain them by other means is a breach of these
terms.

6. Fees, payment and settlement

Fees are payable in the currency and on the billing cycle stated for the plan you
select. Where a payment gateway is in operation, card details are captured and
held by our payment provider, and we retain only the provider's tokenised
reference. Where a payment gateway is not in operation, fees are settled by
invoice or electronic funds transfer and access is enabled once payment is
received. Funds arising from a payment become eligible for extraction only after
the holding period stated on the Platform has elapsed, and in any event subject
to the settlement timetable of the relevant payment provider, acquirer and card
scheme, which we do not control.

7. Acceptable use

You must not use the Platform to break any law; to infringe the rights of any
person; to harass, defame, intimidate or discriminate against any person; to
circumvent or attempt to circumvent access controls, plan restrictions, rate
limits or authentication; to access data belonging to another organisation; to
scrape, resell or systematically extract Platform content other than as your plan
expressly permits; or to introduce malicious code.

8. Media content and third-party material

The Platform surfaces material published by third parties, including news
publishers, broadcasters and social media platforms. That material remains the
property of its owners and is provided to you for monitoring and analysis
purposes. Its inclusion is not an endorsement of its accuracy or of any view it
expresses. You are responsible for obtaining any further licence you may need
before republishing, distributing or commercially exploiting third-party
material, and for complying with the Copyright and Neighbouring Rights Act and
any other applicable law.

9. Our intellectual property

The Platform, its software, design, reports templates and analytical outputs
(other than the underlying third-party material) remain our property or that of
our licensors. You are granted a non-exclusive, non-transferable right to use
them for your own internal business purposes for the duration of your
subscription.

10. Availability and changes to the service

We aim to keep the Platform available but do not warrant uninterrupted or
error-free operation. We may modify, suspend or withdraw features, and will give
reasonable notice of material changes that adversely affect a paid plan.

11. Confidentiality and your data

Each party will keep the other's confidential information confidential. Our
handling of personal data is described in the Privacy and Personal Data Consent
document, which forms part of your agreement with us.

12. Suspension and termination

We may suspend or terminate access where these terms are breached, where fees
remain unpaid, or where required by law. You may terminate your subscription in
accordance with the plan terms. Termination does not affect rights accrued before
it takes effect.

13. Limitation of liability

To the fullest extent permitted by law, and subject always to rights that cannot
be excluded under the Consumer Protection Act and other applicable Botswana
legislation, we are not liable for indirect or consequential loss, loss of
profit, loss of business or reputational harm arising from use of the Platform,
and our total liability in any twelve-month period is limited to the fees paid by
you in that period. Nothing in these terms excludes liability for fraud or for
any other liability that cannot lawfully be excluded.

14. Changes to these terms

We may publish a new version of these terms. Where a new version materially
affects your rights, we will ask you to accept it before you continue to use the
Platform. Your acceptance of each version is recorded separately, and earlier
acceptances remain on record.

15. Governing law and disputes

These terms are governed by the laws of the Republic of Botswana. The parties
submit to the jurisdiction of the courts of Botswana. The parties will attempt in
good faith to resolve any dispute by negotiation before commencing proceedings.

16. Contact

Questions about these terms may be sent to legal@sociallightbw.com."""


PRIVACY_BODY = """1. Purpose of this document

This document explains what personal data Social Light collects about you, why we
collect it, how we use it, who we share it with and what rights you have. It is
intended to be read alongside our Terms and Conditions and is drafted for review
against the Data Protection Act of Botswana and the guidance of the Information
and Data Protection Commission.

2. Who is responsible for your data

Social Light is the data controller for the personal data described here. Where
we process coverage data on behalf of a client organisation, that organisation is
the controller and we act as its processor.

3. What we collect

Account and identity data: your first and last name, email address, telephone
number, job title or position, and country.

Organisation data: the name of the organisation or agency you represent, your
position within it, its contact details, and your confirmation that you are
authorised to represent it.

Consent and compliance data: which version of each legal document you accepted,
when you accepted it, and the IP address and browser user-agent recorded at the
time. This is collected so that we can demonstrate that consent was properly
obtained.

Billing data: the subscription plan applying to your organisation, payment
records, and a tokenised reference to any saved payment card. We do not receive
or store card numbers, card verification values or other sensitive card
authentication data — those are entered directly with our payment provider.

Usage data: sign-in times, pages accessed, reports generated and exports taken,
recorded for security, support and service-improvement purposes.

4. Why we collect it, and on what basis

We process account, organisation and billing data in order to perform our
contract with you or your organisation, and to comply with legal and accounting
obligations. We process consent records to comply with data protection law and to
evidence that consent was given. We process usage data in our legitimate
interests in keeping the Platform secure and working properly. Where we rely on
your consent, you may withdraw it at any time as described below.

5. Who we share it with

We share personal data with service providers who help us run the Platform —
including email delivery, cloud hosting and payment processing providers — under
contracts that restrict their use of it. We share data with public authorities
where we are legally required to. We do not sell personal data.

6. Transfers outside Botswana

Some of our service providers operate outside Botswana. Where personal data is
transferred outside the country, we will take the steps required by the Data
Protection Act to ensure it continues to be protected to an equivalent standard.

7. How long we keep it

We keep account data for as long as your account is active and for a reasonable
period afterwards. We keep consent records for as long as needed to demonstrate
compliance. We keep billing and payment records for the period required by tax
and company law. We keep usage logs for a limited period appropriate to their
security purpose.

8. Security

We apply technical and organisational measures appropriate to the risk, including
access control, encryption of data in transit, organisation-level isolation of
client data, and restriction of administrative access.

9. Your rights

Subject to the conditions and exceptions in the Data Protection Act, you have the
right to be informed about how your data is used; to access the personal data we
hold about you; to have inaccurate data corrected; to have data erased in defined
circumstances; to object to or restrict certain processing; and to lodge a
complaint with the Information and Data Protection Commission.

10. Withdrawing consent

Where we rely on your consent, you may withdraw it by contacting us. Withdrawal
does not affect the lawfulness of processing carried out before it, and may mean
we can no longer provide some or all of the service. Your withdrawal is recorded
as a new consent record; earlier records are not deleted, because they are the
evidence of what was agreed at the time.

11. Contact

To exercise a right, or to ask a question about this document, contact
privacy@sociallightbw.com."""


DISCLAIMER_BODY = """1. Nature of the information provided

The Social Light Platform collects, organises and analyses material published by
third parties — news publishers, broadcasters, online publications and social
media platforms — together with analysis generated from that material. It is
provided for monitoring, awareness and internal decision-support purposes.

2. No warranty as to accuracy or completeness

Source material is reproduced as published. We do not verify the accuracy of what
third parties publish, and inclusion of an item on the Platform is not an
endorsement of it or of any view it expresses. Media monitoring is not
exhaustive: coverage depends on what sources publish, what they make accessible,
and how search terms are configured, and items may be missed, duplicated or
mis-classified.

3. Automated and AI-assisted analysis

Sentiment scores, summaries, issue narratives, recommendations and similar
outputs are produced with automated and AI-assisted methods. They are estimates
and may contain errors or omissions. They must be reviewed by a competent person
before being relied upon, and must not be presented as verified fact.

4. Not professional advice

Nothing on the Platform constitutes legal, financial, investment, public
relations or other professional advice. You should obtain your own professional
advice before acting on anything you find here.

5. Your responsibility for how you use the information

You are responsible for how you use information and content obtained through the
Platform, including any decision you take on the basis of it, any republication,
distribution or onward sharing of it, and any statement you make relying on it.
You must ensure your use complies with applicable law, including the law of
defamation, privacy and data protection, and copyright, and with any licence
terms attaching to the source material.

6. No liability for inappropriate use

Subject to rights that cannot be excluded by law, Social Light does not accept
liability for loss or damage arising from inappropriate, unlawful or negligent
use of information or content obtained through the Platform, including its use to
harass, defame or discriminate against any person, its use in breach of a third
party's rights, or its use without the verification a reasonable person would
carry out. Responsibility for such use rests with the person who made it.

7. Limits of this disclaimer

Nothing in this disclaimer excludes or limits liability for fraud, or any other
liability that cannot lawfully be excluded, including rights conferred by the
Consumer Protection Act and other applicable Botswana legislation.

8. Questions

Questions about this disclaimer may be sent to legal@sociallightbw.com."""


REVIEW_NOTE = (
    'PRODUCT DRAFT — NOT LEGALLY APPROVED. Written to give the consent flow real content and '
    'to show the required coverage. Must be reviewed and settled by a legal practitioner '
    'admitted in Botswana before this platform is offered commercially. Verify in particular: '
    'the correct short titles and citations of the Data Protection Act, the Consumer Protection '
    'Act, the Copyright and Neighbouring Rights Act and the Electronic Communications and '
    'Transactions Act; the registration and notification obligations owed to the Information and '
    'Data Protection Commission; the enforceability of the liability limits in clause 13 of the '
    'Terms and clause 6 of the Disclaimer; the lawful basis and cross-border transfer wording; '
    'and the retention periods, which are stated in general terms and need concrete figures.'
)


DOCUMENTS = [
    {
        'doc_type': 'terms',
        'version': '1.0',
        'title': 'Social Light Terms & Conditions',
        'summary': ('These are the terms on which you may use Social Light. They cover your '
                    'account, your subscription and what you may and may not do with the '
                    'platform. You need to accept them to continue.'),
        'consent_label': 'I have read and accept the Social Light Terms & Conditions.',
        'body': TERMS_BODY,
    },
    {
        'doc_type': 'privacy',
        'version': '1.0',
        'title': 'Privacy & Personal Data Consent',
        'summary': ('This is a separate consent, because it is about your personal data rather '
                    'than the service. It explains exactly what we collect about you, why we '
                    'need each piece of it, who we share it with and what rights you have.'),
        'consent_label': ('I consent to Social Light processing my personal data as described '
                          'in this notice.'),
        'body': PRIVACY_BODY,
    },
    {
        'doc_type': 'disclaimer',
        'version': '1.0',
        'title': 'Disclaimer',
        'summary': ('Media coverage and AI-assisted analysis are provided as information, not as '
                    'verified fact or professional advice. This sets out the limits of what you '
                    'can rely on, and confirms that you are responsible for how you use what you '
                    'find here.'),
        'consent_label': ('I understand and accept the disclaimer, and that I am responsible for '
                          'how I use information obtained through the platform.'),
        'body': DISCLAIMER_BODY,
    },
]


def seed(apps, schema_editor):
    LegalDocument = apps.get_model('monitor', 'LegalDocument')
    now = timezone.now()
    for row in DOCUMENTS:
        LegalDocument.objects.get_or_create(
            doc_type=row['doc_type'],
            version=row['version'],
            defaults={
                **row,
                'jurisdiction': 'Botswana',
                'review_status': 'draft',
                'review_note': REVIEW_NOTE,
                'requires_acceptance': True,
                'is_published': True,
                'effective_from': now,
            },
        )


def unseed(apps, schema_editor):
    LegalDocument = apps.get_model('monitor', 'LegalDocument')
    for row in DOCUMENTS:
        LegalDocument.objects.filter(
            doc_type=row['doc_type'], version=row['version'], consents__isnull=True
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0018_backfill_existing_accounts'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
