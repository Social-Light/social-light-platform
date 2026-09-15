"""Grandfather the new `broadcast_print_monitoring` entitlement onto every
package that already has `basic_monitoring`.

Broadcast and print monitoring have never been gated in code — any account with
`basic_monitoring` (which is every paid package; see migration
0024_package_entitlement_map_and_free_tier) could reach ``media_print`` and
``media_broadcast`` regardless of what its price-list card actually promised.
Introducing real enforcement (monitor/entitlements.py, monitor/views.py) means a
package now needs the new code explicitly, so this migration adds it to every
paid package's existing entitlement list before enforcement goes live — nobody
who already had de facto access loses it.

`slug='free'` is deliberately excluded: the Free tier never had broadcast/print
in any real sense (nobody manually uploads print clippings for a free account),
and the whole point of the new trial/free defaults
(monitor/entitlements.py:DEFAULT_TRIAL_ENTITLEMENTS,
DEFAULT_FREE_ENTITLEMENTS) is that neither gets it going forward.

Not limited to the four originally-seeded slugs — any package an admin created
or cloned since, with `basic_monitoring` ticked, is covered the same way.
"""
from django.db import migrations

NEW_CODE = 'broadcast_print_monitoring'
GATE_CODE = 'basic_monitoring'
EXCLUDED_SLUGS = {'free'}


def add_entitlement(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')
    for package in Package.objects.all():
        if package.slug in EXCLUDED_SLUGS:
            continue
        codes = set(package.entitlements or [])
        if GATE_CODE in codes and NEW_CODE not in codes:
            codes.add(NEW_CODE)
            package.entitlements = sorted(codes)
            package.save(update_fields=['entitlements'])


def remove_entitlement(apps, schema_editor):
    Package = apps.get_model('monitor', 'Package')
    for package in Package.objects.all():
        codes = package.entitlements or []
        if NEW_CODE in codes:
            package.entitlements = [c for c in codes if c != NEW_CODE]
            package.save(update_fields=['entitlements'])


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0031_organization_current_period_end_and_more'),
    ]

    operations = [
        migrations.RunPython(add_entitlement, remove_entitlement),
    ]
