"""
monitor/management/commands/remove_youtube_articles.py

Delete already-imported YouTube coverage from the media models. A clip counts as
"YouTube" when its link host is youtube.com / youtu.be or its source name
contains "youtube" — the same rule mediahost ingest now uses to skip them
(monitor.mediahost.is_youtube_clip).

Usage:
    python manage.py remove_youtube_articles --dry-run     # show what would be deleted
    python manage.py remove_youtube_articles               # delete (asks to confirm)
    python manage.py remove_youtube_articles --yes         # delete without confirming
    python manage.py remove_youtube_articles --org <uuid>  # restrict to one organisation
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from monitor.models import (
    Organization, OnlineArticle, PrintArticle, BroadcastMention,
)

# A row is YouTube when its link or source name points at YouTube. All three
# models expose `url` and `source`.
YOUTUBE_Q = (
    Q(url__icontains='youtube.com')
    | Q(url__icontains='youtu.be')
    | Q(source__icontains='youtube')
)

MODELS = (OnlineArticle, PrintArticle, BroadcastMention)


class Command(BaseCommand):
    help = 'Remove existing YouTube articles/mentions from the media models.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Show how many rows match, delete nothing.')
        parser.add_argument('--yes', action='store_true',
                            help='Delete without the interactive confirmation prompt.')
        parser.add_argument('--org', type=str,
                            help='Restrict deletion to a single Organization UUID.')

    def handle(self, *args, **options):
        org = self._resolve_org(options.get('org'))

        matches = {}
        total = 0
        for model in MODELS:
            qs = model.objects.filter(YOUTUBE_Q)
            if org is not None:
                qs = qs.filter(organization=org)
            count = qs.count()
            matches[model] = (qs, count)
            total += count

        scope = f'"{org.name}"' if org else 'all organisations'
        self.stdout.write(f'YouTube rows in {scope}:')
        for model, (_, count) in matches.items():
            self.stdout.write(f'  {model.__name__}: {count}')
        self.stdout.write(f'  Total: {total}')

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to remove.'))
            return

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — no rows deleted.'))
            return

        if not options['yes']:
            answer = input(f'Delete {total} row(s)? [y/N] ').strip().lower()
            if answer not in ('y', 'yes'):
                self.stdout.write('Aborted.')
                return

        deleted = 0
        for model, (qs, _) in matches.items():
            n, _ = qs.delete()
            deleted += n
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} row(s).'))

    def _resolve_org(self, org_arg):
        if not org_arg:
            return None
        try:
            return Organization.objects.get(id=org_arg)
        except (Organization.DoesNotExist, ValueError):
            raise CommandError(f'Organization {org_arg!r} not found.')
