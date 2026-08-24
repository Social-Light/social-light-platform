"""
monitor/management/commands/update_sector_intelligence.py

Daily refresh of the landing page's public "Sector Intelligence" content —
SectorStory (per sector) and CommodityQuote (the shared ticker) — via a live
web search (see monitor/sector_ai.py for how and why).

Only ever touches is_ai_generated=True rows: for each published Sector, the
previous AI-generated batch is unpublished (not deleted — kept for history)
and a fresh batch created; the same for commodity quotes. An editor's own
hand-written stories/quotes are never touched by this command.

One sector or commodity search failing does not abort the run — it's logged
and the command moves on, so one bad search doesn't cost every other sector
its daily refresh.

Usage:
    python manage.py update_sector_intelligence                # all published sectors + quotes
    python manage.py update_sector_intelligence --sectors-only  # skip commodity quotes
    python manage.py update_sector_intelligence --quotes-only   # skip sector stories
    python manage.py update_sector_intelligence --dry-run       # print what would change, no writes

Scheduled via Celery Beat (see CELERY_BEAT_SCHEDULE) — monitor.update_sector_intelligence.
"""
import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from monitor.models import CommodityQuote, Sector, SectorStory
from monitor.sector_ai import SectorAIError, generate_commodity_quotes, generate_sector_stories

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Refresh AI-generated sector stories and commodity quotes from a live web search"

    def add_arguments(self, parser):
        parser.add_argument('--sectors-only', action='store_true', help='Skip commodity quotes')
        parser.add_argument('--quotes-only', action='store_true', help='Skip sector stories')
        parser.add_argument('--dry-run', action='store_true',
                            help='Search and print what would change; make no database writes')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        do_quotes = not options['sectors_only']
        do_sectors = not options['quotes_only']

        if do_sectors:
            self._update_sectors(dry_run)
        if do_quotes:
            self._update_quotes(dry_run)

    def _update_sectors(self, dry_run):
        sectors = Sector.objects.filter(is_published=True)
        if not sectors:
            self.stdout.write('No published sectors — nothing to do.')
            return

        for sector in sectors:
            self.stdout.write(f'Searching: {sector.name}...')
            try:
                stories = generate_sector_stories(sector)
            except SectorAIError as exc:
                self.stderr.write(self.style.ERROR(f'  {sector.name}: {exc}'))
                logger.error('update_sector_intelligence: %s failed: %s', sector.name, exc)
                continue

            if not stories:
                self.stdout.write(f'  {sector.name}: search returned nothing usable — left as-is.')
                continue

            if dry_run:
                for s in stories:
                    self.stdout.write(f"  [{s['published_on']}] {s['title']} ({s['source_label']})")
                continue

            with transaction.atomic():
                SectorStory.objects.filter(
                    sector=sector, is_ai_generated=True,
                ).update(is_published=False)
                SectorStory.objects.bulk_create([
                    SectorStory(
                        sector=sector, title=s['title'], summary=s['summary'],
                        source_label=s['source_label'], url=s['url'],
                        published_on=s['published_on'], display_order=i,
                        is_published=True, is_ai_generated=True,
                    )
                    for i, s in enumerate(stories)
                ])
            self.stdout.write(self.style.SUCCESS(f'  {sector.name}: {len(stories)} stories published'))

    def _update_quotes(self, dry_run):
        self.stdout.write('Searching: commodity quotes...')
        try:
            quotes = generate_commodity_quotes()
        except SectorAIError as exc:
            self.stderr.write(self.style.ERROR(f'  commodity quotes: {exc}'))
            logger.error('update_sector_intelligence: commodity quotes failed: %s', exc)
            return

        if not quotes:
            self.stdout.write('  commodity quotes: search returned nothing usable — left as-is.')
            return

        if dry_run:
            for q in quotes:
                self.stdout.write(f"  {q['name']}: {q['price_display']} ({q['change_percent']:+.1f}%)")
            return

        with transaction.atomic():
            CommodityQuote.objects.filter(is_ai_generated=True).update(is_published=False)
            CommodityQuote.objects.bulk_create([
                CommodityQuote(
                    name=q['name'], price_display=q['price_display'],
                    change_percent=q['change_percent'], display_order=i,
                    is_published=True, is_ai_generated=True,
                )
                for i, q in enumerate(quotes)
            ])
        self.stdout.write(self.style.SUCCESS(f'  commodity quotes: {len(quotes)} published'))
