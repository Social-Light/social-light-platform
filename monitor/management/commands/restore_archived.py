"""
monitor/management/commands/restore_archived.py

Un-archive rows previously hidden via is_archived=True (see monitor.models.
ArchivableManager) — the reversible counterpart to archiving. Rows are never
deleted by the archive workflow, so this always has something to restore.

Usage:
    python manage.py restore_archived --org <uuid>                # everything for one org
    python manage.py restore_archived --org <uuid> --print         # print only
    python manage.py restore_archived --org <uuid> --broadcast     # broadcast only
    python manage.py restore_archived --dry-run                    # show counts, restore nothing
    python manage.py restore_archived --org <uuid> --yes           # skip the confirmation prompt

Model flags (--online / --print / --broadcast / --social / --competitor) are
additive; pass none to restore across all five. --org is required — this is a
targeted undo, not a global one, since "restore everything ever archived" is
rarely what's wanted.
"""
from django.core.management.base import BaseCommand, CommandError

from monitor.models import (
    Organization, OnlineArticle, PrintArticle, BroadcastMention,
    SocialMediaPost, CompetitorArticle,
)

MODEL_FLAGS = (
    ('online',     OnlineArticle,     '--online'),
    ('print',      PrintArticle,      '--print'),
    ('broadcast',  BroadcastMention,  '--broadcast'),
    ('social',     SocialMediaPost,   '--social'),
    ('competitor', CompetitorArticle, '--competitor'),
)


class Command(BaseCommand):
    help = 'Restore (un-archive) rows previously hidden via is_archived=True.'

    def add_arguments(self, parser):
        parser.add_argument('--org', type=str, required=True,
                            help='Restrict restoration to a single Organization UUID.')
        for name, _, flag in MODEL_FLAGS:
            parser.add_argument(flag, action='store_true', dest=name,
                                help=f'Restore {name} rows. Omit all model flags to restore every type.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show how many rows would be restored, restore nothing.')
        parser.add_argument('--yes', action='store_true',
                            help='Restore without the interactive confirmation prompt.')

    def handle(self, *args, **options):
        org = self._resolve_org(options['org'])

        selected = [f for f, _, _ in MODEL_FLAGS if options.get(f)]
        models = [m for f, m, _ in MODEL_FLAGS if not selected or f in selected]

        matches = {}
        total = 0
        for model in models:
            qs = model.all_objects.filter(organization=org, is_archived=True)
            count = qs.count()
            matches[model] = (qs, count)
            total += count

        self.stdout.write(f'Archived rows for "{org.name}":')
        for model, (_, count) in matches.items():
            self.stdout.write(f'  {model.__name__}: {count}')
        self.stdout.write(f'  Total: {total}')

        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to restore.'))
            return

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — nothing restored.'))
            return

        if not options['yes']:
            answer = input(f'Restore {total} row(s)? [y/N] ').strip().lower()
            if answer not in ('y', 'yes'):
                self.stdout.write('Aborted.')
                return

        restored = 0
        for model, (qs, _) in matches.items():
            n = qs.update(is_archived=False)
            restored += n
        self.stdout.write(self.style.SUCCESS(f'Restored {restored} row(s).'))

    def _resolve_org(self, org_arg):
        try:
            return Organization.objects.get(id=org_arg)
        except (Organization.DoesNotExist, ValueError):
            raise CommandError(f'Organization {org_arg!r} not found.')
