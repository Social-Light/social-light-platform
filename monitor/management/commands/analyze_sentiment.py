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
    python manage.py analyze_sentiment --limit 200           # cap how many rows EACH media type touches
    python manage.py analyze_sentiment --force               # re-analyse even already-analysed rows
    python manage.py analyze_sentiment --dry-run             # print what would change, no writes

Scheduled every 30 min by Celery Beat (see CELERY_BEAT_SCHEDULE in settings /
monitor.tasks.analyze_sentiment_task), with media_type='all' and the default
limit — can also be triggered ad hoc.

--limit is applied PER media type, not as one shared budget across all of
them (2026-08-31 fix — see git history: with a single shared budget consumed
in dict order, 'online' alone routinely has more unanalysed rows than the
whole --limit, so it silently ate the entire budget every run and
'broadcast'/'competitor' (later in MEDIA_TYPES) never got touched — Broadcast
sat at 616/627 still-unanalysed while Online quietly drained, which is
exactly what "new sentiment from radio isn't reflecting" turned out to be).

The per-type split is weighted, not even (2026-09-01, at Tony's request) —
see TYPE_WEIGHTS below. Even splitting cleared Online/Print's much smaller
backlogs fast (40%/36% done) while Social/Broadcast/Competitor's much larger
ones barely moved (0.6%/3.5%/0.6%) on the same fixed per-run slice — so
Social and Broadcast now get 3x Online/Print/Competitor's share of --limit.

2026-09-01, also at Tony's request: this had no date scope at all, so every
scheduled run (every 30 min, indefinitely) also chipped away at the entire
historical backlog of unanalysed mentions across all orgs — real quota spent
competing with report_ai.py/live traffic against the same shared Groq daily
cap. settings.SENTIMENT_AI_CUTOFF_DATE now excludes anything published
before it by default; pass --include-backlog for a deliberate one-off
catch-up on older rows instead.
"""
import time
from datetime import date

from django.conf import settings
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

# Relative share of --limit each media type gets when splitting it across
# the types in scope (see _split_limit below). Social/Broadcast get the
# largest share — their backlogs are far bigger and were barely moving
# under an even split (see module docstring).
TYPE_WEIGHTS = {
    'online': 1,
    'print': 1,
    'social': 3,
    'broadcast': 3,
    'competitor': 1,
}


def _split_limit(limit, keys):
    """Divide `limit` across `keys` proportional to TYPE_WEIGHTS, each type
    getting at least 1. The last key absorbs any rounding remainder so the
    shares always sum to exactly `limit` (or close under it, adjusted down
    only if the floor-of-1 minimums alone would exceed it)."""
    total_weight = sum(TYPE_WEIGHTS.get(k, 1) for k in keys)
    shares = {}
    remaining = limit
    key_list = list(keys)
    for i, key in enumerate(key_list):
        if i == len(key_list) - 1:
            shares[key] = max(1, remaining)
        else:
            share = max(1, round(limit * TYPE_WEIGHTS.get(key, 1) / total_weight))
            shares[key] = share
            remaining -= share
    return shares

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
        parser.add_argument('--limit', type=int,
                            help='Cap how many rows EACH media type touches this run '
                                 '(not a shared total — see module docstring)')
        parser.add_argument('--force', action='store_true',
                            help='Re-analyse rows that already have a sentiment_rationale')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would change; make no database writes')
        parser.add_argument('--include-backlog', action='store_true',
                            help='Ignore SENTIMENT_AI_CUTOFF_DATE and also consider mentions '
                                 'published before it (a deliberate one-off catch-up)')

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

        cutoff = None
        if not options['include_backlog']:
            raw_cutoff = (getattr(settings, 'SENTIMENT_AI_CUTOFF_DATE', '') or '').strip()
            if raw_cutoff:
                try:
                    cutoff = date.fromisoformat(raw_cutoff)
                except ValueError:
                    raise CommandError(
                        f"SENTIMENT_AI_CUTOFF_DATE={raw_cutoff!r} is not a valid YYYY-MM-DD date")

        # Give each media type its own weighted share of `limit`, rather than
        # consuming it sequentially in dict order or splitting it evenly.
        # Online alone routinely has 1000+ unanalysed rows (more than the
        # default limit=300) and keeps growing from live crawling — under
        # strict sequential consumption it silently ate the ENTIRE budget
        # every scheduled run, so broadcast/print/social/competitor got zero
        # analysis passes, indefinitely, regardless of how large their own
        # backlogs were. Confirmed live 2026-08-31: 625 BroadcastMention
        # rows, 0 ever analysed, despite the task "succeeding" every 30 min
        # for weeks. An even split then under-served Social/Broadcast's much
        # bigger backlogs relative to Online/Print/Competitor's — see
        # TYPE_WEIGHTS above.
        per_type_limit = _split_limit(limit, types.keys()) if limit else {}

        processed = analyzed = skipped = 0
        for key, (label, model) in types.items():
            qs = model.objects.select_related('organization').all()
            if org:
                qs = qs.filter(organization=org)
            if not force:
                qs = qs.filter(sentiment_rationale='')
            if cutoff:
                qs = qs.filter(date_published__gte=cutoff)
            if per_type_limit:
                qs = qs[:per_type_limit[key]]

            for mention in qs:
                processed += 1
                # An agency org (e.g. "Launch Comms") tracks coverage of its
                # CLIENT (e.g. "Botswana Power Corporation") — reasoning from
                # the agency's own name produces false-neutral scores, since
                # the agency itself is never the one mentioned in the text.
                # sentiment_subject overrides who the AI reasons as when set;
                # blank (the default, for orgs that ARE the brand) falls back
                # to the org's own name, unchanged from before.
                subject = mention.organization.sentiment_subject or mention.organization.name
                result = analyze_sentiment(subject, mention.headline, mention.summary)
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
        scope = f', published on/after {cutoff.isoformat()}' if cutoff else ''
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {analyzed}/{processed} mentions ({skipped} skipped — AI unavailable/failed){scope}.'))
