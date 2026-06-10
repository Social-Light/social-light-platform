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
