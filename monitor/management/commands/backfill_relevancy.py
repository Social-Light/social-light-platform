"""
monitor/management/commands/backfill_relevancy.py

Recompute the relevancy score (monitor.relevancy.compute_relevancy) for existing
media-coverage rows. New rows get scored at ingest; this command backfills rows
created before scoring was added to all media types, so the relevancy threshold
filter has real scores to work with.

Usage:
    python manage.py backfill_relevancy                  # all orgs, all media types
    python manage.py backfill_relevancy --org <uuid>     # one organisation
    python manage.py backfill_relevancy --type print     # one media type
    python manage.py backfill_relevancy --dry-run        # report only, write nothing
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from monitor.models import (
    Organization, OnlineArticle, PrintArticle, SocialMediaPost, BroadcastMention,
)
from monitor.relevancy import compute_relevancy

MODELS = {
    'online': OnlineArticle,
    'print': PrintArticle,
    'social': SocialMediaPost,
    'broadcast': BroadcastMention,
}


class Command(BaseCommand):
    help = 'Recompute relevancy scores for existing media-coverage rows.'

    def add_arguments(self, parser):
        parser.add_argument('--org', dest='org_id', type=str,
                            help='Restrict to a single organisation id.')
        parser.add_argument('--type', dest='media_type', choices=sorted(MODELS),
                            help='Restrict to a single media type.')
        parser.add_argument('--batch-size', type=int, default=1000,
                            help='Rows per bulk_update batch (default 1000).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        orgs = Organization.objects.all()
        if opts['org_id']:
            orgs = orgs.filter(id=opts['org_id'])
            if not orgs.exists():
                raise CommandError(f"No organisation with id {opts['org_id']!r}.")

        types = [opts['media_type']] if opts['media_type'] else list(MODELS)
        batch_size = opts['batch_size']
        dry_run = opts['dry_run']

        grand_total = grand_changed = 0
        for org in orgs:
            keywords = list(org.keywords.all())
            competitors = list(org.competitors.all())
            for mt in types:
                model = MODELS[mt]
                rows = model.objects.filter(organization=org).only(
                    'id', 'headline', 'summary', 'relevancy')
                changed, total = [], 0
                for row in rows.iterator(chunk_size=batch_size):
                    total += 1
                    score = compute_relevancy(row.headline, row.summary,
                                              keywords=keywords, competitors=competitors)
                    if score != row.relevancy:
                        row.relevancy = score
                        changed.append(row)
                if changed and not dry_run:
                    with transaction.atomic():
                        model.objects.bulk_update(changed, ['relevancy'], batch_size=batch_size)
                grand_total += total
                grand_changed += len(changed)
                if total:
                    self.stdout.write(
                        f'{org.name} / {mt}: {len(changed)}/{total} updated'
                        + (' (dry-run)' if dry_run else ''))

        verb = 'would update' if dry_run else 'updated'
        self.stdout.write(self.style.SUCCESS(
            f'Done: {verb} {grand_changed} of {grand_total} rows.'))
