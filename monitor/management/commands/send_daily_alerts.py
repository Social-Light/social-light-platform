"""
monitor/management/commands/send_daily_alerts.py

Send the daily media digest email to every active Alert that has
frequency='daily' and a recipient email address.

Usage:
    python manage.py send_daily_alerts           # all qualifying alerts
    python manage.py send_daily_alerts --org <uuid>   # one org only
    python manage.py send_daily_alerts --dry-run      # print without sending

Schedule via cron (08:00 daily):
    0 8 * * * /path/to/venv/bin/python /path/to/manage.py send_daily_alerts
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

from monitor.models import Alert, Organization, OnlineArticle, PrintArticle, SocialMediaPost, BroadcastMention


def _country_sort_key(obj, org_country):
    """Items from the org's country sort to the front (key=0), others to back (key=1)."""
    country = (getattr(obj, 'country', '') or '').strip().lower()
    return 0 if country == org_country.strip().lower() else 1


def _gather(org, since):
    """
    Return (online, print, social, broadcast) querysets for today,
    each sorted with the org's country first.
    """
    oc = org.country or ''

    online = sorted(
        org.online_articles.filter(date_published__gte=since).order_by('-date_published')[:50],
        key=lambda a: (_country_sort_key(a, oc), a.date_published),
        reverse=False,
    )
    # Reverse date within each country group — newest first
    online = sorted(online, key=lambda a: (_country_sort_key(a, oc), -a.date_published.toordinal()))

    print_arts = sorted(
        org.print_articles.filter(date_published__gte=since).order_by('-date_published')[:50],
        key=lambda a: (_country_sort_key(a, oc), -a.date_published.toordinal()),
    )

    social = sorted(
        org.social_posts.filter(date_published__gte=since).order_by('-date_published')[:50],
        key=lambda a: (_country_sort_key(a, oc), -a.date_published.toordinal()),
    )

    broadcast = sorted(
        org.broadcast_mentions.filter(date_published__gte=since).order_by('-date_published')[:50],
        key=lambda a: (_country_sort_key(a, oc), -a.date_published.toordinal()),
    )

    return online, print_arts, social, broadcast


class Command(BaseCommand):
    help = "Send daily media digest emails for all active daily alerts"

    def add_arguments(self, parser):
        parser.add_argument('--org', type=str, help='Limit to a single org UUID')
        parser.add_argument('--dry-run', action='store_true', help='Print emails without sending')

    def handle(self, *args, **options):
        since = date.today() - timedelta(days=1)
        dry_run = options['dry_run']
        org_filter = options.get('org')

        qs = Alert.objects.filter(is_active=True, frequency='daily').exclude(email='').select_related('organization')
        if org_filter:
            qs = qs.filter(organization__id=org_filter)

        if not qs.exists():
            self.stdout.write('No qualifying alerts found.')
            return

        sent = 0
        for alert in qs:
            org = alert.organization
            online, print_arts, social, broadcast = _gather(org, since)

            subject = alert.email_subject or f"Social Light: {org.name} Daily Media Update"

            context = {
                'org':               org,
                'subject':           subject,
                'since':             since,
                'online_articles':   online,
                'print_articles':    print_arts,
                'social_posts':      social,
                'broadcast_mentions':broadcast,
                'online_count':      len(online),
                'print_count':       len(print_arts),
                'social_count':      len(social),
                'broadcast_count':   len(broadcast),
            }

            html_body = render_to_string('monitor/email/daily_digest.html', context)

            if dry_run:
                self.stdout.write(f"\n{'='*60}")
                self.stdout.write(f"TO:      {alert.email}")
                self.stdout.write(f"SUBJECT: {subject}")
                self.stdout.write(f"Online: {len(online)}  Print: {len(print_arts)}  Social: {len(social)}  Broadcast: {len(broadcast)}")
                if online:
                    self.stdout.write("  First online article:")
                    self.stdout.write(f"    [{online[0].country or 'no country'}] {online[0].headline[:70]}")
                sent += 1
                continue

            msg = EmailMultiAlternatives(
                subject=subject,
                body=f"{subject}\n\nOpen in an HTML-capable email client to view this message.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[alert.email],
            )
            msg.attach_alternative(html_body, "text/html")

            try:
                msg.send()
                self.stdout.write(f"Sent to {alert.email} — {org.name}")
                sent += 1
            except Exception as exc:
                self.stderr.write(f"Failed for {alert.email} ({org.name}): {exc}")

        self.stdout.write(f"\n{'Dry-run' if dry_run else 'Sent'}: {sent} email(s)")
