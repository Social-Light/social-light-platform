import logging

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger(__name__)


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


@shared_task(name='monitor.renew_subscriptions')
def renew_subscriptions(grace_hours=0):
    """Charge saved cards for subscriptions whose paid period has run out.

    Access does not depend on this task running. ``effective_plan_status``
    already treats a lapsed period as 'past_due' the moment it passes, so an
    organisation that has not paid loses access on time even if the worker is
    down. What this task does is take the money and put the period back — the
    optimistic path — and record a failure when it cannot.

    Every renewal is attempted independently: one organisation's declined card
    must not stop the rest of the run.
    """
    from datetime import timedelta

    from django.utils import timezone

    from .models import Organization
    from .payments import get_provider, payments_enabled

    if not payments_enabled():
        return 'payments are disabled; nothing to renew'

    provider = get_provider()
    charge = getattr(provider, 'charge_recurring', None)
    if charge is None or not provider.is_enabled:
        return f'provider {provider.key} cannot charge saved cards; nothing to renew'

    cutoff = timezone.now() - timedelta(hours=grace_hours)
    due = (Organization.objects
           .filter(plan_status='active', current_period_end__lte=cutoff)
           .exclude(package__isnull=True)
           .select_related('package'))

    renewed = failed = skipped = 0
    for org in due:
        method = provider.recurring_method_for(org)
        if method is None:
            # Nothing saved to charge. Leave it to lapse and let the customer
            # pay again through checkout rather than pretending to bill them.
            _mark_past_due(org, 'No saved card to renew against.')
            skipped += 1
            continue

        try:
            result = charge(org, method, org.package.price, org.package.currency,
                            package=org.package)
        except Exception as exc:                 # noqa: BLE001 - one org must not stop the run
            logger.warning('Renewal errored for org %s: %s', org.id, exc)
            _mark_past_due(org, str(exc))
            failed += 1
            continue

        if result.succeeded:
            # extend=True runs the new period from the end of the old one, so a
            # renewal charged early does not shorten what the customer paid for.
            org.activate_package(org.package, extend=True)
            renewed += 1
        else:
            logger.info('Renewal declined for org %s: %s', org.id, result.failure_reason)
            _mark_past_due(org, result.failure_reason)
            failed += 1

    return f'renew_subscriptions: {renewed} renewed, {failed} failed, {skipped} without a saved card'


def _mark_past_due(org, reason=''):
    """Record that a paid subscription did not renew.

    Only ever moves an 'active' organisation, so a run that overlaps with a
    customer paying again by hand cannot undo their payment.
    """
    if org.plan_status != 'active':
        return
    org.plan_status = 'past_due'
    org.save(update_fields=['plan_status'])
    logger.info('Organisation %s marked past_due: %s', org.id, reason or 'renewal failed')
