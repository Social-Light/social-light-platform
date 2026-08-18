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
