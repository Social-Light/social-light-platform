from celery import shared_task
from django.core.management import call_command


@shared_task(name='monitor.send_alerts')
def send_alerts(frequency='daily', org=None):
    """
    Run the send_daily_alerts management command for the given alert frequency.

    Scheduled by Celery Beat (see CELERY_BEAT_SCHEDULE in settings), but can also
    be triggered ad-hoc: send_alerts.delay('immediate').
    """
    kwargs = {'frequency': frequency}
    if org:
        kwargs['org'] = org
    call_command('send_daily_alerts', **kwargs)
    return f'send_daily_alerts ran for frequency={frequency}'


@shared_task(name='monitor.import_mediahost_clips')
def import_mediahost_clips(days=1, media_type=None, org=None):
    """
    Pull allocated clips from the mediahost API for the last `days` days and
    import them into the media coverage models.

    Scheduled by Celery Beat (see CELERY_BEAT_SCHEDULE), but can also be
    triggered ad-hoc: import_mediahost_clips.delay(days=7).
    """
    kwargs = {'days': days}
    if media_type:
        kwargs['media_type'] = media_type
    if org:
        kwargs['org'] = org
    call_command('import_mediahost_clips', **kwargs)
    return f'import_mediahost_clips ran for days={days}'


@shared_task(name='monitor.update_sector_intelligence')
def update_sector_intelligence():
    """
    Refresh the landing page's public sector stories and commodity quotes via
    a live AI web search (see monitor/sector_ai.py). Only touches
    is_ai_generated=True rows — an editor's own hand-written content is never
    overwritten.

    Scheduled once daily by Celery Beat (see CELERY_BEAT_SCHEDULE), but can
    also be triggered ad-hoc: update_sector_intelligence.delay().
    """
    call_command('update_sector_intelligence')
    return 'update_sector_intelligence ran'


@shared_task(name='monitor.analyze_sentiment')
def analyze_sentiment_task(limit=300):
    """
    Re-score sentiment on newly-ingested mentions via a real contextual AI
    read (Groq — see monitor/sentiment_ai.py), replacing crawler VADER /
    vendor-tagged / manually-typed sentiment with one that actually reasons
    about the text from the monitored org's own perspective.

    Only ever touches rows with a blank sentiment_rationale (i.e. not yet
    AI-analysed) — see the analyze_sentiment management command — so each
    scheduled run only costs API calls on what's genuinely new since the
    last run. `limit` bounds one run's duration/cost; raise it (or pass
    --force via call_command kwargs) for a one-off larger backfill instead
    of waiting for many scheduled runs to work through a big backlog.

    Scheduled every 30 min by Celery Beat (see CELERY_BEAT_SCHEDULE), but can
    also be triggered ad-hoc: analyze_sentiment_task.delay(limit=1000).
    """
    call_command('analyze_sentiment', limit=limit)
    return f'analyze_sentiment ran (limit={limit})'
