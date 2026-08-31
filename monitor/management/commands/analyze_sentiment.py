"""
monitor/management/commands/analyze_sentiment.py

Re-score mentions' sentiment via a real contextual read (Groq — see
monitor/sentiment_ai.py) instead of whatever non-LLM source set it
(crawler VADER, a MediaHost vendor tag, or manual entry).

By default only processes mentions that haven't been AI-analysed yet
(sentiment_rationale is blank) — safe to run repeatedly (e.g. on a schedule)
without re-spending API calls on rows it already handled. --force re-analyses
everything in scope, overwriting any existing sentiment_rationale.

Usage:
    python manage.py analyze_sentiment                      # every media type, all orgs, unanalysed only
    python manage.py analyze_sentiment --org <uuid>          # one org only
    python manage.py analyze_sentiment --media-type online   # online|print|social|broadcast|competitor|all
    python manage.py analyze_sentiment --limit 200           # cap how many rows this run touches
    python manage.py analyze_sentiment --force               # re-analyse even already-analysed rows
    python manage.py analyze_sentiment --dry-run             # print what would change, no writes

Not scheduled by Celery Beat by default — run ad hoc, or wire it into
CELERY_BEAT_SCHEDULE the same way as the other monitor.* tasks once you're
happy with how it performs on real data.
"""
import time

from django.core.management.base import BaseCommand, CommandError

from monitor.models import (
    BroadcastMention, CompetitorArticle, OnlineArticle, Organization, PrintArticle, SocialMediaPost,
)
from monitor.sentiment_ai import analyze_sentiment

# (label, model, extra text fields to try for context beyond headline/summary)
MEDIA_TYPES = {
    'online': ('Online', OnlineArticle),
    'print': ('Print', PrintArticle),
    'social': ('Social', SocialMediaPost),
    'broadcast': ('Broadcast', BroadcastMention),
    'competitor': ('Competitor', CompetitorArticle),
}

# A short pause between calls — comfortably inside Groq's free-tier rate
# limit even for a large backfill, without meaningfully slowing a run of a
# few hundred rows (see sentiment_ai.py's docstring for the token math).
PAUSE_SECONDS = 0.3


class Command(BaseCommand):
    help = "Re-score mention sentiment via a real contextual AI read (Groq)"

    def add_arguments(self, parser):
        parser.add_argument('--org', type=str, help='Limit to a single org UUID')
        parser.add_argument('--media-type', type=str, default='all',
                            help="online|print|social|broadcast|competitor|all (default: all)")
        parser.add_argument('--limit', type=int, help='Cap how many rows this run touches')
        parser.add_argument('--force', action='store_true',
                            help='Re-analyse rows that already have a sentiment_rationale')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would change; make no database writes')

    def handle(self, *args, **options):
        media_type = options['media_type']
        if media_type != 'all' and media_type not in MEDIA_TYPES:
            raise CommandError(f"--media-type must be one of: all, {', '.join(MEDIA_TYPES)}")
        types = MEDIA_TYPES if media_type == 'all' else {media_type: MEDIA_TYPES[media_type]}

        org = None
        if options['org']:
            try:
                org = Organization.objects.get(id=options['org'])
            except Organization.DoesNotExist:
                raise CommandError(f"No organization with id={options['org']}")

        limit = options['limit']
        force = options['force']
        dry_run = options['dry_run']

        # Give each media type its own fair share of `limit`, rather than
        # consuming it sequentially in dict order. Online alone routinely has
        # 1000+ unanalysed rows (more than the default limit=300) and keeps
        # growing from live crawling — under strict sequential consumption it
        # silently ate the ENTIRE budget every scheduled run, so broadcast/
        # print/social/competitor got zero analysis passes, indefinitely,
        # regardless of how large their own backlogs were. Confirmed live
        # 2026-08-31: 625 BroadcastMention rows, 0 ever analysed, despite the
        # task "succeeding" every 30 min for weeks.
        per_type_limit = None
        if limit:
            per_type_limit = max(1, limit // len(types))

        processed = analyzed = skipped = 0
        for label, model in types.values():
            qs = model.objects.select_related('organization').all()
            if org:
                qs = qs.filter(organization=org)
            if not force:
                qs = qs.filter(sentiment_rationale='')
            if per_type_limit:
                qs = qs[:per_type_limit]

            for mention in qs:
                processed += 1
                result = analyze_sentiment(
                    mention.organization.name, mention.headline, mention.summary)
                if result is None:
                    skipped += 1
                    self.stderr.write(self.style.WARNING(
                        f'  [{label}] {mention.id}: analysis failed or unavailable — left unchanged'))
                    continue

                old = mention.sentiment
                if dry_run:
                    self.stdout.write(
                        f"  [{label}] {mention.id}: {old} -> {result['sentiment']} "
                        f"({result['rationale']})")
                else:
                    mention.sentiment = result['sentiment']
                    mention.sentiment_rationale = result['rationale']
                    mention.save(update_fields=['sentiment', 'sentiment_rationale'])
                analyzed += 1
                time.sleep(PAUSE_SECONDS)

        verb = 'Would analyse' if dry_run else 'Analysed'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {analyzed}/{processed} mentions ({skipped} skipped — AI unavailable/failed).'))
