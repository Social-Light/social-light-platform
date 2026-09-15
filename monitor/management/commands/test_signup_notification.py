"""Check that the new-trial-signup notification actually reaches the configured
staff list, without running through the real signup form.

    python manage.py test_signup_notification                    # to SIGNUP_NOTIFICATION_EMAILS
    python manage.py test_signup_notification you@example.com     # ...and/or an override address

Sends the same template signup() emails via ``_notify_new_signup``, populated
with obviously fake data, so what lands in the inbox is exactly what a real
signup produces - styling included.
"""
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.utils import timezone


class Command(BaseCommand):
    help = 'Send a test "new trial signup" notification with fake sample data.'

    def add_arguments(self, parser):
        parser.add_argument('recipient', nargs='?',
                            help='Address to send to instead of SIGNUP_NOTIFICATION_EMAILS.')

    def handle(self, *args, **options):
        recipients = [options['recipient']] if options.get('recipient') else list(
            getattr(settings, 'SIGNUP_NOTIFICATION_EMAILS', []))

        if not recipients:
            raise CommandError(
                'No recipients: SIGNUP_NOTIFICATION_EMAILS is empty and no address was given. '
                'Either set that setting or pass an address, e.g.\n'
                '  python manage.py test_signup_notification you@example.com')

        ctx = {
            'org_name': 'Test Organisation (Pty) Ltd',
            'contact_name': 'Jane Test',
            'contact_email': 'jane@example.com',
            'contact_phone': '+267 71 234 567',
            'country': 'Botswana',
            'registration_id': uuid.uuid4(),
            'trial_ends_at': timezone.now() + timedelta(days=getattr(settings, 'TRIAL_PERIOD_DAYS', 14)),
            'admin_url': f'{getattr(settings, "SITE_URL", "http://127.0.0.1:8000")}'
                         '/admin/monitor/organization/TEST-LINK-NOT-VALID/change/',
        }

        self.stdout.write(f'Sending test notification to: {", ".join(recipients)} ...')
        send_mail(
            subject=f'[TEST] New trial signup — {ctx["org_name"]}',
            message=render_to_string('monitor/email/new_signup_notification.txt', ctx),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            html_message=render_to_string('monitor/email/new_signup_notification.html', ctx),
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS('Sent. Check the inbox(es), including spam.'))
