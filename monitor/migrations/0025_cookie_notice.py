"""Add the Cookie Notice document type and seed version 1.0.

⚠️  THE WORDING BELOW IS A PRODUCT DRAFT, NOT LEGAL ADVICE.

A cookie banner has to link to something that says what the cookies actually
are, otherwise the consent it collects is not informed and is not worth having.
This is that page.

What it describes is what the code does — session and CSRF cookies that the site
cannot work without, the first-party campaign attribution in
monitor/attribution.py, and the Meta Pixel and GA4 in
templates/monitor/partials/tracking.html, which stay dormant until the banner is
accepted. If any of those change, this text has to change with them.

Published as ``review_status='draft'`` like the refund policy, so the page
carries a visible "pending legal review" chip until someone signs it off. Edit
it in the Django admin rather than by writing another migration.
"""
from django.db import migrations, models
from django.utils import timezone


COOKIE_SUMMARY = (
    'What we store on your device and why. The short version: a few cookies are '
    'needed for the site to work at all, and everything used for advertising and '
    'analytics waits until you say yes.'
)

COOKIE_BODY = """1. What a cookie is here

A cookie is a small file this site asks your browser to keep, so that a later
page can recognise the same visit. Some are set by us, some by the companies
whose measurement tools we use. This notice covers both, and covers the
equivalent browser storage we use for the same purposes.

2. Cookies the site cannot work without

These are set whenever you use the site and cannot be switched off, because
without them ordinary things break: staying signed in, submitting a form
without it being rejected, and being returned to the right place after paying.

- A session cookie, which identifies your visit to our server. If you sign in,
  it is what keeps you signed in.
- A security token cookie, which proves a form was submitted from our own page
  and not from somewhere else pretending to be us.
- Your answer to the cookie banner, so that we do not ask you again on every
  page. Declining is itself stored this way.

These are our own cookies. They are not shared with anyone and they are not used
for advertising.

3. Knowing which campaign brought you here

When you arrive from a link we have published — an advert, a newsletter, a post
— that link may carry a short tag naming the campaign. We keep that tag for the
length of your visit, in the session cookie described above, so that if you go
on to complete our free assessment we can see which campaign produced it.

This tells us that a campaign worked. It does not build a profile of you, it is
not shared with the advertising platforms, and it is not used to show you
adverts. It is part of how the site works and it does not wait for the banner.

4. Analytics and advertising cookies

These only ever run if you accept them at the banner, and you can decline
without losing anything on this site.

- **Meta Pixel** (Meta Platforms, Inc.) — records that you visited certain pages
  and whether you completed our assessment. We use it to measure our advertising
  and to show our adverts again to people who have already visited. Meta may
  also use what it collects for its own purposes as a data controller in its own
  right.
- **Google Analytics 4** (Google LLC) — counts visits and shows us which pages
  people read and where they stop. We use it to understand what is working on
  the site.

Both of these place their own cookies and both involve transferring data outside
Botswana, including to the United States.

5. Reporting a conversion from our own systems

When you complete the free assessment we may also tell Meta that a conversion
happened, from our server rather than from your browser, so that our advertising
reporting is accurate. Where we do, your email address is not sent as text: it
is converted into an irreversible code first, which Meta can compare against
codes of its own but cannot read back.

6. Changing your mind

You can clear the cookies for this site in your browser at any time, which
resets your answer and brings the banner back. Most browsers also let you block
third-party cookies altogether; if you do, the tools in section 4 will not run
even if the banner was accepted previously.

7. If you did not accept

Nothing on this site is withheld from you. Every page, the free assessment and
the results you receive work exactly the same. We simply will not know which
campaign to credit for your visit beyond what is described in section 3.

8. Asking us about this

Write to admin@sociallightbw.com and we will answer. This notice sits alongside
our Privacy notice, which covers personal data more broadly, including the
details you give us when you complete the assessment or open an account."""

REVIEW_NOTE = (
    'Product draft, not reviewed. Two things need a decision before this is '
    'approved: whether the Meta Pixel and GA4 are actually being deployed (the '
    'text names both, and any tool named here but not used — or used but not '
    'named — makes the notice wrong), and whether the named contact address is '
    'the right one for data questions. The factual description of the essential '
    'cookies and of campaign attribution matches what the code does today.'
)


def add_cookie_notice(apps, schema_editor):
    apps.get_model('monitor', 'LegalDocument').objects.update_or_create(
        doc_type='cookies',
        version='1.0',
        defaults={
            'title': 'Cookie Notice',
            'summary': COOKIE_SUMMARY,
            'body': COOKIE_BODY,
            # Consent to cookies is given at the banner by a visitor with no
            # account, not by ticking a box in onboarding.
            'consent_label': '',
            'requires_acceptance': False,
            'jurisdiction': 'Botswana',
            'review_status': 'draft',
            'review_note': REVIEW_NOTE,
            'is_published': True,
            'effective_from': timezone.now(),
        },
    )


def remove_cookie_notice(apps, schema_editor):
    apps.get_model('monitor', 'LegalDocument').objects.filter(doc_type='cookies').delete()


class Migration(migrations.Migration):

    dependencies = [('monitor', '0024_assessmentsubmission_channel_and_more')]

    operations = [
        migrations.AlterField(
            model_name='legaldocument',
            name='doc_type',
            field=models.CharField(
                choices=[('terms', 'Terms & Conditions'),
                         ('privacy', 'Privacy & Personal Data Consent'),
                         ('disclaimer', 'Disclaimer'),
                         ('refund', 'Refund & Cancellation Policy'),
                         ('cookies', 'Cookie Notice')],
                max_length=30),
        ),
        # ConsentRecord shares the same choice list. Nothing will ever record a
        # consent decision of this type — cookie consent is a browser cookie, not
        # a row — but the two fields have to stay in step or every subsequent
        # makemigrations run generates a spurious migration.
        migrations.AlterField(
            model_name='consentrecord',
            name='doc_type',
            field=models.CharField(
                choices=[('terms', 'Terms & Conditions'),
                         ('privacy', 'Privacy & Personal Data Consent'),
                         ('disclaimer', 'Disclaimer'),
                         ('refund', 'Refund & Cancellation Policy'),
                         ('cookies', 'Cookie Notice')],
                max_length=30),
        ),
        migrations.RunPython(add_cookie_notice, remove_cookie_notice),
    ]
