"""
monitor/management/commands/boost_relevancy.py

Force the relevancy score of social posts whose message (headline) or page name
contains a given term to a fixed value, so they clear the display-time relevancy
threshold (settings.MENTION_RELEVANCY_THRESHOLD) regardless of keyword scoring.

Useful for manually-added posts that scored 0 and got hidden by the filter.

Usage:
    python manage.py boost_relevancy                     # term "fnb", score 100, all orgs
    python manage.py boost_relevancy --term fnb          # match message/page name containing "fnb"
    python manage.py boost_relevancy --score 100         # relevancy to assign (default 100)
    python manage.py boost_relevancy --org <uuid>        # restrict to one organisation
    python manage.py boost_relevancy --dry-run           # report only, write nothing
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from monitor.models import Organization, SocialMediaPost


class Command(BaseCommand):
    help = 'Set a fixed relevancy on social posts matching a term in the message or page name.'

    def add_arguments(self, parser):
        parser.add_argument('--term', default='fnb',
                            help='Case-insensitive substring to match in message/page name (default "fnb").')
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

        rows = SocialMediaPost.objects.filter(
            Q(headline__icontains=term) | Q(page_name__icontains=term)
        )
        if opts['org_id']:
            if not Organization.objects.filter(id=opts['org_id']).exists():
                raise CommandError(f"No organisation with id {opts['org_id']!r}.")
            rows = rows.filter(organization_id=opts['org_id'])

        matched = rows.count()
        if dry_run:
            for p in rows.only('id', 'headline', 'page_name', 'relevancy'):
                self.stdout.write(
                    f'  [{p.relevancy} -> {score}] {p.page_name or "?"}: {p.headline[:60]}')
            self.stdout.write(self.style.SUCCESS(
                f'Done: would set relevancy={score} on {matched} post(s) matching {term!r} (dry-run).'))
            return

        updated = rows.update(relevancy=score)
        self.stdout.write(self.style.SUCCESS(
            f'Done: set relevancy={score} on {updated} post(s) matching {term!r}.'))
