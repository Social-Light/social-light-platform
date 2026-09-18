"""
monitor/management/commands/analyze_relevancy.py

Disambiguate mentions' relevancy via a real contextual read (Groq — see
monitor/relevancy_ai.py). monitor/relevancy.py's compute_relevancy() is a
deterministic keyword-match score with no understanding of context, so a
short/generic tracked term (e.g. Botswana Power Corporation's "BPC") scores
identically whether the coverage is genuinely about the org or an unrelated
entity that happens to share the term (the peptide "BPC-157", the "British
Psychoanalytic Council", "Building and Plumbing Commission", ...). This
command adds the read compute_relevancy can't do, and records the verdict on
relevancy_ai_relevant/relevancy_ai_rationale — filter_relevant() then always
excludes a confirmed false positive, regardless of its keyword score.

By default only processes rows with relevancy > 0 (nothing to disambiguate
below that) whose relevancy_ai_relevant is still NULL (not yet checked) —
safe to run repeatedly (e.g. on a schedule) without re-spending API calls on
rows it already handled. --force re-checks everything in scope, overwriting
any existing verdict.

Usage:
    python manage.py analyze_relevancy                      # every media type, all orgs, unchecked only
    python manage.py analyze_relevancy --org <uuid>          # one org only
    python manage.py analyze_relevancy --media-type online   # online|print|social|broadcast|all
    python manage.py analyze_relevancy --limit 200           # cap how many rows EACH media type touches
    python manage.py analyze_relevancy --force               # re-check even already-checked rows
    python manage.py analyze_relevancy --dry-run             # print what would change, no writes

Scheduled by Celery Beat (see CELERY_BEAT_SCHEDULE in settings /
monitor.tasks.analyze_relevancy_task) — can also be triggered ad hoc.

--limit is applied PER media type (weighted by TYPE_WEIGHTS), not as one
shared budget — see analyze_sentiment.py's module docstring for why an even
or sequential split starves whichever type has the smaller backlog; the same
reasoning applies here, and this shares the exact same Groq daily-token pool
as analyze_sentiment/report_ai/sector_ai/issue_report_ai, so it's scoped with
the same cutoff-date guard (RELEVANCY_AI_CUTOFF_DATE) rather than chewing
through the entire historical backlog on every scheduled run.
"""
import time
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from monitor.models import BroadcastMention, OnlineArticle, Organization, PrintArticle, SocialMediaPost
from monitor.relevancy import matched_terms as compute_matched_terms
from monitor.relevancy_ai import disambiguate_relevancy

MEDIA_TYPES = {
    'online': ('Online', OnlineArticle),
    'print': ('Print', PrintArticle),
    'social': ('Social', SocialMediaPost),
    'broadcast': ('Broadcast', BroadcastMention),
}

# Same split as analyze_sentiment.py's TYPE_WEIGHTS — Social/Broadcast carry
# far more volume than Online/Print in practice.
TYPE_WEIGHTS = {
    'online': 1,
    'print': 1,
    'social': 3,
    'broadcast': 3,
}


def _split_limit(limit, keys):
    """See analyze_sentiment.py's identically-named function for the full
    reasoning — divides `limit` across `keys` proportional to TYPE_WEIGHTS
    via largest-remainder apportionment, never exceeding `limit` in total."""
    key_list = list(keys)
    n = len(key_list)
    if limit <= 0 or n == 0:
        return {k: 0 for k in key_list}

    total_weight = sum(TYPE_WEIGHTS.get(k, 1) for k in key_list) or n
    raw = {k: limit * TYPE_WEIGHTS.get(k, 1) / total_weight for k in key_list}
    shares = {k: int(raw[k]) for k in key_list}

    min_each = 1 if limit >= n else 0
    if min_each:
        for k in key_list:
            if shares[k] == 0:
                shares[k] = 1

    overflow = sum(shares.values()) - limit
    if overflow > 0:
        for k in sorted(key_list, key=lambda k: shares[k], reverse=True):
            while overflow > 0 and shares[k] > min_each:
                shares[k] -= 1
                overflow -= 1
    elif overflow < 0:
        remainder = -overflow
        for k in sorted(key_list, key=lambda k: raw[k] - int(raw[k]), reverse=True):
            if remainder <= 0:
                break
            shares[k] += 1
            remainder -= 1

    return shares


# Same pause as analyze_sentiment.py — comfortably inside Groq's free-tier
# rate limit even for a large backfill.
PAUSE_SECONDS = 0.3


class Command(BaseCommand):
    help = "Disambiguate mention relevancy via a real contextual AI read (Groq)"

    def add_arguments(self, parser):
        parser.add_argument('--org', type=str, help='Limit to a single org UUID')
        parser.add_argument('--media-type', type=str, default='all',
                            help="online|print|social|broadcast|all (default: all)")
        parser.add_argument('--limit', type=int,
                            help='Cap how many rows EACH media type touches this run '
                                 '(not a shared total — see module docstring)')
        parser.add_argument('--force', action='store_true',
                            help='Re-check rows that already have an AI verdict')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print what would change; make no database writes')
        parser.add_argument('--include-backlog', action='store_true',
                            help='Ignore RELEVANCY_AI_CUTOFF_DATE and also consider mentions '
                                 'published before it (a deliberate one-off catch-up)')

    def handle(self, *args, **options):
        media_type = options['media_type']
        if media_type != 'all' and media_type not in MEDIA_TYPES:
            raise CommandError(f"--media-type must be one of: all, {', '.join(MEDIA_TYPES)}")
        types = MEDIA_TYPES if media_type == 'all' else {media_type: MEDIA_TYPES[media_type]}

        org_filter = None
        if options['org']:
            try:
                org_filter = Organization.objects.get(id=options['org'])
            except Organization.DoesNotExist:
                raise CommandError(f"No organization with id={options['org']}")

        limit = options['limit']
        force = options['force']
        dry_run = options['dry_run']

        cutoff = None
        if not options['include_backlog']:
            raw_cutoff = (getattr(settings, 'RELEVANCY_AI_CUTOFF_DATE', '') or '').strip()
            if raw_cutoff:
                try:
                    cutoff = date.fromisoformat(raw_cutoff)
                except ValueError:
                    raise CommandError(
                        f"RELEVANCY_AI_CUTOFF_DATE={raw_cutoff!r} is not a valid YYYY-MM-DD date")

        per_type_limit = _split_limit(limit, types.keys()) if limit else {}

        # Cache each org's keywords/competitors across the run — many rows in
        # scope share the same handful of orgs, and these querysets are cheap
        # to fetch once and reuse rather than re-querying per row.
        org_terms_cache = {}

        def terms_for(org):
            if org.id not in org_terms_cache:
                org_terms_cache[org.id] = {
                    'keywords': list(org.keywords.all()),
                    'competitors': list(org.competitors.all()),
                }
            return org_terms_cache[org.id]

        processed = checked = flagged = skipped = 0
        for key, (label, model) in types.items():
            qs = model.objects.select_related('organization').filter(relevancy__gt=0)
            if org_filter:
                qs = qs.filter(organization=org_filter)
            if not force:
                qs = qs.filter(relevancy_ai_relevant__isnull=True)
            if cutoff:
                qs = qs.filter(date_published__gte=cutoff)
            if per_type_limit:
                qs = qs[:per_type_limit[key]]

            for mention in qs:
                processed += 1
                org = mention.organization
                cache = terms_for(org)
                terms = compute_matched_terms(
                    mention.headline, mention.summary, keywords=cache['keywords'],
                    competitors=cache['competitors'])
                if not terms:
                    # relevancy > 0 came from the body-inclusive score, or the
                    # squashed-text fallback against a field not passed here —
                    # nothing to hand the AI check confidently. Leave it alone.
                    skipped += 1
                    continue

                subject = org.sentiment_subject or org.name
                result = disambiguate_relevancy(subject, terms, mention.headline, mention.summary)
                if result is None:
                    skipped += 1
                    self.stderr.write(self.style.WARNING(
                        f'  [{label}] {mention.id}: disambiguation failed or unavailable — left unchanged'))
                    continue

                if dry_run:
                    verdict = 'relevant' if result['relevant'] else 'FALSE POSITIVE'
                    self.stdout.write(
                        f"  [{label}] {mention.id}: {verdict} (terms={terms}) — {result['rationale']}")
                else:
                    mention.relevancy_ai_relevant = result['relevant']
                    mention.relevancy_ai_rationale = result['rationale']
                    mention.save(update_fields=['relevancy_ai_relevant', 'relevancy_ai_rationale'])
                checked += 1
                if not result['relevant']:
                    flagged += 1
                time.sleep(PAUSE_SECONDS)

        verb = 'Would check' if dry_run else 'Checked'
        scope = f', published on/after {cutoff.isoformat()}' if cutoff else ''
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {checked}/{processed} mentions ({flagged} flagged as false positives, '
            f'{skipped} skipped — no matched terms or AI unavailable/failed){scope}.'))
