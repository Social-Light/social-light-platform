from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()


def _flag(name, default='False'):
    """Read a boolean from the environment. Defined up here because settings
    below this point use it, and Python reads this file top to bottom."""
    return os.getenv(name, default).strip().lower() in ('1', 'true', 'yes', 'on')


BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-social-light-dev-key-change-in-prod')
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '*,localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.humanize',
    'django.contrib.staticfiles',
    'django_celery_beat',
    'django_celery_results',
    'crispy_forms',
    'crispy_bootstrap5',
    'widget_tweaks',
    'anymail',
    'monitor',
]

CRISPY_ALLOWED_TEMPLATE_PACKS = 'bootstrap5'
CRISPY_TEMPLATE_PACK = 'bootstrap5'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serves STATIC_ROOT directly from the app process, so static assets work
    # even when this app is hit directly (e.g. port 8000) and not just through
    # nginx's /static/ alias.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Scopes non-admin users to their own organisation and enforces the
    # free-trial paywall. Must sit after AuthenticationMiddleware (needs
    # request.user).
    'monitor.middleware.OrganizationAccessMiddleware',
    # Records the campaign a visitor arrived on so the lead they eventually
    # become can be traced back to it. Must sit after SessionMiddleware.
    'monitor.middleware.AttributionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'socialmonitor.urls'

CSRF_TRUSTED_ORIGINS = [
    "https://sociallight.africa",
    "https://www.sociallight.africa",
]
# Extra origins from the environment, comma separated, each with its scheme —
# "https://abc123.ngrok-free.app". Needed whenever the site is reached over a
# host that is not the live domain: a tunnel used to test the payment gateway
# (DPO's firewall rejects loopback return URLs, so the checkout cannot be
# exercised on localhost at all), a staging deployment, or a preview host.
# Appended rather than replacing, so the live domain can never be configured away.
CSRF_TRUSTED_ORIGINS += [
    origin.strip() for origin in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',')
    if origin.strip()
]

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'monitor.context_processors.all_orgs',
                'monitor.context_processors.external_links',
                'monitor.context_processors.entitlements',
                'monitor.context_processors.onboarding',
                'monitor.context_processors.marketing',
            ],
        },
    },
]

WSGI_APPLICATION = 'socialmonitor.wsgi.application'


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "sociallight"),
        "USER": os.getenv("POSTGRES_USER", "sociallight"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "sociallight_password"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),},
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 6}},
]

AUTH_USER_MODEL = 'monitor.User'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/app/organizations/'
LOGOUT_REDIRECT_URL = '/login/'

STATIC_URL = "/static/"
STATIC_ROOT = "/app/staticfiles"

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Gaborone'
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')  # monitor/sentiment_ai.py
# Additional Groq accounts' keys — monitor/sentiment_ai.py's analyze_sentiment()
# and monitor/date_ai.py's extract_published_date() fall back through these in
# order when a request fails (in practice: each account's own free-tier
# 200,000 tokens/day cap, which report_ai.py/sector_ai.py/issue_report_ai.py
# also draw against — GROQ_API_KEY specifically, not the _2/_3/_4 accounts).
# All optional; leave blank to run on fewer accounts.
GROQ_API_KEY_2 = os.getenv('GROQ_API_KEY_2', '')
GROQ_API_KEY_3 = os.getenv('GROQ_API_KEY_3', '')
GROQ_API_KEY_4 = os.getenv('GROQ_API_KEY_4', '')
GROQ_API_KEY_5 = os.getenv('GROQ_API_KEY_5', '')
GROQ_API_KEY_6 = os.getenv('GROQ_API_KEY_6', '')
GROQ_API_KEY_7 = os.getenv('GROQ_API_KEY_7', '')
GROQ_API_KEY_8 = os.getenv('GROQ_API_KEY_8', '')
GROQ_API_KEY_9 = os.getenv('GROQ_API_KEY_9', '')

# Dedicated to monitor/date_ai.py's extract_published_date() ONLY — never
# read by sentiment_ai.py (see that module's _groq_api_keys() vs. date_ai.py's
# own), so re-dating social posts to their real publish date never competes
# with sentiment analysis for quota, and vice versa.
GROQ_API_KEY_DATE_1 = os.getenv('GROQ_API_KEY_DATE_1', '')
GROQ_API_KEY_DATE_2 = os.getenv('GROQ_API_KEY_DATE_2', '')

# Dedicated to monitor/report_ai.py's generate_analysis() and
# monitor/issue_report_ai.py's generate_issue_report() ONLY — appended after
# the shared pool above by report_ai._groq_api_keys(), so it's spent only as
# a last resort once every shared key is exhausted for the day, rather than
# competing with sentiment_ai.py's continuous live traffic for quota.
GROQ_API_KEY_REPORTS = os.getenv('GROQ_API_KEY_REPORTS', '')
# Self-hosted metasearch (same instance media-monitor's discovery/services/
# search_api.py uses) — sector_ai.py's free fallback when Anthropic is
# unavailable. Reachable on the compose network; see docker-compose.yml.
SEARXNG_URL = os.getenv('SEARXNG_URL', 'http://searxng:8080')
MAPBOX_ACCESS_TOKEN = os.getenv('MAPBOX_ACCESS_TOKEN', '')

# ── Email ──────────────────────────────────────────────────────
# 2026-09-03: sociallightbw.com is not a verified sending domain in Resend
# (only sociallight.africa is — see resend.com/domains) — sending from it 403s
# on every attempt, so DEFAULT_FROM_EMAIL's fallback below matches the verified
# domain. That was the only real bug; it is fixed regardless of transport.
#
# 2026-09-04: this host cannot reach outbound SMTP at all — ports 587/465 to
# smtp.resend.com time out (confirmed: a raw socket connect hangs the full
# EMAIL_TIMEOUT and never completes), while HTTPS/443 is wide open. The
# payments/onboarding merge (origin/main PR #36) had defaulted this deployment
# onto plain SMTP, which is what turned a pre-existing, unrelated firewall fact
# into a broken signup — every registration hung for EMAIL_TIMEOUT seconds
# before the mail attempt gave up. Back on the vendor HTTP-API backend
# (`anymail`, reaching Resend over 443) for that reason. Which mail path is
# used is still a .env decision, never a code one — set EMAIL_BACKEND to
# whichever of the options below the deployment can actually reach:
#
#   1. Resend's HTTP API — works anywhere plain HTTPS does, including here
#        EMAIL_BACKEND=anymail.backends.resend.EmailBackend
#        RESEND_API_KEY=...
#
#   2. Any SMTP provider — a hosted mail service or your own mail server
#        EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
#        EMAIL_HOST=smtp.your-provider.example
#        EMAIL_PORT=587
#        EMAIL_USE_TLS=True          ← port 587; use EMAIL_USE_SSL on port 465
#        EMAIL_HOST_USER=...  EMAIL_HOST_PASSWORD=...
#
#   3. The console, for development — the whole message, link included, is
#      printed to the terminal and nothing is sent anywhere
#        EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
#
# Run `manage.py test_email you@example.com` to check what is live and whether
# it actually delivers.
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Social Light <support@sociallight.africa>')
SERVER_EMAIL = os.getenv('SERVER_EMAIL', DEFAULT_FROM_EMAIL)
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')

# Everything the SMTP backend needs, all read from the environment so that moving
# between mail providers is a .env change and never a code change. No value here
# is provider-specific, and EMAIL_HOST_PASSWORD is never logged or rendered.
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = _flag('EMAIL_USE_TLS', 'True')
EMAIL_USE_SSL = _flag('EMAIL_USE_SSL', 'False')
EMAIL_TIMEOUT = int(os.getenv('EMAIL_TIMEOUT', '20'))
# Django refuses to build a connection with both set; catching it here names the
# two variables instead of raising deep inside the backend on the first send.
if EMAIL_USE_TLS and EMAIL_USE_SSL:
    EMAIL_USE_SSL = False

# What the anymail backend needs when EMAIL_BACKEND selects it (option 1 above).
# Harmless and unused otherwise — anymail is always installed (INSTALLED_APPS),
# but nothing reads ANYMAIL unless EMAIL_BACKEND actually points at it.
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
ANYMAIL = {
    'RESEND_API_KEY': RESEND_API_KEY,
}

MEDIA_MONITOR_WEBHOOK_SECRET = os.getenv('MEDIA_MONITOR_WEBHOOK_SECRET', '')
# Distinct from MEDIA_MONITOR_WEBHOOK_SECRET, which already authenticates a
# different (outbound alert) integration — see print_cover_webhook docstring.
PRINT_COVER_WEBHOOK_SECRET = os.getenv('PRINT_COVER_WEBHOOK_SECRET', '')

ARTICLE_EXTRACTOR_URL = os.getenv('ARTICLE_EXTRACTOR_URL', 'https://extractor.sociallight.africa/')
# Signs the short-lived, single-use handoff token that lets a logged-in org
# member land signed-in on the extractor with no separate account there — see
# monitor/extractor_sso.py. Deliberately its own secret, not either app's
# SECRET_KEY: rotating one app's SECRET_KEY must not silently break the other
# side's handoff. Must be identical in both apps' .env, same as
# PRINT_COVER_WEBHOOK_SECRET above.
EXTRACTOR_SSO_SECRET = os.getenv('EXTRACTOR_SSO_SECRET', '')
# Verifies a "My Extracts" push from the extractor (monitor/views.py:
# extractor_push_webhook) — must be identical to that app's own
# EXTRACTOR_PUSH_WEBHOOK_SECRET. Distinct from EXTRACTOR_SSO_SECRET (that one
# proves a login came from here; this one proves an article came from there).
EXTRACTOR_PUSH_WEBHOOK_SECRET = os.getenv('EXTRACTOR_PUSH_WEBHOOK_SECRET', '')

# ── mediahost clips API ───────────────────────────────────────────────────────
# Single global API key (x-api-key header). Imported clips route to organisations
# by matching the clip's `search` term against each org's Keyword entries.
MEDIAHOST_API_URL = os.getenv('MEDIAHOST_API_URL', 'http://mh-api.mediahost.co.za')
MEDIAHOST_API_KEY = os.getenv('MEDIAHOST_API_KEY', '')
MEDIAHOST_TIMEOUT = int(os.getenv('MEDIAHOST_TIMEOUT', '120'))  # per-request read timeout (s)


SITE_URL = os.getenv('SITE_URL', 'https://sociallight.africa')

# ── Marketing measurement ────────────────────────────────────────────────────
# Campaign attribution (monitor/attribution.py) is always on and needs no
# configuration: it is our own session, our own form, our own database.
#
# The Meta Pixel is opt-in and off until an id is set here. It is the half that
# writes cookies to a visitor's device, so it also waits on the cookie banner —
# an id alone does not make it fire.
META_PIXEL_ID = os.getenv('META_PIXEL_ID', '')
# The Conversions API reports the same events from the server, which is what
# recovers the conversions ad blockers and iOS would otherwise lose. Generate
# the token in Events Manager → Settings → Conversions API. It is a credential:
# keep it in the environment, never in the repository.
META_CAPI_ACCESS_TOKEN = os.getenv('META_CAPI_ACCESS_TOKEN', '')
# Set while testing so events land in Events Manager's test tool instead of the
# ad account's real numbers. Must be empty in production.
META_CAPI_TEST_EVENT_CODE = os.getenv('META_CAPI_TEST_EVENT_CODE', '')
# Server-side events carry no cookies, only what a visitor typed into our own
# form, so by default they are not gated on the banner. Set True for the
# stricter reading — and set it True before doing business in the EU.
META_CAPI_REQUIRE_CONSENT = _flag('META_CAPI_REQUIRE_CONSENT', 'False')

# Google Analytics 4, same arrangement: off until a measurement id is set, and
# gated on the same banner.
GA4_MEASUREMENT_ID = os.getenv('GA4_MEASUREMENT_ID', '')

# Meta requires the domain to be verified before an ad account may set event
# priorities, which is what makes iOS conversions attribute at all. Verification
# is a code Meta issues in Business Settings → Brand safety → Domains; paste it
# here and it is rendered into the <head> of every public page. Unrelated to the
# pixel and to consent: it is a proof of ownership, not a tracker, so it renders
# whether or not anyone accepted the banner.
META_DOMAIN_VERIFICATION = os.getenv('META_DOMAIN_VERIFICATION', '')

# The image shown when a page is shared or run as an ad. Meta wants 1200x630.
OG_DEFAULT_IMAGE = os.getenv('OG_DEFAULT_IMAGE', 'images/design/hero-image.jpg')

# ── Free trial & billing ─────────────────────────────────────────────────────
# Length of the self-service free trial started from the public signup page.
TRIAL_PERIOD_DAYS = int(os.getenv('TRIAL_PERIOD_DAYS', '14'))
# Where package requests raised from the paywall are emailed. Comma-separated.
SALES_NOTIFICATION_EMAILS = [
    e.strip() for e in os.getenv('SALES_NOTIFICATION_EMAILS', 'sales@sociallight.africa').split(',') if e.strip()
]
# Where new trial signups are emailed as they happen. Comma-separated; empty
# disables the notification entirely (see _notify_new_signup).
SIGNUP_NOTIFICATION_EMAILS = [
    e.strip() for e in os.getenv('SIGNUP_NOTIFICATION_EMAILS', 'support@sociallight.africa').split(',') if e.strip()
]

# ── Onboarding & email verification ──────────────────────────────────────────
# How long a verification link stays valid. A user whose link has expired can ask
# for a new one from the verify step.
EMAIL_VERIFICATION_TTL_HOURS = int(os.getenv('EMAIL_VERIFICATION_TTL_HOURS', '48'))

# ── Entitlements ─────────────────────────────────────────────────────────────
# What an account with no paid package gets. Leave unset to use the defaults in
# monitor/entitlements.py (basic monitoring, dashboard, alerts). Set to a
# comma-separated list of feature codes to widen or narrow the free tier without
# a release. TRIAL_ENTITLEMENTS does the same for the free trial; unset means the
# trial is monitoring only (basic monitoring, dashboard) — the 14-day clock is
# the trial's limit, not an unrestricted feature set.
_free_entitlements = os.getenv('FREE_PLAN_ENTITLEMENTS', '')
if _free_entitlements:
    FREE_PLAN_ENTITLEMENTS = [c.strip() for c in _free_entitlements.split(',') if c.strip()]
_trial_entitlements = os.getenv('TRIAL_ENTITLEMENTS', '')
if _trial_entitlements:
    TRIAL_ENTITLEMENTS = [c.strip() for c in _trial_entitlements.split(',') if c.strip()]

# ── Payments ─────────────────────────────────────────────────────────────────
# The master switch. Off by default, which is the platform's existing behaviour:
# fees are settled off-platform by invoice/EFT and a platform admin activates the
# package. With it off, onboarding still completes, plans are still assignable
# (by the user and by an admin) and entitlements still apply — the only thing
# that does not happen is card capture. Nothing in the application requires a
# gateway to be configured.
PAYMENTS_ENABLED = _flag('PAYMENTS_ENABLED', 'False')
# Which gateway, when payments are on: 'manual', 'stripe', 'dpo', or a dotted path
# to a monitor.payments.base.PaymentProvider subclass.
PAYMENT_PROVIDER = os.getenv('PAYMENT_PROVIDER', 'manual')

# ── DPO Pay ──────────────────────────────────────────────────────────────────
# DPO is a hosted-redirect gateway: the customer pays on DPO's own page and we
# confirm the outcome server-side with verifyToken. No card data reaches us and
# there is nothing to store, so there is no publishable/secret key pair here —
# the company token is the whole credential and is secret.
#
# DPO runs no separate sandbox host. Test and live integrations hit the same
# endpoint and are told apart ONLY by which company token is configured, so this
# value must never be committed and a staging deployment must be checked against
# the token it actually has.
DPO_COMPANY_TOKEN = os.getenv('DPO_COMPANY_TOKEN', '')
# Issued by DPO alongside the company token; the two are a matched pair.
DPO_SERVICE_TYPE = os.getenv('DPO_SERVICE_TYPE', '')
DPO_ENDPOINT = os.getenv('DPO_ENDPOINT', 'https://secure.3gdirectpay.com/API/v6/')
# DPO's own documentation gives three different hosted-checkout paths (payv3.php
# in the integration email, payv2.php in the createToken reference, pay.asp in
# the hosted-page guide). Overridable so a correction does not need a release.
DPO_PAYMENT_URL = os.getenv('DPO_PAYMENT_URL', 'https://secure.3gdirectpay.com/payv3.php')
# Hours a customer has to finish paying before DPO expires the token. DPO's own
# default is 96, which is far longer than a subscription checkout should stay open.
DPO_PAYMENT_TIME_LIMIT_HOURS = int(os.getenv('DPO_PAYMENT_TIME_LIMIT_HOURS', '2'))
DPO_TIMEOUT_SECONDS = int(os.getenv('DPO_TIMEOUT_SECONDS', '30'))

# Public base URL the gateway sends customers back to, e.g.
# "https://sociallight.africa" or an ngrok URL in development. No trailing slash.
#
# Leave blank in production behind a correctly configured proxy: the return URL
# is then built from the request's own host, which is right. Set it whenever that
# host cannot be trusted — a tunnel that rewrites Host, or local development,
# where the request yields a 127.0.0.1 address. DPO rejects a loopback return URL
# with a 403 on the whole createToken call, so an unset value on a developer
# machine looks like a broken gateway rather than a misconfiguration.
PAYMENT_RETURN_BASE_URL = os.getenv('PAYMENT_RETURN_BASE_URL', '').rstrip('/')

# Card data never reaches this application: the browser posts it to the
# provider's hosted field and we store only the token that comes back. The secret
# key is used server-side to exchange and charge that token.
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY', '')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')

# The business rule: funds from a payment become eligible for extraction only
# after this many days. Stamped onto each Payment when it is taken, so changing
# this affects future payments only. It describes OUR holding period — the
# payment provider's own settlement timetable is separate and is not controlled
# by this application.
PAYMENT_SETTLEMENT_HOLD_DAYS = int(os.getenv('PAYMENT_SETTLEMENT_HOLD_DAYS', '7'))

# Base URL the headless-Chromium PDF renderer uses to reach the app for reports
# that must load live (charts + static assets), e.g. Competitor Analysis. Set to
# the in-container address (http://127.0.0.1:8000) to avoid a public round-trip.
# Leave blank to use the request's own absolute URL.
PDF_RENDER_BASE_URL = os.getenv('PDF_RENDER_BASE_URL', '')

# ── Celery ──────────────────────────────────────────────────────────────────
from celery.schedules import crontab

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'django-db')
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = False
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
CELERY_TASK_TIME_LIMIT = 600

# Scheduled runs of the alert digest command. Beat reads these on startup and,
# with the DatabaseScheduler, syncs them into the django-celery-beat tables.
CELERY_BEAT_SCHEDULE = {
    'send-daily-digests': {
        'task': 'monitor.send_alerts',
        # Ticks every 15 min; daily_alert_due() (alert_email.py) gates the
        # actual send to the two fixed slots, 08:00 + 15:00 Africa/Gaborone.
        'schedule': crontab(minute='*/15'),
        'kwargs': {'frequency': 'daily'},
    },
    'send-immediate-alerts': {
        'task': 'monitor.send_alerts',
        'schedule': crontab(minute='*/15'),             # every 15 min, picks up new records
        'kwargs': {'frequency': 'immediate'},
    },
    'update-sector-intelligence': {
        'task': 'monitor.update_sector_intelligence',
        'schedule': crontab(hour=5, minute=12),          # once daily, before business hours
    },
    'analyze-sentiment': {
        'task': 'monitor.analyze_sentiment',
        'schedule': crontab(minute='*/30'),              # every 30 min, picks up newly-ingested mentions
    },
    'analyze-relevancy': {
        'task': 'monitor.analyze_relevancy',
        # Every 30 min like analyze-sentiment, offset by 15 min so the two
        # don't both fire in the same minute and double up on the same
        # shared Groq key pool at once.
        'schedule': crontab(minute='15,45'),
    },
    # Pulls Tony's personal Apify Facebook-posts schedule (FNBB + BPC's own
    # pages) into SocialMediaPost. The Apify actor itself runs @daily and its
    # last run typically finishes ~02:40 UTC; 05:00 UTC gives it comfortable
    # room to finish before this pulls the dataset. Added 2026-09-11 — this
    # command had run manually-only since 2026-09-04, and un-noticed silence
    # after 2026-09-08 lost several days of owned Facebook coverage. See
    # monitor/management/commands/pull_facebook_schedule.py for the full story.
    'pull-facebook-schedule': {
        'task': 'monitor.pull_facebook_schedule',
        'schedule': crontab(hour=5, minute=0),
    },
    # Charges saved cards for subscriptions whose paid period has run out.
    # Hourly rather than daily so a renewal lands close to the moment it falls
    # due; access is not waiting on it either way, because a lapsed period is
    # computed on read and already withholds access the moment it passes.
    'renew-subscriptions': {
        'task': 'monitor.renew_subscriptions',
        'schedule': crontab(minute=20),
    },
}

# deployment doesn't log a failing task. Clips route to orgs by keyword match.
if MEDIAHOST_API_KEY:
    CELERY_BEAT_SCHEDULE['import-mediahost-clips'] = {
        'task': 'monitor.import_mediahost_clips',
        'schedule': crontab(hour='*/6', minute=15),     # every 6 hours
        'kwargs': {'days': 1},
    }

# ── Session, cookie and transport security ───────────────────────────────────
# The secure-cookie and HSTS settings are tied to DEBUG so local development over
# http keeps working, while a deployment with DEBUG=False gets them on by
# default. Each can still be forced explicitly with its own environment variable.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_HTTPONLY = False        # the front end reads the CSRF token for fetch()
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = _flag('SESSION_COOKIE_SECURE', 'False' if DEBUG else 'True')
CSRF_COOKIE_SECURE = _flag('CSRF_COOKIE_SECURE', 'False' if DEBUG else 'True')
# Sessions last two weeks and are refreshed on each request, so an idle session
# expires rather than living forever.
SESSION_COOKIE_AGE = int(os.getenv('SESSION_COOKIE_AGE', str(60 * 60 * 24 * 14)))
SESSION_SAVE_EVERY_REQUEST = True

# A CSRF rejection tells the user to reload rather than showing a bare 403, and
# logs what actually arrived. Django reports two different failures with the same
# "CSRF token missing" text - a form with no token, and a request body it could
# not read - and that view separates them.
CSRF_FAILURE_VIEW = 'monitor.csrf.csrf_failure'

X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
if not DEBUG:
    SECURE_SSL_REDIRECT = _flag('SECURE_SSL_REDIRECT', 'True')
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

MENTION_RELEVANCY_THRESHOLD = float(os.getenv('MENTION_RELEVANCY_THRESHOLD', '0'))
# 2026-09-01, at Tony's request: analyze_sentiment (management command +
# Celery Beat task) previously had no date scope at all — every scheduled
# run chipped away at the ENTIRE historical backlog of unanalysed mentions
# (across all orgs/history), competing with report_ai.py/live traffic for
# the same shared Groq daily-token pool (see report_ai.py's module
# docstring). Setting this stops the backlog dead: only mentions published
# on/after this date get AI sentiment going forward; older unanalysed rows
# are left as-is permanently unless a deliberate one-off backfill is run
# with --include-backlog. ISO date (YYYY-MM-DD); blank disables the cutoff
# (restores the old unscoped behaviour).
SENTIMENT_AI_CUTOFF_DATE = os.getenv('SENTIMENT_AI_CUTOFF_DATE', '')
# Same guard, same reasoning, for analyze_relevancy (monitor/relevancy_ai.py)
# — it shares this same Groq daily-token pool. See that command's module
# docstring. ISO date (YYYY-MM-DD); blank disables the cutoff.
RELEVANCY_AI_CUTOFF_DATE = os.getenv('RELEVANCY_AI_CUTOFF_DATE', '')
# TEMPORARY: drop YouTube clips at ingest time (source/link is YouTube). Set
# MEDIAHOST_EXCLUDE_YOUTUBE=0 to re-enable YouTube coverage.
MEDIAHOST_EXCLUDE_YOUTUBE = os.getenv('MEDIAHOST_EXCLUDE_YOUTUBE', '1') not in ('0', 'false', 'False', '')

# Without this, the application's own log records (email send failures, CSRF
# diagnostics, verification issues) go to the root logger, which has no handler -
# so the messages the code takes care to emit are never seen.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {'format': '[{asctime}] {levelname} {name}: {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'},
    },
    'loggers': {
        'monitor': {
            'handlers': ['console'],
            'level': os.getenv('MONITOR_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'django.security.csrf': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}
