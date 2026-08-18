"""
monitor/management/commands/boost_relevancy.py

Force the relevancy score of media-coverage rows whose text (headline/message,
source or page name) contains a given term to a fixed value, so they clear the
display-time relevancy threshold (settings.MENTION_RELEVANCY_THRESHOLD)
regardless of keyword scoring.

Useful for manually-added rows that scored 0 and got hidden by the filter.

Usage:
    python manage.py boost_relevancy                     # term "fnb", score 100, all types
    python manage.py boost_relevancy --term fnb          # match text containing "fnb"
    python manage.py boost_relevancy --type print        # one media type (online/print/social/broadcast)
    python manage.py boost_relevancy --score 100         # relevancy to assign (default 100)
    python manage.py boost_relevancy --org <uuid>        # restrict to one organisation
    python manage.py boost_relevancy --dry-run           # report only, write nothing
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from monitor.models import (
    Organization, OnlineArticle, PrintArticle, SocialMediaPost, BroadcastMention,
)

# Per-type: model and the text fields to match the term against. All four models
# share headline/summary; the "name" field differs (page_name vs source).
MEDIA = {
    'online': (OnlineArticle, ['headline', 'source']),
    'print': (PrintArticle, ['headline', 'source']),
    'social': (SocialMediaPost, ['headline', 'page_name']),
    'broadcast': (BroadcastMention, ['headline', 'source']),
}


class Command(BaseCommand):
    help = 'Set a fixed relevancy on media rows matching a term in their text.'

    def add_arguments(self, parser):
        parser.add_argument('--term', default='fnb',
                            help='Case-insensitive substring to match (default "fnb").')
        parser.add_argument('--type', dest='media_type', choices=sorted(MEDIA),
                            help='Restrict to a single media type (default: all).')
        parser.add_argument('--score', type=float, default=100.0,
                            help='Relevancy score to assign to matches (default 100).')
        parser.add_argument('--org', dest='org_id', type=str,
                            help='Restrict to a single organisation id.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        term = opts['term'].strip()
        if not term:
            raise CommandError('--term must not be empty.')
        score = opts['score']
        dry_run = opts['dry_run']

        if opts['org_id'] and not Organization.objects.filter(id=opts['org_id']).exists():
            raise CommandError(f"No organisation with id {opts['org_id']!r}.")

        types = [opts['media_type']] if opts['media_type'] else list(MEDIA)
        grand = 0
        for mt in types:
            model, fields = MEDIA[mt]
            match = Q()
            for f in fields:
                match |= Q(**{f'{f}__icontains': term})
            rows = model.objects.filter(match)
            if opts['org_id']:
                rows = rows.filter(organization_id=opts['org_id'])

            count = rows.count()
            if not count:
                continue
            grand += count
            if dry_run:
                for r in rows.only('id', 'headline', 'relevancy'):
                    self.stdout.write(f'  {mt} [{r.relevancy} -> {score}] {r.headline[:60]}')
            else:
                rows.update(relevancy=score)
            self.stdout.write(f'{mt}: {count} row(s)' + (' (dry-run)' if dry_run else ' updated'))

        verb = 'would set' if dry_run else 'set'
        self.stdout.write(self.style.SUCCESS(
            f'Done: {verb} relevancy={score} on {grand} row(s) matching {term!r}.'))
