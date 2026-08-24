from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

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
    "anymail",
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

GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
MAPBOX_ACCESS_TOKEN = os.getenv('MAPBOX_ACCESS_TOKEN', '')

DEFAULT_FROM_EMAIL  = os.getenv('DEFAULT_FROM_EMAIL', 'Social Light <support@sociallightbw.com>')
EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"

ANYMAIL = {
    "RESEND_API_KEY": os.getenv("RESEND_API_KEY"),
}
MEDIA_MONITOR_WEBHOOK_SECRET = os.getenv('MEDIA_MONITOR_WEBHOOK_SECRET', '')
# Distinct from MEDIA_MONITOR_WEBHOOK_SECRET, which already authenticates a
# different (outbound alert) integration — see print_cover_webhook docstring.
PRINT_COVER_WEBHOOK_SECRET = os.getenv('PRINT_COVER_WEBHOOK_SECRET', '')

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

MENTION_RELEVANCY_THRESHOLD = float(os.getenv('MENTION_RELEVANCY_THRESHOLD', '0'))
# TEMPORARY: drop YouTube clips at ingest time (source/link is YouTube). Set
# MEDIAHOST_EXCLUDE_YOUTUBE=0 to re-enable YouTube coverage.
MEDIAHOST_EXCLUDE_YOUTUBE = os.getenv('MEDIAHOST_EXCLUDE_YOUTUBE', '1') not in ('0', 'false', 'False', '')
