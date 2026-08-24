"""Seed the price list with three placeholder tiers.

These exist so the paywall and the public price list have something to show from
the first deploy. The names, prices and bullets are placeholders — edit them in
the Django admin (Monitor → Packages) once the real price list is agreed. The
migration only runs when the table is empty, so it will never overwrite real
packages, and reversing it only removes the placeholders it created.
"""
from django.db import migrations


PLACEHOLDERS = [
    {
        'name': 'Essential',
        'slug': 'essential',
        'tagline': 'For a single brand watching its own coverage.',
        'price': 4500,
        'sort_order': 1,
        'is_featured': False,
        'features': [
            'Online and social media monitoring',
            'Up to 15 tracked keywords',
            'Daily email alerts',
            'Sentiment analysis and AVE',
            '3 platform users',
        ],
    },
    {
        'name': 'Professional',
        'slug': 'professional',
        'tagline': 'Full multimedia scope for an in-house comms team.',
        'price': 9500,
        'sort_order': 2,
        'is_featured': True,
        'features': [
            'Everything in Essential',
            'Print and broadcast monitoring',
            'Up to 50 tracked keywords',
            'Competitor tracking and share of voice',
            'Campaign and issue-focused reports',
            '10 platform users',
        ],
    },
    {
        'name': 'Enterprise',
        'slug': 'enterprise',
        'tagline': 'For agencies and groups managing several brands.',
        'price': 0,
        'sort_order': 3,
        'is_featured': False,
        'features': [
            'Everything in Professional',
            'Unlimited keywords and competitors',
            'Multiple organisations under one account',
            'AI-generated executive analysis',
            'Branded PDF and PowerPoint exports',
            'Unlimited users',
        ],
    },
]


def seed(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')
    if Package.objects.exists():
        return
    for row in PLACEHOLDERS:
        Package.objects.create(currency='BWP', billing_period='monthly', is_active=True, **row)


def unseed(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')
    Package.objects.filter(slug__in=[p['slug'] for p in PLACEHOLDERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0009_package_organization_plan_status_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
