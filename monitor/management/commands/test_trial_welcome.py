"""Check what the trial-welcome email actually looks like, without running
through the real signup form.

    python manage.py test_trial_welcome you@example.com

Renders the same templates ``_send_trial_welcome`` uses, populated with
obviously fake data, so what lands in the inbox is exactly what a new signup
receives - styling included.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.utils import timezone

from monitor.models import trial_period_days


class Command(BaseCommand):
    help = 'Send a test "trial started" welcome email with fake sample data.'

    def add_arguments(self, parser):
        parser.add_argument('recipient', help='Address to send the sample to.')

    def handle(self, *args, **options):
        recipient = options['recipient']
        days = trial_period_days()
        ctx = {
            'full_name': 'Kabo',
            'org_name': 'Maru Communications (Pty) Ltd',
            'trial_days': days,
            'trial_ends_at': timezone.now() + timedelta(days=days),
            'protocol': 'https',
            'domain': getattr(settings, 'SITE_URL', 'https://sociallight.africa').split('//')[-1],
        }

        self.stdout.write(f'Sending test welcome email to: {recipient} ...')
        send_mail(
            subject=f'[TEST] Your {days}-day Social Light trial has started',
            message=render_to_string('monitor/email/trial_started.txt', ctx),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            html_message=render_to_string('monitor/email/trial_started.html', ctx),
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS('Sent. Check the inbox, including spam.'))
