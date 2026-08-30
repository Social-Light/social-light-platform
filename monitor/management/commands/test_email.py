"""Check that outbound email actually works, without going through signup.

Onboarding is blocked on email: a new account cannot reach the product until it
follows a verification link. When that link does not arrive there are half a
dozen candidate causes — an unset mail host, wrong SMTP credentials, the wrong
TLS/SSL pairing for the port, a firewall — and finding out by repeatedly
registering test accounts is slow and leaves debris in the database.

    python manage.py test_email                      # report the configuration
    python manage.py test_email you@example.com      # …and actually send

Anything this command cannot do, signup cannot do either.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string

NEWLINE = chr(10)

from monitor.verification import email_configuration_problem, redact_smtp_credentials


class Command(BaseCommand):
    help = 'Report the email configuration and, given an address, send a test message to it.'

    def add_arguments(self, parser):
        parser.add_argument('recipient', nargs='?',
                            help='Address to send a test message to. Omit to only report config.')
        parser.add_argument('--plain', action='store_true',
                            help='Send a minimal text-only probe instead of the real '
                                 'verification email. Useful when isolating a transport fault.')
        parser.add_argument('--base-url', dest='base_url', default=None,
                            help='Base URL for the link in the test email. Defaults to the '
                                 'local server in DEBUG, otherwise SITE_URL.')

    @staticmethod
    def _transport_name(backend):
        """A readable name for the backend. The dotted path's last segment is
        always the class name ("EmailBackend"), which tells the reader nothing."""
        for marker, label in (('smtp', 'SMTP'),
                              ('console', 'the console'), ('locmem', 'memory'),
                              ('filebased', 'files'), ('dummy', 'the dummy backend')):
            if marker in backend:
                return label
        return backend

    def handle(self, *args, **options):
        backend = settings.EMAIL_BACKEND
        short = self._transport_name(backend)

        self.stdout.write(self.style.MIGRATE_HEADING('Email configuration'))
        self.stdout.write(f'  EMAIL_BACKEND      {backend}')
        self.stdout.write(f'  DEFAULT_FROM_EMAIL {settings.DEFAULT_FROM_EMAIL}')

        if 'smtp' in backend:
            self.stdout.write(f'  EMAIL_HOST         {settings.EMAIL_HOST or "(not set)"}:{settings.EMAIL_PORT}')
            self.stdout.write(f'  EMAIL_HOST_USER    {settings.EMAIL_HOST_USER or "(not set)"}')
            self.stdout.write(f'  EMAIL_HOST_PASSWORD{"  set" if settings.EMAIL_HOST_PASSWORD else "  (not set)"}')
            self.stdout.write(f'  TLS / SSL          {settings.EMAIL_USE_TLS} / {settings.EMAIL_USE_SSL}')
        elif 'console' in backend:
            self.stdout.write(self.style.WARNING(
                '  Console backend - messages print here and are NOT delivered to anyone.'))
        elif 'locmem' in backend:
            self.stdout.write(self.style.WARNING('  In-memory backend - nothing is sent.'))

        problem = email_configuration_problem()
        if problem:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR(f'PROBLEM: {problem}'))
            self.stdout.write('Signup will create the account but the verification email '
                              'will not arrive until this is fixed.')

        recipient = options.get('recipient')
        if not recipient:
            self.stdout.write('')
            self.stdout.write('Pass an address to send a real test message, e.g.:')
            self.stdout.write('  python manage.py test_email you@example.com')
            return

        if problem:
            raise CommandError('Not sending - fix the problem above first.')

        self.stdout.write('')
        kind = 'a plain probe' if options.get('plain') else 'the real verification email'
        self.stdout.write(f'Sending {kind} to {recipient} over {short} ...')
        if not options.get('plain'):
            preview = options.get('base_url') or (
                'http://127.0.0.1:8000' if settings.DEBUG
                else (getattr(settings, 'SITE_URL', '') or ''))
            self.stdout.write(f'  the link in it points at {preview} and is deliberately '
                              'inert - it is a placeholder token, not a real one')

        if options.get('plain'):
            subject = 'Social Light email test'
            text = ('This is a transport test from the Social Light platform.' + NEWLINE * 2
                    + 'If you can read this, mail is leaving the server over '
                    + short + '.' + NEWLINE)
            html = ('<p>This is a transport test from the <strong>Social Light</strong> '
                    'platform.</p><p>Mail is leaving the server over ' + short + '.</p>')
        else:
            # The real message, with a deliberately dead link, so what lands in
            # the inbox is exactly what a user receives - styling included.
            # SITE_URL is the public production address. In development the link
            # must point at the server actually running this code, or it lands on
            # a deployed build that may not have the route yet - a 404 that looks
            # like a broken email rather than a not-yet-deployed one.
            base = options.get('base_url') or (
                'http://127.0.0.1:8000' if settings.DEBUG
                else (getattr(settings, 'SITE_URL', '') or '').rstrip('/'))
            base = base.rstrip('/')
            ctx = {
                'full_name': 'there',
                'verification_url': base + '/onboarding/verify/TEST-LINK-NOT-VALID/',
                'expires_at': '',
                'ttl_hours': int(getattr(settings, 'EMAIL_VERIFICATION_TTL_HOURS', 48)),
            }
            subject = '[TEST] Confirm your email address'
            text = render_to_string('monitor/email/verify_email.txt', ctx)
            html = render_to_string('monitor/email/verify_email.html', ctx)

        message = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            connection=get_connection(fail_silently=False),
        )
        message.attach_alternative(html, 'text/html')

        try:
            sent = message.send(fail_silently=False)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'FAILED: {exc.__class__.__name__}'))
            # Redacted: a chatty relay can echo the password back in its refusal,
            # and this output routinely gets pasted into tickets and chat.
            self.stdout.write(redact_smtp_credentials(exc))
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(self._hint(exc)))
            raise CommandError('Email could not be sent.')

        if not sent:
            raise CommandError('The backend reported that nothing was sent.')

        self.stdout.write(self.style.SUCCESS(
            f'Sent. Check {recipient}, including its spam folder.'))
        self.stdout.write('If it does not arrive within a few minutes, the provider accepted '
                          'the message but did not deliver it - check the provider dashboard, '
                          'not this application.')

    def _hint(self, exc):
        """Turn the common provider errors into something actionable."""
        text = redact_smtp_credentials(exc).lower()
        if 'authentication' in text or '535' in text or '5.7.8' in text:
            return ('The mail server rejected EMAIL_HOST_USER / EMAIL_HOST_PASSWORD. Many '
                    'providers require an application-specific password rather than the '
                    'account password.')
        if '530' in text or '5.7.0' in text or 'must issue a starttls' in text:
            return ('The mail server requires an encrypted session. Set EMAIL_USE_TLS=True '
                    'for port 587, or EMAIL_USE_SSL=True for port 465.')
        if '553' in text or '550' in text or 'sender' in text or 'not verified' in text:
            return ('The mail server refused the sending address. DEFAULT_FROM_EMAIL usually '
                    'has to belong to a domain or mailbox the account is allowed to send as.')
        if 'ssl' in text or 'certificate' in text or 'wrong_version' in text:
            return ('A TLS problem, usually a proxy or antivirus intercepting HTTPS, or a '
                    'wrong EMAIL_USE_TLS / EMAIL_USE_SSL combination for this port.')
        if 'timeout' in text or 'timed out' in text or 'connection' in text:
            return ('Could not reach the mail server. Check EMAIL_HOST and EMAIL_PORT, and '
                    'whether outbound traffic on that port is blocked.')
        return 'See the error above; the mail server usually states the reason directly.'
