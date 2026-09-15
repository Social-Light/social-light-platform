"""
monitor/management/commands/pull_facebook_schedule.py

Pulls the latest dataset from a personal Apify schedule (netdesignr/
facebook-posts-scraper, run @daily on Tony's own Apify account against FNB
Botswana's page and BPC's Facebook profile — see APIFY_SCHEDULE_TOKEN /
APIFY_SCHEDULE_ACTOR_ID in .env) and ingests new posts as SocialMediaPost
rows. This is the automated version of the manual Facebook backfill done by
hand on 2026-09-04.

Deliberately separate from monitor/alert_xlsx.py and media-monitor's own
Apify integration (discovery/services/apify.py, a different actor —
apify/facebook-posts-scraper, keyword-search based, shared across every
org): this schedule is page-specific and lives on Tony's personal account,
so it gets its own small, self-contained puller rather than being folded
into the shared crawler pipeline.

    python manage.py pull_facebook_schedule
    python manage.py pull_facebook_schedule --dry-run

Safe to run more than once a day — dedupes on (organization, url) like every
other ingestion path on the platform.
"""
import datetime
import os

import requests
from django.core.management.base import BaseCommand

from monitor.models import Organization, SocialMediaPost
from monitor.relevancy import compute_relevancy
from monitor.sentiment_ai import analyze_sentiment

APIFY_API_BASE = "https://api.apify.com/v2"

# sourceUsername (from the scraper's own output) -> platform Organization
# name. Extend this if the schedule's startUrls list grows to cover more
# pages; an unmapped source is reported, never silently dropped.
SOURCE_TO_ORG = {
    "FNBBotswana": "FNBB",
    "100057282685105": "Botswana Power Corporation",
}

# Sentiment-weighted rank, matching the convention discovered across the rest
# of the platform's real SocialMediaPost.rank data (see the 2026-09-04
# investigation): positive/neutral/negative average roughly 3.72/2.16/-0.11.
# There is no platform-native formula for rank (see alert_xlsx.py's Rank
# column and media_social.html's CSV-import docs) — this is Tony's own
# convention, applied consistently since that date.
SENTIMENT_RANK = {"positive": 3.72, "neutral": 2.16, "negative": -0.11}

# AVE = reach * AVE_RATE — mirrors media-monitor's fetcher/bridge.py::_social_ave.
AVE_RATE = 0.35


class Command(BaseCommand):
    help = "Pull the latest run of the personal Apify Facebook-posts schedule into SocialMediaPost."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                             help="Report what would be created; write nothing.")

    def handle(self, *args, **options):
        token = os.environ.get("APIFY_SCHEDULE_TOKEN", "")
        actor_id = os.environ.get("APIFY_SCHEDULE_ACTOR_ID", "")
        if not token or not actor_id:
            self.stderr.write("APIFY_SCHEDULE_TOKEN / APIFY_SCHEDULE_ACTOR_ID not configured — skipping.")
            return

        runs_resp = requests.get(
            f"{APIFY_API_BASE}/acts/{actor_id}/runs",
            params={"token": token, "desc": "true", "limit": 1, "status": "SUCCEEDED"},
            timeout=20,
        )
        runs_resp.raise_for_status()
        run_items = runs_resp.json()["data"]["items"]
        if not run_items:
            self.stdout.write("No successful runs found yet.")
            return
        dataset_id = run_items[0]["defaultDatasetId"]
        finished_at = run_items[0]["finishedAt"]

        ds_resp = requests.get(
            f"{APIFY_API_BASE}/datasets/{dataset_id}/items",
            params={"token": token},
            timeout=30,
        )
        ds_resp.raise_for_status()
        posts = ds_resp.json()

        self.stdout.write(f"Latest run finished {finished_at} — {len(posts)} post(s) in dataset.")

        created = skipped_existing = skipped_no_text = skipped_unmapped = 0
        for post in posts:
            username = post.get("sourceUsername", "")
            org_name = SOURCE_TO_ORG.get(username)
            if not org_name:
                skipped_unmapped += 1
                self.stdout.write(self.style.WARNING(
                    f"  unmapped source '{username}' ({post.get('sourceName')}) — "
                    f"add it to SOURCE_TO_ORG if this page should be tracked"))
                continue

            url = (post.get("postUrl") or "").strip()
            if not url:
                continue

            try:
                org = Organization.objects.get(name=org_name)
            except Organization.DoesNotExist:
                self.stderr.write(f"  organisation '{org_name}' not found — skipping post {url}")
                continue

            if SocialMediaPost.objects.filter(organization=org, url=url).exists():
                skipped_existing += 1
                continue

            text = (post.get("text") or "").strip()
            if not text:
                # Matches the platform's own rule (see match_article /
                # push_social_to_platform in media-monitor): nothing to
                # keyword-match or score without caption text. Logged, not
                # silently dropped, so an image/video-only post stays visible
                # for manual review — the OCR-fallback gap noted elsewhere.
                skipped_no_text += 1
                self.stdout.write(self.style.WARNING(f"  no caption text — skipped: {url}"))
                continue

            page = post.get("page") or {}
            reach = page.get("pageFollowers") or page.get("pageLikes") or 0
            reactions = post.get("totalReactions") or 0
            comments = post.get("comments") or 0
            shares = post.get("shares") or 0
            summary = (f"{text} Engagement at capture: {reactions} reactions, "
                       f"{comments} comments, {shares} shares.")
            headline = text[:500]

            relevancy = compute_relevancy(headline, summary, org=org)
            ai_result = analyze_sentiment(org.sentiment_subject or org.name, headline, summary)
            sentiment = ai_result["sentiment"] if ai_result else "neutral"
            rationale = ai_result["rationale"] if ai_result else ""
            rank = SENTIMENT_RANK[sentiment]
            ave = round(reach * AVE_RATE, 2)

            raw_time = post.get("time", "")
            try:
                date_published = datetime.date.fromisoformat(raw_time[:10]) if raw_time else datetime.date.today()
            except ValueError:
                date_published = datetime.date.today()

            if options["dry_run"]:
                self.stdout.write(
                    f"  [would create] org={org_name} sentiment={sentiment} relevancy={relevancy} "
                    f"reach={reach} ave={ave} rank={rank} date={date_published} | {headline[:60]}")
                created += 1
                continue

            SocialMediaPost.objects.create(
                organization=org,
                platform="Facebook",
                page_name=post.get("sourceName", ""),
                headline=headline,
                summary=summary,
                url=url,
                date_published=date_published,
                country=org.country or "Botswana",
                sentiment=sentiment,
                sentiment_rationale=rationale,
                ave=ave,
                rank=rank,
                reach=reach,
                relevancy=relevancy,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done. created={created} existing={skipped_existing} "
            f"no_text={skipped_no_text} unmapped={skipped_unmapped}"
            + (" (dry run — nothing written)" if options["dry_run"] else "")
        ))
