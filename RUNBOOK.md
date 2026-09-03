# SocialLight — Runbook & Standard Operating Procedures (SOPs)

Operational procedures for the SocialLight Media Monitoring Platform. Covers production
releases, deployments, server recovery, crawler/ingestion failures, and database backups.

> **Audience:** operators with SSH access to the production droplet.
> **Keep this current** — when a procedure changes, update it here.

---

## 1. Environment reference

| Item | Value |
| --- | --- |
| Host | DigitalOcean droplet, Frankfurt (`ubuntu-s-2vcpu-4gb-120gb-intel-fra1`) |
| Resources | 2 vCPU / **4 GB RAM** (memory-tight — OOM SIGKILL has occurred) |
| Deploy directory | `/opt/sociallight/social-light-platform` (umbrella stack) |
| Orchestration | `docker-compose` **v1 (hyphenated command)** |
| Timezone | `Africa/Gaborone` (UTC+2); Celery `CELERY_ENABLE_UTC=False` |
| Public URL | https://sociallight.africa |

### Containers

| Container | Role |
| --- | --- |
| `sociallight_django` | Django web (Gunicorn) |
| `sociallight_celery_worker` | Celery worker (alerts, mediahost import) |
| `sociallight_celery_beat` | Celery Beat scheduler |
| `sociallight_db` | PostgreSQL 15 |
| `sociallight_redis` | Redis (Celery broker) |
| `sociallight_nginx` | Nginx reverse proxy + static |
| `sociallight_extractor` | Article Extractor service |
| `crawler_web` / `crawler_worker` / `crawler_beat` | Media Monitor crawler (sibling app) |

> **Command note:** production uses **`docker-compose`** (v1). On newer hosts the
> equivalent is `docker compose` (v2). All commands below use the v1 form — adjust if
> your host only has v2.

---

## 2. Production release (code deploy)

Standard procedure to ship a new version of the main platform.

### Pre-flight
1. Confirm the change is merged to `main` and tested.
2. Check for new migrations: `git log --oneline --stat origin/main | grep migrations`.
3. Note whether the release touches static assets, the Celery Beat schedule, or models
   (each has an extra step below).

### Deploy
```bash
ssh <user>@<droplet>
cd /opt/sociallight/social-light-platform

# 1. Pull the new code
git fetch origin
git checkout main
git pull origin main

# 2. Rebuild and restart (only rebuilds changed images)
docker-compose up -d --build

# 3. Apply migrations (if any)
docker-compose exec web python manage.py migrate

# 4. Refresh static files (see §2.1 for the --clear gotcha)
docker-compose exec web python manage.py collectstatic --noinput
docker-compose restart nginx
```

### Post-deploy verification
```bash
docker-compose ps                       # all services Up
docker-compose logs --tail=50 web       # no tracebacks on boot
curl -I https://sociallight.africa      # 200/302
```
Then load the site, log in, and open a dashboard + a report.

### 2.1 Static-asset gotcha (IMPORTANT)
Nginx serves `/static/` straight from `collectstatic` output. `collectstatic` **skips files
whose destination isn't older than the source**, so **replacing a same-named asset**
(e.g. swapping `favicon.ico` or `report_section.html`-referenced images) silently keeps
serving the old copy. When you've changed an existing file in place:
```bash
docker-compose exec web python manage.py collectstatic --noinput --clear
docker-compose restart web nginx
```
Brand-new filenames copy fine without `--clear`. Favicon `<link>` tags use `?v=N`
cache-busters for client caching.

### 2.2 Celery Beat schedule changes
The `DatabaseScheduler` syncs `CELERY_BEAT_SCHEDULE` into the DB **on beat startup only**.
After changing the schedule in `settings.py`, restart beat:
```bash
docker-compose restart celery_beat
docker-compose logs --tail=30 celery_beat   # confirm schedule loaded
```

### Rollback
```bash
cd /opt/sociallight/social-light-platform
git log --oneline -n 5            # find the previous good commit
git checkout <previous-good-sha>
docker-compose up -d --build
# If the bad release ran a migration, reverse it BEFORE checking out old code:
#   docker-compose exec web python manage.py migrate <app> <previous_migration>
```
> Reversing migrations can be destructive. If unsure, restore from backup (§5) instead.

---

## 3. Server recovery

### 3.1 Full stack restart (after reboot or hang)
```bash
cd /opt/sociallight/social-light-platform
docker-compose ps                # what's down
docker-compose up -d             # bring everything up
docker-compose logs --tail=100   # watch boot
```

### 3.2 Single service crash-looping
```bash
docker-compose ps                          # find the Restarting/Exited service
docker-compose logs --tail=200 <service>   # read the cause
docker-compose restart <service>
```

### 3.3 Out-of-memory (OOM) — the 4 GB box
The web container has been OOM-killed (SIGKILL / exit 137). Symptoms: `web` restarting,
502s from nginx.
```bash
free -h                                     # host memory
docker stats --no-stream                    # per-container usage
docker-compose logs --tail=100 web | grep -i -E 'killed|memory|137'
dmesg | grep -i -E 'oom|killed process'     # kernel OOM killer
```
Mitigations:
- Restart the offending service: `docker-compose restart web`.
- Avoid running heavy jobs (large mediahost imports, AI report generation) concurrently.
- If chronic, reduce Gunicorn `--workers` (compose `web` command) or Celery
  `--concurrency`, or resize the droplet.

### 3.4 Disk full
```bash
df -h
docker system df
docker system prune -f          # remove stopped containers, dangling images
docker image prune -a -f        # (careful) unused images — forces rebuild next deploy
```
Also check Postgres volume growth and old backup files under the backup dir (§5).

### 3.5 Nginx up but site 502
Means nginx can't reach `web`. Confirm `web` is healthy (§3.2), then
`docker-compose restart nginx`.

---

## 4. Crawler / ingestion failures

Coverage enters through several channels; diagnose by channel.

### 4.1 Mediahost clips import (scheduled pull, every 6 h)
Task `monitor.import_mediahost_clips` runs on Celery Beat. If clips stop appearing:

```bash
# Is the worker/beat alive and did the task run?
docker-compose ps celery_worker celery_beat
docker-compose logs --tail=100 celery_beat | grep -i mediahost
docker-compose logs --tail=200 celery_worker | grep -i import_mediahost

# Run it manually to see the result (last 1 day)
docker-compose exec web python manage.py import_mediahost_clips --days 1

# Inspect the raw API response / schema without writing
docker-compose exec web python manage.py import_mediahost_clips --probe

# Dry run: fetch + map but write nothing
docker-compose exec web python manage.py import_mediahost_clips --days 7 --dry-run
```

Common causes:
- **`celery_worker`/`celery_beat` missing or not running** — historically the server's
  compose file was an older copy *without* these services, so nothing scheduled ran.
  Confirm both are `Up`; if absent, redeploy the repo's `docker-compose.yml`.
- **`MEDIAHOST_API_KEY` blank/invalid** — import is disabled when blank; a bad key gives
  auth errors in the command output. Check `.env`.
- **Clips fetched but "unmapped"** — a clip only lands if its `search` term matches an
  org's `Keyword`. The command prints top unmapped searches; add the missing keyword to
  the relevant organisation.
- **mediahost host slow/down** — the client retries with backoff; a timeout surfaces as
  `MediahostError`. Re-run later; raise `MEDIAHOST_TIMEOUT` if needed.

### 4.2 Media Monitor crawler (webhook push → OnlineArticle)
The sibling crawler POSTs matches to `/api/<org_id>/webhook/media-monitor/`.
```bash
docker-compose ps crawler_web crawler_worker crawler_beat
docker-compose logs --tail=200 crawler_worker
```
- **403 from webhook** — `X-Webhook-Secret` mismatch. Confirm the crawler's secret matches
  the platform's `MEDIA_MONITOR_WEBHOOK_SECRET`.
- **Duplicates ignored** — expected; the receiver dedups by `(organization, url)`.

### 4.3 Article Extractor
```bash
docker-compose ps extractor
docker-compose logs --tail=100 extractor
curl -I https://extractor.sociallight.africa
```

### 4.4 General Celery health
```bash
docker-compose exec celery_worker celery -A socialmonitor inspect ping
docker-compose exec celery_worker celery -A socialmonitor inspect active
docker-compose logs --tail=50 redis        # broker reachable?
```
If the worker can't reach the broker, confirm `redis` is `Up` and
`CELERY_BROKER_URL=redis://redis:6379/0`.

---

## 5. Database backups & restore

PostgreSQL 15 in `sociallight_db` (db `sociallight`, user `sociallight`), data on the
`postgres_data` Docker volume.

### 5.1 Manual backup
```bash
cd /opt/sociallight/social-light-platform
mkdir -p backups
docker-compose exec -T db pg_dump -U sociallight sociallight \
  | gzip > backups/sociallight_$(date +%F_%H%M).sql.gz
ls -lh backups/
```

### 5.2 Scheduled backup (cron on the host)
Add to the deploy user's crontab (`crontab -e`) — daily at 02:30:
```cron
30 2 * * * cd /opt/sociallight/social-light-platform && \
  docker-compose exec -T db pg_dump -U sociallight sociallight | \
  gzip > backups/sociallight_$(date +\%F).sql.gz && \
  find backups/ -name 'sociallight_*.sql.gz' -mtime +14 -delete
```
(Keeps 14 days. Copy backups off-box — e.g. `rsync`/`scp` or DO Spaces — for real DR.)

### 5.3 Restore
> **Destructive — overwrites current data. Take a fresh backup first (§5.1).**
```bash
cd /opt/sociallight/social-light-platform

# 1. Stop app traffic so nothing writes during restore
docker-compose stop web celery_worker celery_beat

# 2. Drop & recreate the schema, then load the dump
gunzip -c backups/sociallight_<DATE>.sql.gz | \
  docker-compose exec -T db psql -U sociallight -d sociallight

# 3. Restart the app and verify
docker-compose start web celery_worker celery_beat
docker-compose exec web python manage.py migrate --check
```
Verify by loading the site and spot-checking recent coverage and report data.

### 5.4 Backup before risky operations
Always run §5.1 before: production releases that include migrations, migration rollbacks,
bulk data fixes (management commands), or a Postgres major-version change.

---

## 6. Routine operations

| Task | Command (run in deploy dir) |
| --- | --- |
| Send due alert digests manually | `docker-compose exec web python manage.py send_daily_alerts --frequency daily` |
| Import clips (last 7 days) | `docker-compose exec web python manage.py import_mediahost_clips --days 7` |
| Tail web logs | `docker-compose logs -f web` |
| Django shell | `docker-compose exec web python manage.py shell` |
| Create admin user | `docker-compose exec web python manage.py createsuperuser` |
| Check pending migrations | `docker-compose exec web python manage.py migrate --check` |

---

## 7. Escalation

- **Anthropic AI report errors** — non-fatal by design; reports omit AI sections. Check
  `ANTHROPIC_API_KEY` and `report_ai.py` logs (`docker-compose logs web | grep -i anthropic`).
- **Email not sending** — run `python manage.py test_email you@example.com`, then check the
  SMTP settings it reports and the worker logs; test alerts send
  synchronously in the web process, scheduled digests via the worker.
- **Data integrity / unknown corruption** — stop writers (§5.3 step 1), take a backup, then
  investigate before resuming.
```
