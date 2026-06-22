"""
monitor/management/commands/import_mediahost_clips.py

Fetch allocated clips from the mediahost API (GET /api/clips) and import them
into PrintArticle / OnlineArticle / BroadcastMention, routing each clip to the
organisation(s) whose Keyword matches the clip's `search` term.

Authentication uses the single global key in settings.MEDIAHOST_API_KEY.

Usage:
    python manage.py import_mediahost_clips                       # last 1 day, all orgs/types
    python manage.py import_mediahost_clips --days 7              # last 7 days
    python manage.py import_mediahost_clips --from "2026-05-01 00:00:00" --to "2026-05-31 23:59:59"
    python manage.py import_mediahost_clips --type Online         # one media type only
    python manage.py import_mediahost_clips --org <uuid>          # restrict routing to one org
    python manage.py import_mediahost_clips --dry-run             # fetch + map, write nothing
    python manage.py import_mediahost_clips --probe               # print the first raw clip and exit

Times are South Africa Standard Time (UTC+2), the project's TIME_ZONE.
"""
import json
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from monitor.models import Organization
from monitor.mediahost import (
    MediahostClient, MediahostError, ingest_clips, MEDIA_TYPES, SAST_FMT,
)


class Command(BaseCommand):
    help = 'Import allocated clips from the mediahost API into media coverage models.'

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='date_from', type=str,
                            help='Start datetime, SAST "YYYY-MM-DD HH:mm:ss". Default: --days ago.')
        parser.add_argument('--to', dest='date_to', type=str,
                            help='End datetime, SAST "YYYY-MM-DD HH:mm:ss". Default: now.')
        parser.add_argument('--days', type=int, default=1,
                            help='When --from is omitted, fetch this many days back (default 1).')
        parser.add_argument('--type', dest='media_type', type=str,
                            help=f'Restrict to one media type: {", ".join(MEDIA_TYPES)}.')
        parser.add_argument('--org', type=str,
                            help='Restrict routing to a single Organization UUID (default: all orgs).')
        parser.add_argument('--page-size', type=int, default=200,
                            help='API page size (max 500). Smaller is gentler on the slow host.')
        parser.add_argument('--timeout', type=int, default=None,
                            help='Per-request read timeout in seconds (default: settings.MEDIAHOST_TIMEOUT or 120).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Fetch and map but write nothing.')
        parser.add_argument('--probe', action='store_true',
                            help='Print the first raw clip JSON and exit (no writes).')

    def handle(self, *args, **options):
        media_type = options.get('media_type')
        if media_type and media_type not in MEDIA_TYPES:
            raise CommandError(f'--type must be one of {", ".join(MEDIA_TYPES)} (got {media_type!r}).')

        now = timezone.localtime()
        date_to = options.get('date_to') or now.strftime(SAST_FMT)
        if options.get('date_from'):
            date_from = options['date_from']
        else:
            date_from = (now - timedelta(days=options['days'])).strftime(SAST_FMT)

        try:
            client = MediahostClient(timeout=options['timeout'])
        except MediahostError as exc:
            raise CommandError(str(exc))

        # --probe: dump the first clip, then summarise every field across a sample
        # (which fields are populated, with an example) so link fields are easy to spot.
        if options['probe']:
            try:
                clips = []
                for clip in client.fetch_clips(date_from, date_to, media_type=media_type,
                                                page_size=options['page_size']):
                    clips.append(clip)
                    if len(clips) >= options['page_size']:
                        break
            except MediahostError as exc:
                raise CommandError(str(exc))
            if not clips:
                self.stdout.write('No clips returned for that window.')
                return
            self.stdout.write(self.style.MIGRATE_HEADING('First raw clip:'))
            self.stdout.write(json.dumps(clips[0], indent=2, default=str, ensure_ascii=False))

            # Field summary: for each key, how many clips have a non-"N/A" value + an example.
            na = {'', 'n/a', 'na', 'none', 'null', '-'}
            stats = {}
            for c in clips:
                if not isinstance(c, dict):
                    continue
                for k, v in c.items():
                    filled, example = stats.get(k, (0, ''))
                    sval = '' if v is None else str(v)
                    if sval.strip().lower() not in na:
                        filled += 1
                        if not example:
                            example = sval
                    stats[k] = (filled, example)
            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\nField summary across {len(clips)} clip(s) (key: filled/total — example):'))
            for k in sorted(stats):
                filled, example = stats[k]
                self.stdout.write(f'  {k}: {filled}/{len(clips)} — {example[:90]}')
            return

        restrict_org = self._resolve_org(options.get('org'))

        scope = f'"{restrict_org.name}" only' if restrict_org else 'all orgs (by keyword)'
        self.stdout.write(
            f'Fetching mediahost clips → {scope} '
            f'[{date_from} → {date_to}]'
            + (f' type={media_type}' if media_type else '')
            + (' (dry-run)' if options['dry_run'] else '')
        )

        try:
            summary = ingest_clips(
                date_from, date_to,
                media_type=media_type,
                dry_run=options['dry_run'],
                client=client,
                page_size=options['page_size'],
                restrict_org=restrict_org,
                logger=lambda msg: self.stderr.write(msg),
            )
        except MediahostError as exc:
            raise CommandError(str(exc))

        created = summary['created']
        skipped = summary['skipped']
        verb = 'Would create' if options['dry_run'] else 'Created'
        self.stdout.write(self.style.SUCCESS(
            f'Fetched {summary["fetched"]} clip(s). '
            f'{verb}: Print {created["Print"]}, Online {created["Online"]}, '
            f'Broadcast {created["Broadcast"]} (across {len(summary["per_org"])} org(s)).'
        ))
        for org_name, c in sorted(summary['per_org'].items()):
            self.stdout.write(f'  {org_name}: Print {c["Print"]}, Online {c["Online"]}, Broadcast {c["Broadcast"]}')
        self.stdout.write(
            f'Skipped: {skipped["duplicate"]} duplicate, {skipped["invalid"]} invalid, '
            f'{skipped["unknown_type"]} unknown type, {skipped["unmapped"]} unmapped, '
            f'{skipped.get("youtube", 0)} youtube.'
        )
        if summary['unmapped_searches']:
            top = sorted(summary['unmapped_searches'].items(), key=lambda kv: -kv[1])[:10]
            self.stdout.write('  Unmapped searches (add a Keyword to route these): '
                              + ', '.join(f'{t}×{n}' for t, n in top))

    def _resolve_org(self, org_arg):
        """Return the Organization for --org, or None to route across all orgs."""
        if not org_arg:
            return None
        try:
            return Organization.objects.get(id=org_arg)
        except (Organization.DoesNotExist, ValueError):
            raise CommandError(f'Organization {org_arg!r} not found.')
