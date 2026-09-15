"""Tests for how email leaves the platform.

Two transports are supported, chosen by EMAIL_BACKEND alone — a .env decision,
never a code one: plain SMTP (a fresh deployment's default), and the anymail/
Resend HTTP API (monitor/verification.py's `email_configuration_problem` and
`redact_smtp_credentials` are transport-aware, and the platform docstring in
socialmonitor/settings.py explains why 2026-09-04 put this host back on the API
path — outbound SMTP is blocked here, HTTPS is not). The point of this module is
the transport layer itself: that a transport failure costs a user neither their
account nor their verification link, and that a mail credential — the SMTP
password or the Resend key — cannot reach a page, a log line or a console.

Everything runs against Django's in-memory backend or a patched one. No socket is
opened and no credential is real.
"""
from pathlib import Path
from smtplib import SMTPAuthenticationError
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.mail import get_connection
from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPBackend
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from monitor import verification
from monitor.alert_email import build_and_send
from monitor.models import Alert, EmailVerificationToken, Event, Organization, User

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
SMTP = 'django.core.mail.backends.smtp.EmailBackend'

# Credentials that exist only here. Deliberately not readable as a real host or a
# real password, so that a copy-paste out of this file cannot configure anything.
FAKE_SMTP = {
    'EMAIL_BACKEND': SMTP,
    'EMAIL_HOST': 'smtp.invalid.test',
    'EMAIL_PORT': 587,
    'EMAIL_HOST_USER': 'not-a-real-user',
    'EMAIL_HOST_PASSWORD': 'not-a-real-password',
    'EMAIL_USE_TLS': True,
    'EMAIL_USE_SSL': False,
}

REGISTRATION = {
    'first_name': 'Tebogo',
    'last_name': 'Kgosi',
    'email': 'tebogo@ministry.co.bw',
    'phone': '+267 71 999 111',
    'job_title': 'Press Officer',
    'country': 'Botswana',
    'org_name': 'Ministry of Transport',
    'password': 'correct-horse-9',
    'confirm_password': 'correct-horse-9',
}

PROJECT_ROOT = Path(settings.BASE_DIR)


class TransportTests(TestCase):
    """Plain SMTP is what a fresh deployment gets with no .env at all; the
    anymail/Resend HTTP API is the other supported option, selected the same
    way — EMAIL_BACKEND, nothing else."""

    def test_the_configured_backend_is_djangos_smtp_backend(self):
        with override_settings(**FAKE_SMTP):
            connection = get_connection()
        self.assertIsInstance(connection, DjangoSMTPBackend)

    def test_smtp_is_the_default_when_the_environment_says_nothing(self):
        """Read from the source rather than from the loaded settings: the running
        process has a .env, and the question here is what a deployment without one
        would get. A specific deployment is free to override EMAIL_BACKEND to the
        anymail path instead (this host's own .env does, since outbound SMTP is
        blocked here) — that override belongs in .env, not in this default."""
        source = (PROJECT_ROOT / 'socialmonitor' / 'settings.py').read_text(encoding='utf-8')
        self.assertIn(
            "EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', "
            "'django.core.mail.backends.smtp.EmailBackend')",
            source,
        )

    def test_the_smtp_connection_is_built_from_the_environment_settings(self):
        """Which mail provider is used has to be a .env question. If any of these
        stopped being read from settings, switching provider would need a code
        change."""
        with override_settings(**FAKE_SMTP):
            connection = get_connection()
        self.assertEqual(connection.host, 'smtp.invalid.test')
        self.assertEqual(connection.port, 587)
        self.assertEqual(connection.username, 'not-a-real-user')
        self.assertTrue(connection.use_tls)
        self.assertFalse(connection.use_ssl)

    def test_the_anymail_backend_is_available_and_reads_the_resend_key(self):
        """The other supported transport: not the default, but installed and
        wired up so a deployment's .env can select it outright — which is
        exactly what this host's own .env currently does, since outbound SMTP
        is blocked here but HTTPS is not."""
        self.assertIn('anymail', settings.INSTALLED_APPS)
        with override_settings(EMAIL_BACKEND='anymail.backends.resend.EmailBackend',
                               ANYMAIL={'RESEND_API_KEY': 'not-a-real-key'}):
            self.assertIsNone(verification.email_configuration_problem())
            connection = get_connection()
        self.assertEqual(connection.__class__.__module__, 'anymail.backends.resend')

    def test_a_missing_resend_key_is_reported_rather_than_left_to_fail_silently(self):
        with override_settings(EMAIL_BACKEND='anymail.backends.resend.EmailBackend',
                               ANYMAIL={'RESEND_API_KEY': ''}):
            problem = verification.email_configuration_problem()
        self.assertIn('RESEND_API_KEY', problem)

    def test_email_works_with_no_environment_variables_present(self):
        with mock.patch.dict('os.environ', {}, clear=True), override_settings(EMAIL_BACKEND=LOCMEM):
            self.assertIsNone(verification.email_configuration_problem())
            mail.send_mail('Subject', 'Body', settings.DEFAULT_FROM_EMAIL, ['someone@example.com'])
        self.assertEqual(len(mail.outbox), 1)


class CredentialLeakTests(TestCase):
    """The SMTP password is the one configuration value that must never be
    printed. Mail servers do not normally echo it back, but the failure path
    stringifies whatever the server said and shows it to a developer."""

    def test_the_password_is_stripped_out_of_a_failure_detail(self):
        with override_settings(**FAKE_SMTP):
            scrubbed = verification.redact_smtp_credentials(
                'server said: AUTH failed for not-a-real-password')
        self.assertNotIn('not-a-real-password', scrubbed)
        self.assertIn('[redacted]', scrubbed)

    def test_a_configuration_problem_names_variables_and_never_values(self):
        with override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST='smtp.invalid.test',
                               EMAIL_HOST_USER='not-a-real-user', EMAIL_HOST_PASSWORD=''):
            problem = verification.email_configuration_problem()
        self.assertIn('EMAIL_HOST_PASSWORD', problem)
        self.assertNotIn('not-a-real-user', problem)


@override_settings(EMAIL_BACKEND=LOCMEM)
class VerificationDeliveryTests(TestCase):
    """The onboarding email itself: it is generated, it carries a working link,
    and it goes out over whatever backend is configured."""

    def setUp(self):
        self.client = Client()

    def _register(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        return User.objects.get(email=REGISTRATION['email'])

    @staticmethod
    def _verification_message():
        """Signup sends two messages — the trial welcome and the verification.
        Pick the one this class is about rather than trusting the order."""
        matches = [m for m in mail.outbox if 'Confirm your email address' in m.subject]
        assert len(matches) == 1, f'expected one verification email, got {len(matches)}'
        return matches[0]

    def test_signing_up_sends_the_verification_email(self):
        self._register()
        message = self._verification_message()
        self.assertEqual(message.to, [REGISTRATION['email']])

    def test_the_email_carries_the_link_that_verifies_the_account(self):
        user = self._register()
        token = EmailVerificationToken.objects.get(user=user)

        path = reverse('monitor:onboarding_verify_confirm', args=[token.token])
        self.assertIn(path, self._verification_message().body)

        self.client.get(path)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_the_html_alternative_carries_the_same_link(self):
        user = self._register()
        token = EmailVerificationToken.objects.get(user=user)
        html = self._verification_message().alternatives[0][0]
        self.assertIn(str(token.token), html)

    def test_asking_for_the_link_again_sends_another_email(self):
        self._register()
        mail.outbox.clear()

        response = self.client.post(reverse('monitor:onboarding_verify'))

        self.assertTrue(response.context['resent'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Confirm your email address', mail.outbox[0].subject)


@override_settings(**FAKE_SMTP)
class SmtpFailureTests(TestCase):
    """What a mail server outage may and may not cost the person signing up."""

    FAILURE = SMTPAuthenticationError(535, b'5.7.8 authentication failed')

    def setUp(self):
        self.client = Client()

    @staticmethod
    def smtp_down(failure):
        """Fail where a real mail server fails — at the connection — rather than
        by replacing ``send_messages``. Patching the backend method would also
        defeat ``fail_silently`` on the sends that deliberately use it, and this
        module is about the transport, not about rewriting those callers."""
        return mock.patch('django.core.mail.backends.smtp.smtplib.SMTP',
                          side_effect=failure)

    def _register_with_smtp_failing(self):
        with self.smtp_down(self.FAILURE):
            return self.client.post(reverse('monitor:signup'), REGISTRATION)

    def test_the_account_survives_a_failed_send(self):
        self._register_with_smtp_failing()
        user = User.objects.get(email=REGISTRATION['email'])
        self.assertEqual(user.onboarding.state, 'registered')
        self.assertFalse(user.email_verified)

    def test_the_verification_token_survives_a_failed_send(self):
        """Issued before the send is attempted and never rolled back — if the
        provider recovers, or an admin reads the link out of the log, the token
        that was issued is still the one that works."""
        self._register_with_smtp_failing()
        token = EmailVerificationToken.objects.get(user__email=REGISTRATION['email'])
        self.assertIsNone(token.used_at)
        self.assertFalse(token.is_expired)

    def test_the_failure_is_reported_rather_than_swallowed(self):
        """fail_silently would turn this into a page that says "Sent" while the
        user waits for an email that is never coming."""
        user = User.objects.create_user(username='u@example.com', email='u@example.com',
                                        password='pw-for-tests-1')
        with self.smtp_down(self.FAILURE):
            token, error = verification.send_verification_email(None, user)

        self.assertIsNotNone(token)
        self.assertIsNotNone(error)
        self.assertIn("couldn't send", error['message'])

    def test_the_failure_detail_never_carries_the_smtp_password(self):
        user = User.objects.create_user(username='u2@example.com', email='u2@example.com',
                                        password='pw-for-tests-1')
        leaky = SMTPAuthenticationError(535, b'rejected password not-a-real-password')
        with self.smtp_down(leaky):
            _token, error = verification.send_verification_email(None, user)

        self.assertNotIn('not-a-real-password', error['detail'])

    def test_the_user_can_ask_for_the_link_again_once_smtp_recovers(self):
        self._register_with_smtp_failing()

        with override_settings(EMAIL_BACKEND=LOCMEM):
            response = self.client.post(reverse('monitor:onboarding_verify'))

        self.assertTrue(response.context['resent'])
        self.assertIsNone(response.context['mail_error'])
        self.assertEqual(len(mail.outbox), 1)

    def test_a_failed_send_is_logged_for_whoever_is_debugging(self):
        user = User.objects.create_user(username='u3@example.com', email='u3@example.com',
                                        password='pw-for-tests-1')
        with self.smtp_down(self.FAILURE):
            with self.assertLogs('monitor.verification', level='ERROR') as captured:
                verification.send_verification_email(None, user)

        self.assertTrue(any('failed' in line.lower() for line in captured.output))


@override_settings(EMAIL_BACKEND=LOCMEM)
class ExistingEmailStillWorksTests(TestCase):
    """Everything the platform sent before the transport changed, it still sends."""

    def test_a_password_reset_email_is_sent_with_a_working_link(self):
        User.objects.create_user(username='reset@example.com', email='reset@example.com',
                                 password='pw-for-tests-1')

        response = self.client.post(reverse('password_reset'), {'email': 'reset@example.com'})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/reset/', mail.outbox[0].body)

    def test_an_alert_digest_is_sent(self):
        org = Organization.objects.create(name='Digest Org', country='Botswana')
        alert = Alert.objects.create(organization=org, name='Daily digest',
                                     recipients='desk@example.com', frequency='immediate')
        Event.objects.create(organization=org, category='report',
                             event_type='report_generated', title='Quarterly analysis ready',
                             summary='A report was generated.')

        result = build_and_send(alert, since=None, force=True)

        self.assertTrue(result['sent'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['desk@example.com'])

    def test_an_organisation_status_notice_is_sent(self):
        from monitor.org_email import send_org_disabled_email

        org = Organization.objects.create(name='Paused Org', email='admin@example.com')

        result = send_org_disabled_email(org)

        self.assertTrue(result['sent'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Paused Org', mail.outbox[0].subject)

    def test_every_message_goes_out_from_the_configured_sender(self):
        User.objects.create_user(username='sender@example.com', email='sender@example.com',
                                 password='pw-for-tests-1')
        self.client.post(reverse('password_reset'), {'email': 'sender@example.com'})
        self.assertEqual(mail.outbox[0].from_email, settings.DEFAULT_FROM_EMAIL)
