"""Give every package a machine-readable entitlement list, and add the Free tier.

Until now a package's ``features`` list was marketing copy only — nothing in the
application could answer "may this account download a report?". This migration
fills in ``Package.entitlements`` for the four published tiers, derived from what
each tier's own price-list bullets already promise (migration 0012), and creates
the **Free** tier that the trial falls back to.

Entitlements are written by slug with ``update_or_create``-style assignment, but
**only where the field is still empty**: once someone has adjusted a tier's
entitlements in the admin, the admin is the source of truth and re-running this
migration must not overwrite that. Free is created if absent and left alone if it
already exists.

The Free tier is deliberately ``is_public=False``: it is assignable (onboarding,
manual admin assignment when the payment gateway is off) but is not advertised on
the published price list, which is a marketing decision and not this migration's
to make.
"""
from django.db import migrations


# Derived from the price-list bullets in migration 0012:
#   Spark      — reports and AI analysis, no campaigns, no export
#   Momentum   — "All 5 report types", "Up to 5 campaigns", "Read-only API"
#   Scale      — "Full media database with export", "Full API", unlimited
#   Enterprise — unlimited scope
ENTITLEMENTS_BY_SLUG = {
    'spark': [
        'basic_monitoring', 'dashboard', 'media_sources', 'alerts',
        'advanced_analytics', 'competitor_analysis', 'ai_analysis',
        'report_download',
    ],
    'momentum': [
        'basic_monitoring', 'dashboard', 'media_sources', 'alerts',
        'advanced_analytics', 'competitor_analysis', 'ai_analysis',
        'report_download', 'premium_reports', 'campaigns', 'api_access',
    ],
    'scale': [
        'basic_monitoring', 'dashboard', 'media_sources', 'alerts',
        'advanced_analytics', 'competitor_analysis', 'ai_analysis',
        'report_download', 'premium_reports', 'campaigns', 'api_access',
        'crawl_result_download',
    ],
    'enterprise-full-scope': [
        'basic_monitoring', 'dashboard', 'media_sources', 'alerts',
        'advanced_analytics', 'competitor_analysis', 'ai_analysis',
        'report_download', 'premium_reports', 'campaigns', 'api_access',
        'crawl_result_download',
    ],
}

FREE_TIER = {
    'slug': 'free',
    'name': 'Free',
    'tagline': 'Basic monitoring for a single brand.',
    'price': 0,
    'price_override': 'Free',
    'price_note': 'No card required.',
    'accent_color': '#64748b',
    'sort_order': 0,
    'is_public': False,
    'cta_label': 'Stay on Free',
    'features': [
        'Basic media monitoring',
        'Coverage dashboard',
        'Keyword alert digests',
    ],
    'excluded_features': [
        'Report and coverage downloads',
        'Premium and issue-focused reports',
        'Competitor and campaign analysis',
        'AI-assisted analysis',
    ],
    'exclusion_note': 'Paid plans',
    'entitlements': ['basic_monitoring', 'dashboard', 'alerts'],
}


def apply_entitlements(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')

    for slug, codes in ENTITLEMENTS_BY_SLUG.items():
        for package in Package.objects.filter(slug=slug):
            if package.entitlements:
                continue          # already configured in the admin — leave it
            package.entitlements = codes
            package.save(update_fields=['entitlements'])

    if not Package.objects.filter(slug=FREE_TIER['slug']).exists():
        Package.objects.create(**FREE_TIER)


def remove_entitlements(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')
    Package.objects.filter(slug=FREE_TIER['slug'], organizations__isnull=True).delete()
    Package.objects.filter(slug__in=ENTITLEMENTS_BY_SLUG).update(entitlements=[])


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0015_package_entitlements_package_is_public_and_more'),
    ]

    operations = [
        migrations.RunPython(apply_entitlements, remove_entitlements),
    ]
