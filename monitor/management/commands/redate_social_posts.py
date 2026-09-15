"""
monitor/management/commands/redate_social_posts.py

Re-date SocialMediaPost rows whose date_published looks like an ingestion-
time fallback ("today, because nothing better was found") rather than a
real publish date — reading the post's own headline+summary for date
evidence via Groq (see monitor/date_ai.py).

Scope: only rows where date_published == created_at's date (the fallback
signature) are touched by default — a row with a genuinely different date
already has real data, don't second-guess it. --all widens to every row.

Usage:
    python manage.py redate_social_posts                  # candidates only, all orgs
    python manage.py redate_social_posts --org <uuid>      # one org only
    python manage.py redate_social_posts --limit 200        # cap rows touched
    python manage.py redate_social_posts --month 2026-08     # only rows ingested that month
    python manage.py redate_social_posts --all               # include already-correct-looking rows too
    python manage.py redate_social_posts --dry-run           # print what would change, no writes

--month filters on created_at (ingestion date), not date_published — for
the default candidate scope that's the same value anyway (the fallback
signature IS date_published == created_at's date), so --month picks out
"posts ingested in August" the same way whether or not they've been touched
yet. Combine with --all to instead mean "re-check every August row,
including ones that already look correctly dated."

Not scheduled by Celery Beat — this is a backfill for existing mis-dated
rows, not an ongoing per-mention job like analyze_sentiment. Run ad hoc,
re-run later to keep chipping at whatever quota didn't cover this time
(only rows still matching the candidate filter get touched again).
"""
import time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F
from django.db.models.functions import TruncDate
from django.utils import timezone

from monitor.date_ai import extract_published_date
from monitor.models import Organization, SocialMediaPost

# Same pacing as analyze_sentiment.py — comfortably inside Groq's free-tier
# rate limit even for a large backfill.
PAUSE_SECONDS = 0.3


class Command(BaseCommand):
    help = "Re-date SocialMediaPost rows stuck on their ingestion date, via a contextual AI read (Groq)"

    def add_arguments(self, parser):
        parser.add_argument('--org', type=str, help='Limit to a single org UUID')
        parser.add_argument('--limit', type=int, help='Cap how many rows this run touches')
        parser.add_argument('--month', type=str,
                            help='Limit to rows ingested (created_at) in this month, '
                                 'YYYY-MM, e.g. 2026-08')
        parser.add_argument('--all', action='store_true',
                            help='Also re-check rows that already look correctly dated '
                                 '(default: only date_published == ingestion date)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would change; make no database writes')

    def handle(self, *args, **options):
        org = None
        if options['org']:
            try:
                org = Organization.objects.get(id=options['org'])
            except Organization.DoesNotExist:
                raise CommandError(f"No organization with id={options['org']}")

        qs = SocialMediaPost.objects.all()
        if org:
            qs = qs.filter(organization=org)
        if options['month']:
            try:
                year, month = (int(part) for part in options['month'].split('-'))
                if not 1 <= month <= 12:
                    raise ValueError
            except ValueError:
                raise CommandError("--month must be in YYYY-MM format, e.g. 2026-08")
            qs = qs.filter(created_at__year=year, created_at__month=month)
        if not options['all']:
            qs = qs.annotate(created_date=TruncDate('created_at')).filter(
                date_published=F('created_date'))
        if options['limit']:
            qs = qs[:options['limit']]

        dry_run = options['dry_run']
        processed = changed = unchanged = 0

        for post in qs:
            processed += 1
            # created_at comes back UTC from the DB; TruncDate('created_at')
            # above buckets by the local (Africa/Gaborone) calendar date, so
            # this must too or the two disagree near local midnight.
            ingested_date = timezone.localtime(post.created_at).date()
            result = extract_published_date(post.headline, post.summary, ingested_date)

            if result is None or result['published_date'] == post.date_published:
                unchanged += 1
                continue

            old = post.date_published
            new_date = result['published_date']
            note = (f"AI-corrected from ingestion date {old} — evidence: "
                    f"{result['evidence'] or '(none given)'}")
            if dry_run:
                self.stdout.write(f"  {post.id}: {old} -> {new_date}  ({post.headline[:60]})")
            else:
                post.date_published = new_date
                post.date_correction_note = note
                post.save(update_fields=['date_published', 'date_correction_note'])
            changed += 1
            time.sleep(PAUSE_SECONDS)

        verb = 'Would re-date' if dry_run else 'Re-dated'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {changed}/{processed} posts ({unchanged} left unchanged — '
            f'no date evidence in text, or AI unavailable/failed).'))
