"""
monitor/management/commands/fix_print_sections.py

Clear purely-numeric print article sections. The mediahost feed sometimes put a
page number in the section field; a section should be a name (Main, Business,
Sport, ...), so a bare number is meaningless. Blanking it makes the row show as
"Main". New ingests already drop numeric sections (see mediahost._print_section).

Usage:
    python manage.py fix_print_sections             # clear numeric sections
    python manage.py fix_print_sections --dry-run    # report only, write nothing
    python manage.py fix_print_sections --org <uuid> # restrict to one organisation
"""
from django.core.management.base import BaseCommand, CommandError

from monitor.models import Organization, PrintArticle


class Command(BaseCommand):
    help = 'Clear purely-numeric print article sections (leftover page numbers from the feed).'

    def add_arguments(self, parser):
        parser.add_argument('--org', dest='org_id', type=str,
                            help='Restrict to a single organisation id.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        qs = PrintArticle.objects.exclude(section='')
        if opts['org_id']:
            if not Organization.objects.filter(id=opts['org_id']).exists():
                raise CommandError(f"No organisation with id {opts['org_id']!r}.")
            qs = qs.filter(organization_id=opts['org_id'])

        changed = []
        for row in qs.only('id', 'section').iterator():
            if (row.section or '').strip().isdigit():
                row.section = ''
                changed.append(row)

        if changed and not opts['dry_run']:
            PrintArticle.objects.bulk_update(changed, ['section'], batch_size=1000)

        verb = 'would clear' if opts['dry_run'] else 'cleared'
        self.stdout.write(self.style.SUCCESS(
            f'Done: {verb} numeric section on {len(changed)} print article(s).'))
