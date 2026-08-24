"""Replace the placeholder tiers seeded by migration 0010 with the real
published price list: Spark, Momentum, Scale and Enterprise.

The placeholders were invented figures standing in until the real list was
agreed. They are removed here, but only when nothing references them — if an
organisation was ever put on a placeholder, or a package request named one, it
is deactivated instead of deleted so that history stays intact.

Prices are USD and are written by slug, so re-running this migration on a
database where someone has already edited a package in the admin will overwrite
those edits. After this point the admin is the source of truth: change prices
there, not by editing this file.
"""
from django.db import migrations


PLACEHOLDER_SLUGS = ['essential', 'professional', 'enterprise']

BROADCAST_EXCLUSION = ['Broadcast and print monitoring']

PACKAGES = [
    {
        'slug': 'spark',
        'name': 'Spark',
        'tagline': 'Single brands, startups and small public relations teams.',
        'price': 49,
        'price_note': 'Billed annually · $588 per year',
        'accent_color': '#f59e0b',
        'sort_order': 1,
        'features': [
            '1 organisation, 3 users',
            '10 tracked keywords',
            'Up to 3,000 mentions per month (soft cap)',
            '2 competitors tracked',
            '1 Monthly Summary report per month (SocialLight branding)',
            '2 AI-assisted analyses per month',
            '3 keyword alerts',
            '6-month data history',
            'Email support',
        ],
        'excluded_features': BROADCAST_EXCLUSION,
        'exclusion_note': 'Enterprise only',
    },
    {
        'slug': 'momentum',
        'name': 'Momentum',
        'tagline': 'Growing organisations and agencies.',
        'price': 299,
        'price_note': 'Billed annually · $3,588 per year',
        'accent_color': '#1d4878',
        'is_featured': True,
        'sort_order': 2,
        'features': [
            'Up to 5 organisations, 15 users',
            '50 keywords',
            '30,000 mentions per month',
            '5 competitors',
            'All 5 report types, white-label branding',
            '20 AI credits per month',
            'Up to 5 campaigns',
            'Regional media database',
            'Unlimited alerts, 12-month history',
            'Read-only API',
            'Priority support and live chat',
        ],
        'excluded_features': BROADCAST_EXCLUSION,
        'exclusion_note': 'Enterprise only',
    },
    {
        'slug': 'scale',
        'name': 'Scale',
        'tagline': 'Large corporates, government and multi-country agencies.',
        'price': 1299,
        'price_note': 'Billed annually · approx. $15,600 per year',
        'accent_color': '#0d9488',
        'sort_order': 3,
        'features': [
            'Up to 20 organisations, unlimited users',
            'Unlimited keywords',
            '150,000 mentions per month',
            '15 competitors',
            'Unlimited reports, per-sub-organisation white-label',
            'Unlimited AI credits',
            'Unlimited campaigns',
            'Full media database with export',
            '36-month history',
            'Full API',
            'Dedicated account manager and service level agreement',
        ],
        'excluded_features': BROADCAST_EXCLUSION,
        'exclusion_note': 'Enterprise only',
    },
    {
        'slug': 'enterprise-full-scope',
        'name': 'Enterprise',
        'eyebrow': 'Full scope',
        'tagline': 'Unlimited scope, custom organisation hierarchy.',
        'price': 0,
        'price_override': 'Custom',
        'price_note': 'From approximately $30,000 per year',
        'accent_color': '#f59e0b',
        'is_dark': True,
        'contact_only': True,
        'cta_label': 'Contact sales',
        'sort_order': 4,
        'highlight_title': 'Only on Enterprise',
        'highlight_body': ('Broadcast monitoring (television and radio) and print or '
                           'newspaper monitoring.'),
        'features': [
            'Unlimited organisations, users and volume',
            'Full reseller and white-label licence',
            'Custom integrations — Slack, Teams, Power BI, CRM, SSO',
            'Compliance packs — POPIA, GDPR, CCPA',
            'Round-the-clock support with a dedicated success team',
        ],
    },
]


def retire_placeholders(Package):
    """Delete the invented placeholder tiers, keeping any that were actually
    used — a referenced package is deactivated so it drops off the price list
    without breaking the organisation or request pointing at it."""
    for package in Package.objects.filter(slug__in=PLACEHOLDER_SLUGS):
        in_use = package.organizations.exists() or package.requests.exists()
        if in_use:
            package.is_active = False
            package.save(update_fields=['is_active'])
        else:
            package.delete()


def apply_price_list(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')
    retire_placeholders(Package)
    for row in PACKAGES:
        Package.objects.update_or_create(
            slug=row['slug'],
            defaults={'currency': 'USD', 'billing_period': 'monthly', 'is_active': True, **row},
        )


def remove_price_list(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')
    Package.objects.filter(slug__in=[p['slug'] for p in PACKAGES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0011_package_accent_color_package_contact_only_and_more'),
    ]

    operations = [
        migrations.RunPython(apply_price_list, remove_price_list),
    ]
