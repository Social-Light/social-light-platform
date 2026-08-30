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
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'socialmonitor.urls'

CSRF_TRUSTED_ORIGINS = [
    "https://sociallight.africa",
    "https://www.sociallight.africa",
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
MAPBOX_ACCESS_TOKEN = os.getenv('MAPBOX_ACCESS_TOKEN', '')

# ── Email ──────────────────────────────────────────────────────
# Onboarding depends on outbound email: a new account cannot reach the product
# until it follows a verification link. Delivery goes over SMTP, and which mail
# server is used is a .env question rather than a code one — nothing here names a
# provider. Run `manage.py test_email you@example.com` to check what is live and
# whether it actually delivers.
#
#   1. Any SMTP provider — a hosted mail service or your own mail server
#        EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
#        EMAIL_HOST=smtp.your-provider.example
#        EMAIL_PORT=587
#        EMAIL_USE_TLS=True          ← port 587; use EMAIL_USE_SSL on port 465
#        EMAIL_HOST_USER=...  EMAIL_HOST_PASSWORD=...
#
#   2. The console, for development — the whole message, link included, is
#      printed to the terminal and nothing is sent anywhere
#        EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Social Light <noreply@sociallight.africa>')
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

MEDIA_MONITOR_WEBHOOK_SECRET = os.getenv('MEDIA_MONITOR_WEBHOOK_SECRET', '')

ARTICLE_EXTRACTOR_URL = os.getenv('ARTICLE_EXTRACTOR_URL', 'https://extractor.sociallight.africa/')

# ── mediahost clips API ───────────────────────────────────────────────────────
# Single global API key (x-api-key header). Imported clips route to organisations
# by matching the clip's `search` term against each org's Keyword entries.
MEDIAHOST_API_URL = os.getenv('MEDIAHOST_API_URL', 'http://mh-api.mediahost.co.za')
MEDIAHOST_API_KEY = os.getenv('MEDIAHOST_API_KEY', '')
MEDIAHOST_TIMEOUT = int(os.getenv('MEDIAHOST_TIMEOUT', '120'))  # per-request read timeout (s)


SITE_URL = os.getenv('SITE_URL', 'https://sociallight.africa')

# ── Free trial & billing ─────────────────────────────────────────────────────
# Length of the self-service free trial started from the public signup page.
TRIAL_PERIOD_DAYS = int(os.getenv('TRIAL_PERIOD_DAYS', '14'))
# Where package requests raised from the paywall are emailed. Comma-separated.
SALES_NOTIFICATION_EMAILS = [
    e.strip() for e in os.getenv('SALES_NOTIFICATION_EMAILS', 'sales@sociallight.africa').split(',') if e.strip()
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
# trial unlocks everything, which is how it has always behaved.
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
# Which gateway, when payments are on: 'manual', 'stripe', or a dotted path to a
# monitor.payments.base.PaymentProvider subclass.
PAYMENT_PROVIDER = os.getenv('PAYMENT_PROVIDER', 'manual')

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
        'schedule': crontab(minute='*/15'),          # 08:00 Africa/Gaborone, daily
        'kwargs': {'frequency': 'daily'},
    },
    'send-immediate-alerts': {
        'task': 'monitor.send_alerts',
        'schedule': crontab(minute='*/15'),             # every 15 min, picks up new records
        'kwargs': {'frequency': 'immediate'},
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
