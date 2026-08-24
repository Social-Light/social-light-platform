from django.db import migrations


# Only the sector tabs are seeded — they are structure, not content. Stories,
# commodity quotes and publications are left empty deliberately: the placeholder
# copy the page shipped with was mockup text and invented prices, and editors
# should publish real items from the admin rather than inherit fabricated ones.
SECTORS = [
    ('Mining', 'mining', True, 0),
    ('Banking', 'banking', False, 1),
    ('Energy', 'energy', False, 2),
    ('Agriculture', 'agriculture', False, 3),
]


def seed_sectors(apps, schema_editor):
    Sector = apps.get_model('monitor', 'Sector')
    for name, slug, shows_ticker, order in SECTORS:
        Sector.objects.update_or_create(
            slug=slug,
            defaults={'name': name, 'shows_ticker': shows_ticker,
                      'display_order': order, 'is_published': True},
        )


def unseed_sectors(apps, schema_editor):
    Sector = apps.get_model('monitor', 'Sector')
    Sector.objects.filter(slug__in=[s[1] for s in SECTORS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0013_commodityquote_publication_sector_sectorstory'),
    ]

    operations = [
        migrations.RunPython(seed_sectors, unseed_sectors),
    ]
