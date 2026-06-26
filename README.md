# SocialLight — Media Monitoring Platform

A multi-tenant **media intelligence platform** (Django) for Social Light Botswana. It
ingests media coverage — print, online, broadcast, and social — about client
organisations, scores each item for relevance and sentiment, and produces dashboards,
analytics, scheduled alert emails, and AI-generated slide reports.

Everything is scoped by `Organization` (UUID primary key): every coverage record, view,
and API endpoint belongs to one tenant.

## Features

- **Four ingestion channels** feeding one database (see [Architecture](#architecture)):
  automated pull from the mediahost clips API, a webhook receiver for the Media Monitor
  crawler, the Article Extractor service, and manual / CSV entry.
- Per-organisation **keyword routing** — incoming clips are routed to the org(s) whose
  tracked keywords match.
- Deterministic **relevancy scoring** (0–100) and sentiment (positive / neutral / negative)
  on every mention.
- **Dashboards & analytics** per media type, with Chart.js visualisations.
- **AI report analysis** (Anthropic) — ESG matrix, stakeholder radar, competitor ranking,
  reputational risks/opportunities, and KPI insights, rendered as a slide deck.
- **Scheduled alert digests** (daily / immediate) delivered by email.
- Competitor tracking and comparison.

## Architecture

```
 SOURCES                    INGESTION & ROUTING            STORAGE            DELIVERY
 ───────                    ───────────────────            ───────            ────────
 mediahost clips API ─poll─▶ mediahost client +     ┌────────────────┐  ┌─ dashboards / analytics
                            ingest_clips (dedup,    │  PostgreSQL    │  │  (Django web, Chart.js)
                            keyword→org routing) ───▶│                │──┤
 Media Monitor crawler ─────▶ webhook receiver  ────▶│ Organization-  │  ├─ AI slide reports
   (sibling service)                                 │ scoped mention │  │  (ReportAnalysis)
                                                     │ models, Keyword│  │
 Article Extractor ─────────▶ (full-text extract) ──▶│ /Competitor,   │  └─ alert digest emails
   (sibling service)                                 │ ReportAnalysis,│     (Resend / Anymail)
                                                     │ Alert          │
 Manual + CSV upload ───────▶ create / CSV endpoints▶│                │
                                                     └────────────────┘
                            relevancy + sentiment        ▲       ▲
                            scored at write time         │       │
                                                     Celery worker / beat
                                                  (Redis broker · Africa/Gaborone)
```

### Ingestion channels

1. **Mediahost clips API** (primary automated pull) — `monitor/mediahost.py`. A polling
   client calls `GET /api/clips` (single global `x-api-key`, SAST/UTC+2 date windows,
   paginated). Clips arrive as Print / Online / Broadcast and are mapped to the
   corresponding models. **Each clip is routed to the organisation(s) whose `Keyword`
   matches the clip's `search` term**, deduplicated per org, relevancy-scored, and
   bulk-inserted. Runs every 6 hours via Celery Beat
   (`monitor/management/commands/import_mediahost_clips.py`, `monitor/tasks.py`).

2. **Media Monitor crawler** (webhook push) — a companion service (`../media-monitor`,
   its own web/worker/beat containers) crawls the web and POSTs article matches to
   `media_monitor_webhook` (`/api/<org_id>/webhook/media-monitor/`, secured by
   `X-Webhook-Secret`). Each match becomes an `OnlineArticle`.

3. **Article Extractor** — a sibling microservice (`../article-extractor`, port 8001,
   `ARTICLE_EXTRACTOR_URL`) for pulling full article content.

4. **Manual + CSV upload** — every media type exposes create/update/delete and
   CSV-upload endpoints (`monitor/urls.py`) for analysts.

### Data model — `monitor/models.py`

- **Tenancy & auth:** `Organization`, custom `User` (roles: platform_admin / org_admin /
  viewer), `Keyword` (brand / personnel / campaign — drives routing *and* relevance),
  `Competitor` (with aliases).
- **Coverage:** `OnlineArticle`, `PrintArticle`, `SocialMediaPost`, `BroadcastMention`
  (all org-scoped, all carry sentiment / AVE / relevancy), plus `CompetitorArticle` and
  `MediaSource`.
- **Reports & alerts:** `GeneratedReport` (saved report config), `ReportAnalysis`
  (persisted AI output, reused until new data arrives or a forced refresh), `Alert`
  (digest config — recipients, frequency, last-sent watermark).

### Processing

- **Relevancy** — `monitor/relevancy.py`: a deterministic 0–100 score computed at
  write time from keyword/competitor hits in headline + summary, weighted by category. A
  display-time threshold (`MENTION_RELEVANCY_THRESHOLD`) filters noisy matches without
  re-importing.
- **AI analysis** — `monitor/report_ai.py`: one Anthropic call per (organisation, period)
  returns structured JSON (ESG, stakeholders, competitor ranking, reputational
  risks/opportunities, KPI insights), persisted in `ReportAnalysis` and regenerated only
  on new data or a forced refresh. Page loads never call the API.
- **Presentation** — `monitor/templates/monitor/partials/report_section.html` renders the
  slide deck client-side (Chart.js) from JSON embedded in `data-*` attributes.

## Tech stack

- **Backend:** Django 5.2, Gunicorn
- **Database:** PostgreSQL 15 (SQLite for local dev by default)
- **Async:** Celery + Redis (broker), Celery Beat scheduler (`django_celery_beat`)
- **AI:** Anthropic API (`claude-sonnet-4-6`)
- **Email:** Resend via Anymail
- **Frontend:** Django templates + Chart.js, Bootstrap 5 (crispy-forms)
- **Infra:** Docker Compose, Nginx reverse proxy

## Quick start (local development)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in the keys you need:

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django secret key |
| `DEBUG` | `True` for local dev |
| `ANTHROPIC_API_KEY` | AI report analysis (optional — sections are omitted if unset) |
| `MEDIAHOST_API_KEY` | mediahost clips import (blank = import disabled) |
| `CELERY_BROKER_URL` | Redis broker (e.g. `redis://localhost:6379/0`) |
| `RESEND_API_KEY` | Outbound email via Resend/Anymail |
| `SITE_URL` | Public base URL, used for absolute links in alert emails |

### 3. Run migrations and start the server

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Visit http://127.0.0.1:8000.

### 4. (Optional) run the background workers

Alert digests and the mediahost import run on Celery Beat. Locally:

```bash
celery -A socialmonitor worker -l info
celery -A socialmonitor beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

### Useful management commands

```bash
python manage.py import_mediahost_clips --days 7        # pull clips from the mediahost API
python manage.py import_mediahost_clips --probe         # inspect the raw clip schema
python manage.py send_daily_alerts --frequency daily    # send due alert digests
```

## Deployment (Docker Compose)

`docker-compose.yml` brings up the full stack: the Django `web` service, `celery_worker`,
`celery_beat`, PostgreSQL, Redis, and Nginx — alongside the sibling **Article Extractor**
and **Media Monitor** crawler services.

```bash
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py collectstatic --noinput
docker compose exec web python manage.py createsuperuser
```

For production set `DEBUG=False`, configure `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`, and
point `CELERY_BROKER_URL` at the `redis` service (`redis://redis:6379/0`). The timezone is
`Africa/Gaborone`.

## Project structure

```
socialmonitor/
├── manage.py
├── requirements.txt
├── docker-compose.yml          # web, workers, db, redis, nginx, extractor, crawler
├── Dockerfile
├── socialmonitor/              # Django project config
│   ├── settings.py             # apps, Celery Beat schedule, integrations
│   ├── celery.py
│   └── urls.py
└── monitor/                    # main app
    ├── models.py               # Organization, mention models, Keyword, Alert, ...
    ├── views.py                # dashboards, media CRUD, reports, webhook
    ├── mediahost.py            # mediahost API client + ingest/routing
    ├── relevancy.py            # deterministic relevancy scoring
    ├── report_ai.py            # Anthropic report analysis
    ├── tasks.py                # Celery tasks (import, alerts)
    ├── management/commands/    # import_mediahost_clips, send_daily_alerts, ...
    └── templates/monitor/      # dashboard, media, reports (Chart.js slide deck)
```
