"""
monitor/management/commands/send_daily_alerts.py

Send the daily media digest email to every active Alert that has
frequency='daily' and at least one recipient. Reports records that came
through today (by created_at), ordered with the org's own country first.
The alert's banner image (if set) is embedded inline and links to login.

Usage:
    python manage.py send_daily_alerts                       # all active daily alerts
    python manage.py send_daily_alerts --frequency immediate # immediate alerts (new records only)
    python manage.py send_daily_alerts --frequency all       # ignore frequency
    python manage.py send_daily_alerts --org <uuid>          # one org only
    python manage.py send_daily_alerts --alert <id> --test   # force-send one alert (a test)
    python manage.py send_daily_alerts --dry-run             # print without sending

Schedule via Celery Beat (see CELERY_BEAT_SCHEDULE) or cron.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import Alert
from monitor.alert_email import (
    build_and_send, gather, start_of_today, daily_alert_due,
    MAX_PUBLISH_AGE_DAYS, DEFAULT_MAX_PUBLISH_AGE_DAYS,
)


class Command(BaseCommand):
    help = "Send media digest emails for active alerts"

    def add_arguments(self, parser):
        parser.add_argument('--org', type=str, help='Limit to a single org UUID')
        parser.add_argument('--alert', type=str, help='Limit to a single alert id (ignores frequency/active filters)')
        parser.add_argument('--test', action='store_true', help='Force-send even with no new records; do not advance the watermark')
        parser.add_argument('--dry-run', action='store_true', help='Print emails without sending')
        parser.add_argument(
            '--frequency', type=str, default='daily',
            help="Which alert frequency to send: daily (default), immediate, weekly, monthly, or 'all'.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        test = options['test']
        org_filter = options.get('org')
        alert_id = options.get('alert')
        frequency = (options.get('frequency') or 'daily').lower()

        if alert_id:
            qs = Alert.objects.filter(id=alert_id).select_related('organization')
        else:
            # Disabled (inactive) organisations receive no scheduled alerts.
            qs = Alert.objects.filter(
                is_active=True, organization__status='active'
            ).select_related('organization')
            if frequency != 'all':
                qs = qs.filter(frequency=frequency)
            if org_filter:
                qs = qs.filter(organization__id=org_filter)

        # Skip alerts with no recipients at all.
        alerts = [a for a in qs if a.recipient_list()]

        # Daily alerts fire at each alert's own delivery_time (default 08:00). The
        # beat job runs every ~15 min; only send the ones due now. A forced single
        # --alert run or --test bypasses this so you can always send on demand.
        if frequency == 'daily' and not alert_id and not test:
            now = timezone.localtime()
            alerts = [a for a in alerts if daily_alert_due(a, now)]

        if not alerts:
            self.stdout.write('No qualifying alerts found.')
            return

        sent = 0
        for alert in alerts:
            org = alert.organization
            recipients = alert.recipient_list()
            # A test send ignores the watermark and reports the full day.
            since = start_of_today() if test else (alert.last_sent_at or start_of_today())

            if dry_run:
                max_age = MAX_PUBLISH_AGE_DAYS.get(alert.frequency, DEFAULT_MAX_PUBLISH_AGE_DAYS)
                online, print_arts, social, broadcast = gather(org, since, max_publish_age_days=max_age)
                total = len(online) + len(print_arts) + len(social) + len(broadcast)
                if not test and alert.frequency == 'immediate' and total == 0:
                    continue
                self.stdout.write(f"\n{'='*60}")
                self.stdout.write(f"TO:      {', '.join(recipients)}")
                self.stdout.write(f"SUBJECT: {alert.email_subject or 'Social Light: ' + org.name + ' Daily Media Update'}")
                self.stdout.write(f"Online: {len(online)}  Print: {len(print_arts)}  Social: {len(social)}  Broadcast: {len(broadcast)}")
                sent += 1
                continue

            try:
                result = build_and_send(
                    alert,
                    since=since,
                    force=test,
                    update_watermark=not test,
                )
            except Exception as exc:
                self.stderr.write(f"Failed for {', '.join(recipients)} ({org.name}): {exc}")
                continue

            if result.get('sent'):
                self.stdout.write(f"Sent to {', '.join(recipients)} — {org.name}")
                sent += 1
            else:
                self.stdout.write(f"Skipped {org.name}: {result.get('reason')}")

        self.stdout.write(f"\n{'Dry-run' if dry_run else 'Sent'}: {sent} email(s)")
