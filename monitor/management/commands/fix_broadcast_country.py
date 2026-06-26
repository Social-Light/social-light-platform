"""
monitor/management/commands/fix_broadcast_country.py

Set country = "Botswana" for existing broadcast rows that come from a confirmed
Botswana station (Gabz FM, Duma FM) but were imported without a country. New
broadcast rows infer this at import time; this backfills older rows.

Only rows with a blank or "Unknown" country are touched, and only stations we've
confirmed are Botswana — everything else is left as-is.

Usage:
    python manage.py fix_broadcast_country              # update Gabz FM / Duma FM rows
    python manage.py fix_broadcast_country --dry-run     # report only, write nothing
    python manage.py fix_broadcast_country --org <uuid>  # restrict to one organisation
"""
import re

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from monitor.models import Organization, BroadcastMention

# Normalised station-name prefixes confirmed to be Botswana stations.
BW_STATIONS = ('gabzfm', 'gabz', 'dumafm', 'duma')


def _is_bw_station(name):
    key = re.sub(r'[^a-z0-9]', '', (name or '').lower())
    return bool(key) and any(key.startswith(s) for s in BW_STATIONS)


class Command(BaseCommand):
    help = 'Set country=Botswana for existing broadcast rows from confirmed Botswana stations (Gabz FM, Duma FM).'

    def add_arguments(self, parser):
        parser.add_argument('--org', dest='org_id', type=str,
                            help='Restrict to a single organisation id.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        qs = BroadcastMention.objects.all()
        if opts['org_id']:
            if not Organization.objects.filter(id=opts['org_id']).exists():
                raise CommandError(f"No organisation with id {opts['org_id']!r}.")
            qs = qs.filter(organization_id=opts['org_id'])

        # Only rows that don't already have a real country.
        qs = qs.filter(Q(country='') | Q(country__iexact='unknown'))

        changed = []
        for row in qs.only('id', 'source', 'country').iterator():
            if _is_bw_station(row.source):
                row.country = 'Botswana'
                changed.append(row)

        if changed and not opts['dry_run']:
            BroadcastMention.objects.bulk_update(changed, ['country'], batch_size=1000)

        verb = 'would update' if opts['dry_run'] else 'updated'
        self.stdout.write(self.style.SUCCESS(
            f'Done: {verb} {len(changed)} broadcast row(s) to Botswana.'))
