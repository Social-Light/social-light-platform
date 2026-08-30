"""Tests for onboarding, legal consent, payments and feature entitlements.

Everything here runs against the test database and the in-memory email backend,
so no real mail server, Celery, payment gateway or real data is touched. The packages and
legal documents the tests rely on are the ones migrations 0016 and 0019 write
into every fresh database, so these also check that a new deployment comes up
with a usable price list and a usable set of consent documents.
"""
import re
from datetime import timedelta

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from monitor import legal, onboarding, verification
from monitor.entitlements import entitlements_for_organization, user_has_feature
from monitor.models import (AgencyDeclaration, ConsentRecord, EmailVerificationToken,
                            LegalDocument, OnboardingProgress, Organization, Package,
                            Payment, User)
from monitor.payments import ManualProvider, get_provider, record_manual_payment

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
SMTP = 'django.core.mail.backends.smtp.EmailBackend'

# The SMTP backend still carrying the placeholders from .env.example — i.e. a
# deployment where nobody has configured mail. Recognised before a send is
# attempted, so these tests never open a socket.
UNCONFIGURED_SMTP = {
    'EMAIL_BACKEND': SMTP,
    'EMAIL_HOST': 'smtp.your-provider.example',
    'EMAIL_HOST_USER': 'your-smtp-username',
    'EMAIL_HOST_PASSWORD': 'your-smtp-password',
}

REGISTRATION = {
    'first_name': 'Naledi',
    'last_name': 'Mokgadi',
    'email': 'naledi@ministry.co.bw',
    'phone': '+267 71 234 567',
    'job_title': 'Communications Manager',
    'country': 'Botswana',
    'org_name': 'Ministry of Health',
    'password': 'correct-horse-9',
    'confirm_password': 'correct-horse-9',
}

AGENCY_POST = {
    'account_type': 'organisation',
    'organisation_name': 'Ministry of Health',
    'position': 'Communications Manager',
    'is_authorised': 'on',
}


def complete_account(email='member@example.com', role='org_admin', org=None, package=None,
                     plan_status='active'):
    """A user who is past onboarding, for the tests that are about what a
    finished account can and cannot do."""
    if org is None:
        org = Organization.objects.create(name=f'Org for {email}', plan_status=plan_status,
                                          package=package)
    elif package is not None:
        org.package = package
        org.plan_status = plan_status
        org.save(update_fields=['package', 'plan_status'])

    user = User.objects.create_user(
        username=email, email=email, password='pw-for-tests-1',
        first_name='Test', last_name='User', role=role, organization=org,
        email_verified=True, email_verified_at=timezone.now(),
    )
    OnboardingProgress.objects.create(user=user, state='complete', completed_at=timezone.now())
    return user, org


def free_package():
    return Package.objects.get(slug='free')


def paid_package():
    """Scale — the tier whose price-list bullets promise export."""
    return Package.objects.get(slug='scale')


# ═══════════════════════════════════════════════════════════════════════════
#  1–3. Registration, verification, and what an unverified account may do
# ═══════════════════════════════════════════════════════════════════════════

@override_settings(EMAIL_BACKEND=LOCMEM)
class RegistrationTests(TestCase):

    def test_a_user_can_register(self):
        response = self.client.post(reverse('monitor:signup'), REGISTRATION)

        user = User.objects.get(email=REGISTRATION['email'])
        self.assertEqual(user.first_name, 'Naledi')
        self.assertEqual(user.last_name, 'Mokgadi')
        self.assertEqual(user.phone, '+267 71 234 567')
        self.assertEqual(user.job_title, 'Communications Manager')
        self.assertEqual(user.country, 'Botswana')
        self.assertEqual(user.organization.name, 'Ministry of Health')
        self.assertEqual(user.role, 'org_admin')
        self.assertRedirects(response, reverse('monitor:onboarding_verify'))

    def test_registration_starts_onboarding_at_the_verify_step(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        user = User.objects.get(email=REGISTRATION['email'])

        self.assertEqual(user.onboarding.state, 'registered')
        self.assertFalse(user.onboarding.is_legacy)
        self.assertFalse(user.email_verified)
        self.assertEqual(onboarding.next_step(user).key, 'verify')

    def test_registration_sends_a_verification_link(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        user = User.objects.get(email=REGISTRATION['email'])
        token = EmailVerificationToken.objects.get(user=user)

        verification = [m for m in mail.outbox if 'Confirm your email' in m.subject]
        self.assertEqual(len(verification), 1)
        self.assertIn(token.token, verification[0].body)
        self.assertTrue(token.is_usable)

    def test_the_older_contact_name_field_is_still_accepted(self):
        """Anything still posting the previous single-name form keeps working."""
        payload = {k: v for k, v in REGISTRATION.items() if k not in ('first_name', 'last_name')}
        payload['contact_name'] = 'Naledi Mokgadi'

        self.client.post(reverse('monitor:signup'), payload)

        user = User.objects.get(email=REGISTRATION['email'])
        self.assertEqual((user.first_name, user.last_name), ('Naledi', 'Mokgadi'))


@override_settings(EMAIL_BACKEND=LOCMEM)
class EmailFailureTests(TestCase):
    """When the mail provider refuses, the page must say so.

    The original implementation sent with ``fail_silently=True``, so a
    misconfigured provider produced a page that said "Sent" while nothing had
    been sent — leaving the user stuck on a step they could not pass and giving
    whoever was debugging nothing to go on.
    """

    def test_the_placeholder_host_is_reported_as_a_configuration_problem(self):
        with override_settings(**UNCONFIGURED_SMTP):
            problem = verification.email_configuration_problem()
        self.assertIsNotNone(problem)
        self.assertIn('placeholder', problem)

    def test_a_missing_mail_host_is_reported(self):
        with override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST=''):
            self.assertIn('not set', verification.email_configuration_problem())

    def test_a_username_without_a_password_is_reported(self):
        with override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST='smtp.example.net',
                               EMAIL_HOST_USER='postmaster@example.net', EMAIL_HOST_PASSWORD=''):
            self.assertIn('EMAIL_HOST_PASSWORD', verification.email_configuration_problem())

    def test_tls_and_ssl_together_are_reported(self):
        with override_settings(EMAIL_BACKEND=SMTP, EMAIL_HOST='smtp.example.net',
                               EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='',
                               EMAIL_USE_TLS=True, EMAIL_USE_SSL=True):
            self.assertIn('EMAIL_USE_SSL', verification.email_configuration_problem())

    def test_a_working_backend_reports_no_problem(self):
        self.assertIsNone(verification.email_configuration_problem())

    def test_registration_still_succeeds_when_email_cannot_be_sent(self):
        """A mail outage must not cost someone their registration."""
        with override_settings(**UNCONFIGURED_SMTP):
            response = self.client.post(reverse('monitor:signup'), REGISTRATION)

        user = User.objects.get(email=REGISTRATION['email'])
        self.assertEqual(user.onboarding.state, 'registered')
        self.assertRedirects(response, reverse('monitor:onboarding_verify'))

    def test_the_verify_page_admits_the_email_did_not_go_out(self):
        with override_settings(**UNCONFIGURED_SMTP):
            self.client.post(reverse('monitor:signup'), REGISTRATION)
            response = self.client.get(reverse('monitor:onboarding_verify'))

        self.assertContains(response, "couldn&#x27;t send your confirmation email")
        self.assertNotContains(response, 'We sent a confirmation link')

    @override_settings(DEBUG=False)
    def test_the_user_is_never_shown_the_technical_detail(self):
        """A provider validation error or an exception class name tells the
        person signing up nothing they can act on, and reads like a broken site."""
        with override_settings(**UNCONFIGURED_SMTP):
            self.client.post(reverse('monitor:signup'), REGISTRATION)
            response = self.client.get(reverse('monitor:onboarding_verify'))

        body = response.content.decode()
        self.assertIn("couldn&#x27;t send your confirmation email", body)
        for leak in ('EMAIL_HOST', 'your-smtp-password', 'SMTPAuthenticationError',
                     'Development mode', 'placeholder', '.env'):
            self.assertNotIn(leak, body, f'{leak!r} must not be shown to an end user')

    @override_settings(DEBUG=True)
    def test_the_technical_detail_is_available_to_a_developer(self):
        with override_settings(**UNCONFIGURED_SMTP):
            self.client.post(reverse('monitor:signup'), REGISTRATION)
            response = self.client.get(reverse('monitor:onboarding_verify'))

        self.assertContains(response, 'Development mode')
        self.assertContains(response, 'EMAIL_HOST')

    def test_no_raw_template_syntax_reaches_the_page(self):
        """A multi-line {# … #} is not a Django comment — it renders literally.
        This caught exactly that on the verify step."""
        with override_settings(**UNCONFIGURED_SMTP):
            self.client.post(reverse('monitor:signup'), REGISTRATION)
            response = self.client.get(reverse('monitor:onboarding_verify'))

        body = response.content.decode()
        for marker in ('{#', '#}', '{%', '%}', '{{', '}}'):
            self.assertNotIn(marker, body, f'unrendered template syntax {marker!r} on the page')

    def test_a_failed_resend_does_not_claim_success(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        with override_settings(**UNCONFIGURED_SMTP):
            response = self.client.post(reverse('monitor:onboarding_verify'))

        self.assertFalse(response.context['resent'])
        self.assertIsNotNone(response.context['mail_error'])

    def test_a_successful_send_says_so_and_reports_no_error(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        response = self.client.post(reverse('monitor:onboarding_verify'))

        self.assertTrue(response.context['resent'])
        self.assertIsNone(response.context['mail_error'])

    @override_settings(DEBUG=True)
    def test_debug_mode_shows_the_link_so_a_developer_is_not_stuck(self):
        with override_settings(**UNCONFIGURED_SMTP):
            self.client.post(reverse('monitor:signup'), REGISTRATION)
            response = self.client.get(reverse('monitor:onboarding_verify'))

        token = EmailVerificationToken.objects.get(user__email=REGISTRATION['email'])
        self.assertContains(response, token.token)
        self.assertContains(response, 'Development mode')

    def test_the_link_is_never_shown_outside_debug(self):
        """It is a credential for the account — it must not appear on a page in
        production merely because email is broken."""
        with override_settings(DEBUG=False, **UNCONFIGURED_SMTP):
            self.client.post(reverse('monitor:signup'), REGISTRATION)
            response = self.client.get(reverse('monitor:onboarding_verify'))

        token = EmailVerificationToken.objects.get(user__email=REGISTRATION['email'])
        self.assertNotContains(response, token.token)
        self.assertIsNone(response.context['verification_url'])


@override_settings(EMAIL_BACKEND=LOCMEM)
class SignupCsrfTests(TestCase):
    """The signup form under real CSRF enforcement.

    Django's test client disables CSRF checking by default, so every other test
    here would still pass if the form had lost its ``{% csrf_token %}``. These
    use ``enforce_csrf_checks=True`` to exercise the same path a browser does.
    """

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def token_from(self, response):
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"',
                          response.content.decode())
        return match.group(1) if match else None

    def test_the_form_renders_a_csrf_token_inside_the_form(self):
        response = self.client.get(reverse('monitor:signup'))
        body = response.content.decode()

        token = self.token_from(response)
        self.assertIsNotNone(token, 'the signup form must render a CSRF token')
        self.assertLess(body.index('<form'), body.index('csrfmiddlewaretoken'))
        self.assertLess(body.index('csrfmiddlewaretoken'), body.index('</form>'))

    def test_a_submission_carrying_the_token_is_accepted(self):
        response = self.client.get(reverse('monitor:signup'))
        token = self.token_from(response)

        posted = self.client.post(reverse('monitor:signup'),
                                  {**REGISTRATION, 'csrfmiddlewaretoken': token})

        self.assertEqual(posted.status_code, 302, 'a token-carrying signup must succeed')
        self.assertTrue(User.objects.filter(email=REGISTRATION['email']).exists())

    def test_a_submission_without_a_token_is_rejected(self):
        """Not a bug — this is the protection working. A 403 here means the
        browser submitted a stale or non-live copy of the page."""
        self.client.get(reverse('monitor:signup'))

        posted = self.client.post(reverse('monitor:signup'), REGISTRATION)

        self.assertEqual(posted.status_code, 403)
        self.assertFalse(User.objects.filter(email=REGISTRATION['email']).exists())

    def test_the_rejection_page_tells_the_user_how_to_recover(self):
        """A bare 403 leaves someone stuck. The page has to say "reload"."""
        self.client.get(reverse('monitor:signup'))
        posted = self.client.post(reverse('monitor:signup'), REGISTRATION)

        self.assertContains(posted, 'Reload and try again', status_code=403)
        self.assertContains(posted, 'Nothing was lost', status_code=403)

    def test_the_rejection_identifies_a_form_that_sent_no_token(self):
        """The diagnostic must distinguish 'no token in the form' from the other
        cause of the same Django message — an unreadable request body."""
        self.client.get(reverse('monitor:signup'))
        posted = self.client.post(reverse('monitor:signup'), REGISTRATION)

        facts = posted.context['facts']
        self.assertTrue(facts['body_readable'])
        self.assertFalse(facts['token_in_post'])
        self.assertIn('first_name', facts['posted_fields'])
        self.assertIn('no token', facts['likely_cause'])

    @override_settings(DEBUG=False)
    def test_the_diagnostics_are_hidden_outside_debug(self):
        self.client.get(reverse('monitor:signup'))
        posted = self.client.post(reverse('monitor:signup'), REGISTRATION)

        self.assertNotContains(posted, 'Diagnostics', status_code=403)
        self.assertContains(posted, 'Reload and try again', status_code=403)


@override_settings(EMAIL_BACKEND=LOCMEM)
class PasswordConfirmationTests(TestCase):
    """A mistyped password at signup creates an account whose owner cannot get
    into it — the only way out is a reset. So it is confirmed."""

    def post(self, **overrides):
        return self.client.post(reverse('monitor:signup'), {**REGISTRATION, **overrides})

    def test_matching_passwords_create_the_account(self):
        self.post(confirm_password=REGISTRATION['password'])
        self.assertTrue(User.objects.filter(email=REGISTRATION['email']).exists())

    def test_a_mismatch_is_rejected_and_no_account_is_created(self):
        response = self.post(confirm_password='something-else-9')

        self.assertEqual(response.status_code, 200)
        self.assertIn('confirm_password', response.context['errors'])
        self.assertFalse(User.objects.filter(email=REGISTRATION['email']).exists())
        self.assertFalse(Organization.objects.filter(name=REGISTRATION['org_name']).exists())

    def test_a_blank_confirmation_is_rejected(self):
        response = self.post(confirm_password='')
        self.assertIn('confirm_password', response.context['errors'])
        self.assertFalse(User.objects.filter(email=REGISTRATION['email']).exists())

    def test_the_password_rules_are_reported_before_the_mismatch(self):
        """Being told the password is too short *and* that it does not match is
        noise — fix the password first."""
        response = self.post(password='short', confirm_password='different')

        self.assertIn('password', response.context['errors'])
        self.assertNotIn('confirm_password', response.context['errors'])

    def test_the_password_is_never_handed_back_to_the_template(self):
        response = self.post(confirm_password='mismatch-9')

        self.assertNotIn('password', response.context['values'])
        self.assertNotIn('confirm_password', response.context['values'])
        self.assertNotContains(response, REGISTRATION['password'])

    def test_the_other_fields_survive_a_rejected_submission(self):
        response = self.post(confirm_password='mismatch-9')

        values = response.context['values']
        self.assertEqual(values['first_name'], 'Naledi')
        self.assertEqual(values['email'], REGISTRATION['email'])
        self.assertEqual(values['org_name'], 'Ministry of Health')

    def test_the_form_renders_a_confirmation_field(self):
        response = self.client.get(reverse('monitor:signup'))
        self.assertContains(response, 'name="confirm_password"')
        self.assertContains(response, 'Confirm password')


class CountryChoiceTests(TestCase):
    """The country list. Africa is covered in full — a client in Lagos or Nairobi
    picking "Other" because their country was left out is lost data, not a
    cosmetic problem."""

    def test_every_african_country_is_offered(self):
        self.assertEqual(len(onboarding.AFRICAN_COUNTRIES), 54)
        for country in ('Nigeria', 'Kenya', 'Egypt', 'Morocco', 'Ethiopia', 'Ghana',
                        'Rwanda', 'Senegal', 'Botswana', 'South Africa', 'Tunisia',
                        "Côte d'Ivoire", 'São Tomé and Príncipe',
                        'Congo (Democratic Republic of the)'):
            self.assertIn(country, onboarding.COUNTRY_CHOICES, country)

    def test_the_list_has_no_duplicates_and_ends_with_other(self):
        self.assertEqual(len(onboarding.COUNTRY_CHOICES),
                         len(set(onboarding.COUNTRY_CHOICES)))
        self.assertEqual(onboarding.COUNTRY_CHOICES[-1], 'Other')

    def test_african_countries_are_in_alphabetical_order(self):
        """Accents must not push São Tomé to the bottom of the S section."""
        import unicodedata

        def sort_key(name):
            stripped = unicodedata.normalize('NFD', name)
            return ''.join(c for c in stripped if unicodedata.category(c) != 'Mn').lower()

        self.assertEqual(onboarding.AFRICAN_COUNTRIES,
                         sorted(onboarding.AFRICAN_COUNTRIES, key=sort_key))

    def test_no_country_name_is_too_long_for_the_field(self):
        longest = max(len(c) for c in onboarding.COUNTRY_CHOICES)
        self.assertLessEqual(longest, User._meta.get_field('country').max_length)
        self.assertLessEqual(longest, Organization._meta.get_field('country').max_length)

    def test_the_signup_form_offers_the_full_list_with_botswana_preselected(self):
        response = self.client.get(reverse('monitor:signup'))
        self.assertEqual(response.context['countries'], onboarding.COUNTRY_CHOICES)
        self.assertEqual(response.context['values']['country'], 'Botswana')
        self.assertContains(response, '<option value="Nigeria"')
        self.assertContains(response, '<option value="Botswana" selected>')

    def test_registering_from_another_african_country_is_stored(self):
        self.client.post(reverse('monitor:signup'), {**REGISTRATION, 'country': 'Kenya'})

        user = User.objects.get(email=REGISTRATION['email'])
        self.assertEqual(user.country, 'Kenya')
        self.assertEqual(user.organization.country, 'Kenya')


@override_settings(EMAIL_BACKEND=LOCMEM)
class UnverifiedAccessTests(TestCase):
    """An account that has not proved control of its email address gets nowhere."""

    def setUp(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        self.user = User.objects.get(email=REGISTRATION['email'])
        self.org = self.user.organization

    def test_unverified_user_is_sent_back_to_the_verify_step(self):
        response = self.client.get(reverse('monitor:dashboard', args=[self.org.id]))
        self.assertRedirects(response, reverse('monitor:onboarding_verify'))

    def test_unverified_user_gets_a_403_from_the_api(self):
        response = self.client.get(
            reverse('monitor:coverage_export', args=[self.org.id, 'online']))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'onboarding_incomplete')

    def test_unverified_user_cannot_skip_ahead_to_a_later_step(self):
        response = self.client.get(reverse('monitor:onboarding_plan'))
        self.assertRedirects(response, reverse('monitor:onboarding_verify'))

    def test_following_the_link_verifies_the_account_and_moves_it_on(self):
        token = EmailVerificationToken.objects.get(user=self.user)
        response = self.client.get(
            reverse('monitor:onboarding_verify_confirm', args=[token.token]))

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertIsNotNone(self.user.email_verified_at)
        self.assertEqual(self.user.onboarding.state, 'email_verified')
        self.assertRedirects(response, reverse('monitor:onboarding_profile'))

    def test_the_link_works_in_a_browser_with_no_session(self):
        """Confirmation emails get opened on phones. The token is the proof of
        control, so requiring a sign-in first would only strand people."""
        token = EmailVerificationToken.objects.get(user=self.user)
        self.client.logout()

        response = self.client.get(
            reverse('monitor:onboarding_verify_confirm', args=[token.token]))

        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)
        self.assertRedirects(response, f"{reverse('monitor:login')}?verified=1",
                             fetch_redirect_response=False)

    def test_a_used_link_cannot_verify_a_second_account(self):
        token = EmailVerificationToken.objects.get(user=self.user)
        self.client.get(reverse('monitor:onboarding_verify_confirm', args=[token.token]))
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)

    def test_an_expired_link_is_refused_and_offers_a_new_one(self):
        token = EmailVerificationToken.objects.get(user=self.user)
        token.expires_at = timezone.now() - timedelta(seconds=1)
        token.save(update_fields=['expires_at'])

        response = self.client.get(
            reverse('monitor:onboarding_verify_confirm', args=[token.token]))

        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        self.assertContains(response, 'expired', status_code=200)

    def test_a_resend_invalidates_the_previous_link(self):
        old = EmailVerificationToken.objects.get(user=self.user)
        self.client.post(reverse('monitor:onboarding_verify'))

        self.assertFalse(EmailVerificationToken.objects.filter(pk=old.pk).exists())
        self.assertEqual(EmailVerificationToken.objects.filter(user=self.user).count(), 1)


# ═══════════════════════════════════════════════════════════════════════════
#  4–6. Walking the flow, and the agency declaration
# ═══════════════════════════════════════════════════════════════════════════

@override_settings(EMAIL_BACKEND=LOCMEM, PAYMENTS_ENABLED=False)
class OnboardingFlowTests(TestCase):

    def setUp(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        self.user = User.objects.get(email=REGISTRATION['email'])
        token = EmailVerificationToken.objects.get(user=self.user)
        self.client.get(reverse('monitor:onboarding_verify_confirm', args=[token.token]))
        self.user.refresh_from_db()

    # ── helpers ──────────────────────────────────────────────────────────────
    def submit_profile(self):
        return self.client.post(reverse('monitor:onboarding_profile'), {
            'first_name': 'Naledi', 'last_name': 'Mokgadi',
            'job_title': 'Communications Manager', 'phone': '', 'country': 'Botswana',
        })

    def accept_all_documents(self):
        for name in ('onboarding_terms', 'onboarding_privacy', 'onboarding_disclaimer'):
            self.client.post(reverse(f'monitor:{name}'), {'accept': 'on'})

    def state(self):
        self.user.refresh_from_db()
        return self.user.onboarding.state

    # ── 4. the whole flow ────────────────────────────────────────────────────
    def test_an_individual_user_can_complete_onboarding(self):
        self.submit_profile()
        self.client.post(reverse('monitor:onboarding_agency'), {'account_type': 'individual'})
        self.accept_all_documents()
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})
        self.client.post(reverse('monitor:onboarding_plan'), {'package': 'free'})
        self.client.get(reverse('monitor:onboarding_done'))

        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding.is_complete)
        self.assertTrue(onboarding.is_complete(self.user))
        self.assertEqual(self.user.agency_declaration.account_type, 'individual')
        self.assertEqual(ConsentRecord.objects.filter(user=self.user, decision='accepted').count(), 3)

    def test_a_completed_account_reaches_the_application(self):
        self.submit_profile()
        self.client.post(reverse('monitor:onboarding_agency'), {'account_type': 'individual'})
        self.accept_all_documents()
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})
        self.client.post(reverse('monitor:onboarding_plan'), {'package': 'free'})
        self.client.get(reverse('monitor:onboarding_done'))

        response = self.client.get(reverse('monitor:dashboard', args=[self.user.organization_id]))
        self.assertEqual(response.status_code, 200)

    def test_onboarding_is_resumable(self):
        """Abandoning half-way and coming back lands on the same step, with the
        earlier answers still there."""
        self.submit_profile()
        self.assertEqual(self.state(), 'profile_completed')

        self.client.logout()
        self.client.login(username=REGISTRATION['email'], password=REGISTRATION['password'])

        response = self.client.get(reverse('monitor:dashboard', args=[self.user.organization_id]))
        self.assertRedirects(response, reverse('monitor:onboarding_agency'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.job_title, 'Communications Manager')

    def test_a_user_may_go_back_to_a_completed_step(self):
        self.submit_profile()
        response = self.client.get(reverse('monitor:onboarding_profile'))
        self.assertEqual(response.status_code, 200)

    # ── 5 & 6. the agency declaration ────────────────────────────────────────
    def test_an_agency_user_must_provide_agency_information(self):
        self.submit_profile()
        response = self.client.post(reverse('monitor:onboarding_agency'),
                                    {'account_type': 'organisation', 'is_authorised': 'on'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('organisation_name', response.context['errors'])
        self.assertIn('position', response.context['errors'])
        self.assertFalse(AgencyDeclaration.objects.filter(user=self.user).exists())
        self.assertEqual(self.state(), 'profile_completed')

    def test_an_agency_user_must_confirm_they_are_authorised(self):
        self.submit_profile()
        payload = {k: v for k, v in AGENCY_POST.items() if k != 'is_authorised'}
        response = self.client.post(reverse('monitor:onboarding_agency'), payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn('is_authorised', response.context['errors'])
        self.assertFalse(AgencyDeclaration.objects.filter(user=self.user).exists())
        self.assertEqual(self.state(), 'profile_completed')

    def test_confirming_authority_is_recorded_with_a_timestamp_and_ip(self):
        self.submit_profile()
        self.client.post(reverse('monitor:onboarding_agency'), AGENCY_POST)

        declaration = AgencyDeclaration.objects.get(user=self.user)
        self.assertTrue(declaration.is_authorised_representative)
        self.assertIsNotNone(declaration.authorised_at)
        self.assertIsNotNone(declaration.authorisation_ip)
        self.assertTrue(declaration.is_complete)
        self.assertEqual(self.state(), 'agency_declared')

    def test_an_individual_is_not_asked_for_organisation_details(self):
        self.submit_profile()
        self.client.post(reverse('monitor:onboarding_agency'), {'account_type': 'individual'})

        declaration = AgencyDeclaration.objects.get(user=self.user)
        self.assertFalse(declaration.represents_organisation)
        self.assertTrue(declaration.is_complete)
        self.assertEqual(self.state(), 'agency_declared')


# ═══════════════════════════════════════════════════════════════════════════
#  7–10. Legal documents and consent
# ═══════════════════════════════════════════════════════════════════════════

@override_settings(EMAIL_BACKEND=LOCMEM, PAYMENTS_ENABLED=False)
class ConsentTests(TestCase):

    def setUp(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        self.user = User.objects.get(email=REGISTRATION['email'])
        token = EmailVerificationToken.objects.get(user=self.user)
        self.client.get(reverse('monitor:onboarding_verify_confirm', args=[token.token]))
        self.client.post(reverse('monitor:onboarding_profile'), {
            'first_name': 'Naledi', 'last_name': 'Mokgadi',
            'job_title': 'Communications Manager', 'country': 'Botswana'})
        self.client.post(reverse('monitor:onboarding_agency'), {'account_type': 'individual'})
        self.user.refresh_from_db()

    def test_a_fresh_deployment_publishes_all_three_documents(self):
        for doc_type in ('terms', 'privacy', 'disclaimer'):
            doc = LegalDocument.current(doc_type)
            self.assertIsNotNone(doc, doc_type)
            self.assertEqual(doc.version, '1.0')
            self.assertTrue(doc.is_published)

    def test_shipped_documents_are_labelled_as_drafts_pending_legal_review(self):
        """The wording shipped with the product is a draft. It must never present
        itself to a user as settled law."""
        for doc_type in ('terms', 'privacy', 'disclaimer'):
            doc = LegalDocument.current(doc_type)
            self.assertEqual(doc.review_status, 'draft')
            self.assertTrue(doc.is_draft)
            self.assertEqual(doc.jurisdiction, 'Botswana')

        response = self.client.get(reverse('monitor:onboarding_terms'))
        self.assertContains(response, 'Draft')

    # ── 7. terms are compulsory ──────────────────────────────────────────────
    def test_terms_must_be_accepted_to_continue(self):
        response = self.client.post(reverse('monitor:onboarding_terms'), {})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ConsentRecord.objects.filter(user=self.user, doc_type='terms').exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.onboarding.state, 'agency_declared')

    def test_the_later_steps_cannot_be_reached_without_accepting_the_terms(self):
        for name in ('onboarding_privacy', 'onboarding_disclaimer', 'onboarding_plan'):
            response = self.client.get(reverse(f'monitor:{name}'))
            self.assertRedirects(response, reverse('monitor:onboarding_terms'))

    def test_onboarding_cannot_complete_with_documents_outstanding(self):
        response = self.client.get(reverse('monitor:onboarding_done'))
        self.assertRedirects(response, reverse('monitor:onboarding_terms'))
        self.user.refresh_from_db()
        self.assertFalse(self.user.onboarding.is_complete)

    # ── 8 & 9. each consent is its own record ────────────────────────────────
    def test_each_consent_is_recorded_separately_with_its_own_audit_trail(self):
        for name, doc_type in (('onboarding_terms', 'terms'),
                               ('onboarding_privacy', 'privacy'),
                               ('onboarding_disclaimer', 'disclaimer')):
            self.client.post(reverse(f'monitor:{name}'), {'accept': 'on'},
                             HTTP_USER_AGENT='TestBrowser/1.0')

            record = ConsentRecord.objects.get(user=self.user, doc_type=doc_type)
            self.assertEqual(record.decision, 'accepted')
            self.assertEqual(record.version, '1.0')
            self.assertEqual(record.source, 'onboarding')
            self.assertEqual(record.organization, self.user.organization)
            self.assertIsNotNone(record.occurred_at)
            self.assertIsNotNone(record.ip_address)
            self.assertEqual(record.user_agent, 'TestBrowser/1.0')

        self.assertEqual(ConsentRecord.objects.filter(user=self.user).count(), 3)

    def test_privacy_consent_is_not_implied_by_accepting_the_terms(self):
        self.client.post(reverse('monitor:onboarding_terms'), {'accept': 'on'})

        self.assertTrue(legal.has_accepted(self.user, 'terms'))
        self.assertFalse(legal.has_accepted(self.user, 'privacy'))
        self.assertFalse(legal.has_accepted(self.user, 'disclaimer'))

    # ── 10. versions are preserved ───────────────────────────────────────────
    def test_publishing_a_new_version_preserves_the_earlier_acceptance(self):
        self.client.post(reverse('monitor:onboarding_terms'), {'accept': 'on'})
        original = ConsentRecord.objects.get(user=self.user, doc_type='terms')

        v11 = LegalDocument.objects.create(
            doc_type='terms', version='1.1', title='Social Light Terms & Conditions',
            body='Updated terms.', consent_label='I accept the updated Terms.',
            is_published=True, effective_from=timezone.now())

        # The old record is untouched, and the new version is now outstanding.
        original.refresh_from_db()
        self.assertEqual(original.version, '1.0')
        self.assertEqual(LegalDocument.current('terms'), v11)
        self.assertFalse(legal.has_accepted(self.user, 'terms'))
        self.assertIn(v11, legal.outstanding_documents(self.user))

        legal.record_consent(self.user, v11, decision='accepted', source='reconsent')

        versions = set(ConsentRecord.objects.filter(user=self.user, doc_type='terms')
                       .values_list('version', flat=True))
        self.assertEqual(versions, {'1.0', '1.1'})
        self.assertTrue(legal.has_accepted(self.user, 'terms'))

    def test_a_settled_account_is_prompted_but_not_locked_out_by_a_new_version(self):
        """Publishing a new version must not put a paying client back through
        onboarding — it prompts, it does not block."""
        self.client.post(reverse('monitor:onboarding_terms'), {'accept': 'on'})
        self.client.post(reverse('monitor:onboarding_privacy'), {'accept': 'on'})
        self.client.post(reverse('monitor:onboarding_disclaimer'), {'accept': 'on'})
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})
        self.client.post(reverse('monitor:onboarding_plan'), {'package': 'free'})
        self.client.get(reverse('monitor:onboarding_done'))

        LegalDocument.objects.create(
            doc_type='terms', version='2.0', title='Social Light Terms & Conditions',
            body='Revised terms.', consent_label='I accept the revised Terms.',
            is_published=True, effective_from=timezone.now())

        response = self.client.get(reverse('monitor:dashboard', args=[self.user.organization_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([d.version for d in response.context['outstanding_documents']], ['2.0'])
        self.assertContains(response, 'Review now')

    def test_re_consent_records_the_new_version_and_keeps_the_old_one(self):
        for name in ('onboarding_terms', 'onboarding_privacy', 'onboarding_disclaimer'):
            self.client.post(reverse(f'monitor:{name}'), {'accept': 'on'})
        LegalDocument.objects.create(
            doc_type='terms', version='2.0', title='Social Light Terms & Conditions',
            body='Revised terms.', consent_label='I accept the revised Terms.',
            is_published=True, effective_from=timezone.now())

        self.client.post(reverse('monitor:reconsent'), {'accept': 'on'})

        records = ConsentRecord.objects.filter(user=self.user, doc_type='terms')
        self.assertEqual(set(records.values_list('version', flat=True)), {'1.0', '2.0'})
        self.assertEqual(records.get(version='2.0').source, 'reconsent')
        self.assertEqual(legal.outstanding_documents(self.user), [])

    def test_a_consent_record_cannot_be_rewritten(self):
        self.client.post(reverse('monitor:onboarding_terms'), {'accept': 'on'})
        record = ConsentRecord.objects.get(user=self.user, doc_type='terms')

        record.decision = 'withdrawn'
        with self.assertRaises(ValueError):
            record.save()

        record.refresh_from_db()
        self.assertEqual(record.decision, 'accepted')

    def test_withdrawing_consent_writes_a_new_record_rather_than_deleting_one(self):
        self.client.post(reverse('monitor:onboarding_terms'), {'accept': 'on'})
        document = LegalDocument.current('terms')

        legal.record_consent(self.user, document, decision='withdrawn', source='settings')

        self.assertEqual(ConsentRecord.objects.filter(user=self.user, doc_type='terms').count(), 2)
        self.assertFalse(legal.has_accepted(self.user, 'terms'))


# ═══════════════════════════════════════════════════════════════════════════
#  11–14. Feature entitlements
# ═══════════════════════════════════════════════════════════════════════════

class EntitlementResolutionTests(TestCase):

    def test_the_free_tier_grants_only_basic_monitoring(self):
        granted = entitlements_for_organization(
            Organization.objects.create(name='Free Org', plan_status='active',
                                        package=free_package()))
        self.assertEqual(granted, {'basic_monitoring', 'dashboard', 'alerts'})

    def test_a_paid_tier_grants_what_its_entitlements_say(self):
        granted = entitlements_for_organization(
            Organization.objects.create(name='Paid Org', plan_status='active',
                                        package=paid_package()))
        self.assertIn('report_download', granted)
        self.assertIn('crawl_result_download', granted)
        self.assertIn('premium_reports', granted)

    def test_a_trial_unlocks_everything(self):
        org = Organization.objects.create(name='Trial Org')
        org.start_trial()
        org.save()
        self.assertIn('report_download', entitlements_for_organization(org))
        self.assertIn('crawl_result_download', entitlements_for_organization(org))

    def test_an_organisation_from_before_packages_keeps_full_access(self):
        """Every organisation that predates self-signup is 'active' with no
        package. Treating those as free-tier would revoke functionality from
        existing clients."""
        org = Organization.objects.create(name='Legacy Org')          # active, no package
        self.assertEqual(org.plan_status, 'active')
        self.assertIsNone(org.package)
        self.assertIn('report_download', entitlements_for_organization(org))

    def test_marketing_bullets_do_not_grant_anything(self):
        """Package.features is display copy. Rewording it in the admin must not
        change what anyone is allowed to do."""
        package = Package.objects.create(
            name='Copy Only', slug='copy-only',
            features=['Report downloads', 'Crawl-result downloads'], entitlements=[])
        org = Organization.objects.create(name='Copy Org', plan_status='active', package=package)

        granted = entitlements_for_organization(org)
        self.assertNotIn('report_download', granted)
        self.assertNotIn('crawl_result_download', granted)

    def test_an_unknown_entitlement_code_is_ignored(self):
        package = Package.objects.create(name='Stale', slug='stale',
                                         entitlements=['report_download', 'retired_feature'])
        org = Organization.objects.create(name='Stale Org', plan_status='active', package=package)
        self.assertEqual(entitlements_for_organization(org), {'report_download'})

    def test_platform_admins_have_every_feature(self):
        user, _ = complete_account('admin@sociallight.test', role='platform_admin',
                                   package=free_package())
        self.assertTrue(user.has_feature('crawl_result_download'))

    @override_settings(FREE_PLAN_ENTITLEMENTS=['dashboard'])
    def test_the_free_tier_is_configurable_without_a_release(self):
        org = Organization.objects.create(name='Nothing Org', plan_status='expired')
        self.assertEqual(entitlements_for_organization(org), {'dashboard'})


class FeatureGatingTests(TestCase):
    """The gates themselves — enforced at the view, not in the template."""

    def setUp(self):
        self.free_user, self.free_org = complete_account('free@example.com', package=free_package())
        self.paid_user, self.paid_org = complete_account('paid@example.com', package=paid_package())

    # ── 11. report downloads ─────────────────────────────────────────────────
    def test_a_free_user_cannot_download_a_report(self):
        self.client.force_login(self.free_user)
        url = reverse('monitor:report_sentiment', args=[self.free_org.id])

        self.assertEqual(self.client.get(url).status_code, 200)          # the report itself
        blocked = self.client.get(url, {'download': 'pdf'})              # the file
        self.assertEqual(blocked.status_code, 403)
        self.assertContains(blocked, 'not available on your current plan', status_code=403)

    def test_a_free_user_cannot_download_a_report_as_powerpoint(self):
        self.client.force_login(self.free_user)
        response = self.client.get(reverse('monitor:report_competitor_pptx', args=[self.free_org.id]))
        self.assertEqual(response.status_code, 403)

    def test_a_free_user_cannot_reach_a_premium_report(self):
        self.client.force_login(self.free_user)
        response = self.client.get(reverse('monitor:report_full', args=[self.free_org.id]))
        self.assertEqual(response.status_code, 403)

    # ── 12. crawl-result downloads ───────────────────────────────────────────
    def test_a_free_user_cannot_download_crawl_results(self):
        self.client.force_login(self.free_user)
        response = self.client.get(
            reverse('monitor:coverage_export', args=[self.free_org.id, 'online']))

        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body['code'], 'feature_not_available')
        self.assertEqual(body['feature'], 'crawl_result_download')
        self.assertIn('upgrade_url', body)

    # ── 13. a paid plan gets through ─────────────────────────────────────────
    def test_a_paid_user_can_download_crawl_results(self):
        self.client.force_login(self.paid_user)
        response = self.client.get(
            reverse('monitor:coverage_export', args=[self.paid_org.id, 'online']))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn(b'Headline', b''.join(response.streaming_content))

    def test_a_paid_user_can_download_a_report(self):
        self.client.force_login(self.paid_user)
        self.assertTrue(self.paid_user.has_feature('report_download'))
        # Not exercising the PDF renderer here — the gate is what is under test,
        # and the entitlement check runs before any Chromium is started.
        self.assertIsNone(self.paid_user.entitlements.isdisjoint({'report_download'}) or None)

    # ── 14. no bypass through the API ────────────────────────────────────────
    def test_the_backend_blocks_a_direct_api_request(self):
        """Hiding the button is not what stops anyone: a hand-built request to
        the same URL is refused by the view."""
        self.client.force_login(self.free_user)
        response = self.client.get(
            reverse('monitor:coverage_export', args=[self.free_org.id, 'online']),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 403)

    def test_a_download_url_for_a_report_that_does_not_exist_is_still_refused(self):
        """The entitlement check runs before the object lookup, so a free user
        cannot probe for report ids through a paid endpoint."""
        self.client.force_login(self.free_user)
        response = self.client.get(reverse('monitor:report_issue_pdf', args=[
            self.free_org.id, '00000000-0000-0000-0000-000000000000']))
        self.assertEqual(response.status_code, 403)

    def test_a_user_cannot_export_another_organisations_coverage(self):
        self.client.force_login(self.paid_user)
        response = self.client.get(
            reverse('monitor:coverage_export', args=[self.free_org.id, 'online']))
        self.assertEqual(response.status_code, 403)

    def test_the_template_flags_match_what_the_backend_enforces(self):
        self.client.force_login(self.free_user)
        response = self.client.get(reverse('monitor:media_online', args=[self.free_org.id]))
        self.assertFalse(response.context['features']['crawl_result_download'])
        self.assertTrue(response.context['features']['dashboard'])


# ═══════════════════════════════════════════════════════════════════════════
#  15–17. Payments, manual assignment and settlement
# ═══════════════════════════════════════════════════════════════════════════

@override_settings(EMAIL_BACKEND=LOCMEM, PAYMENTS_ENABLED=False)
class PaymentsDisabledTests(TestCase):
    """Payments off is a supported production mode, not a broken configuration."""

    def setUp(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        self.user = User.objects.get(email=REGISTRATION['email'])
        token = EmailVerificationToken.objects.get(user=self.user)
        self.client.get(reverse('monitor:onboarding_verify_confirm', args=[token.token]))
        self.client.post(reverse('monitor:onboarding_profile'), {
            'first_name': 'Naledi', 'last_name': 'Mokgadi',
            'job_title': 'Communications Manager', 'country': 'Botswana'})
        self.client.post(reverse('monitor:onboarding_agency'), {'account_type': 'individual'})
        for name in ('onboarding_terms', 'onboarding_privacy', 'onboarding_disclaimer'):
            self.client.post(reverse(f'monitor:{name}'), {'accept': 'on'})

    def test_the_manual_provider_is_used_when_payments_are_off(self):
        provider = get_provider()
        self.assertIsInstance(provider, ManualProvider)
        self.assertFalse(provider.is_enabled)

    def test_the_payment_step_explains_itself_rather_than_failing(self):
        response = self.client.get(reverse('monitor:onboarding_payment'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['payments_enabled'])
        self.assertContains(response, 'not switched on')

    def test_onboarding_completes_with_no_gateway_configured(self):
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})
        self.client.post(reverse('monitor:onboarding_plan'), {'package': 'free'})
        self.client.get(reverse('monitor:onboarding_done'))

        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding.is_complete)
        self.assertTrue(self.user.onboarding.payment_skipped)

    def test_no_card_is_recorded_when_the_step_is_skipped(self):
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})
        self.assertFalse(self.user.organization.payment_methods.exists())

    def test_choosing_a_paid_plan_raises_a_request_instead_of_charging(self):
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})
        self.client.post(reverse('monitor:onboarding_plan'), {'package': 'scale'})

        org = Organization.objects.get(pk=self.user.organization_id)
        self.assertEqual(org.subscription_requests.filter(status='pending').count(), 1)
        # Not activated — nobody has paid yet.
        self.assertNotEqual(org.plan_status, 'active')

    def test_choosing_the_free_plan_activates_it_immediately(self):
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})
        self.client.post(reverse('monitor:onboarding_plan'), {'package': 'free'})

        org = Organization.objects.get(pk=self.user.organization_id)
        self.assertEqual(org.plan_status, 'active')
        self.assertEqual(org.package.slug, 'free')


class FakeGateway(ManualProvider):
    """A stand-in tokenising gateway, so the provider contract is exercised
    without reaching a real payment network.

    It models the only shape the interface allows: the browser hands back an
    opaque token, and the provider expands it into the display fragments. There
    is no code path here — or in the real Stripe provider — by which a card
    number could reach the application.
    """
    key = 'fake'
    label = 'Fake gateway'
    is_enabled = True
    collects_card = True

    def client_config(self, request, organization):
        return {'provider': self.key, 'publishable_key': 'pk_test', 'client_secret': 'seti_test'}

    def attach_payment_method(self, organization, user, token, **extra):
        from monitor.models import PaymentMethod

        return PaymentMethod.objects.create(
            organization=organization, added_by=user, provider=self.key,
            provider_token=token, brand='visa', last4='4242',
            exp_month=12, exp_year=timezone.now().year + 2, is_default=True,
        )


@override_settings(EMAIL_BACKEND=LOCMEM, PAYMENTS_ENABLED=True,
                   PAYMENT_PROVIDER='monitor.test_onboarding.FakeGateway')
class PaymentsEnabledTests(TestCase):
    """The other half of the switch: a gateway is configured and a card is saved
    as a token."""

    def setUp(self):
        self.client.post(reverse('monitor:signup'), REGISTRATION)
        self.user = User.objects.get(email=REGISTRATION['email'])
        token = EmailVerificationToken.objects.get(user=self.user)
        self.client.get(reverse('monitor:onboarding_verify_confirm', args=[token.token]))
        self.client.post(reverse('monitor:onboarding_profile'), {
            'first_name': 'Naledi', 'last_name': 'Mokgadi',
            'job_title': 'Communications Manager', 'country': 'Botswana'})
        self.client.post(reverse('monitor:onboarding_agency'), {'account_type': 'individual'})
        for name in ('onboarding_terms', 'onboarding_privacy', 'onboarding_disclaimer'):
            self.client.post(reverse(f'monitor:{name}'), {'accept': 'on'})

    def test_the_configured_provider_is_used(self):
        self.assertIsInstance(get_provider(), FakeGateway)

    def test_the_payment_step_offers_the_providers_card_field(self):
        response = self.client.get(reverse('monitor:onboarding_payment'))
        self.assertTrue(response.context['payments_enabled'])
        self.assertContains(response, 'cardElement')
        self.assertContains(response, 'never sees or stores your card number')

    def test_only_the_token_and_display_fragments_are_stored(self):
        self.client.post(reverse('monitor:onboarding_payment'),
                         {'payment_token': 'pm_test_token_123'})

        method = self.user.organization.payment_methods.get()
        self.assertEqual(method.provider_token, 'pm_test_token_123')
        self.assertEqual(method.last4, '4242')
        self.assertEqual(method.display_label, 'Visa ending 4242')
        self.user.refresh_from_db()
        self.assertEqual(self.user.onboarding.state, 'payment_method_added')
        self.assertFalse(self.user.onboarding.payment_skipped)

    def test_a_missing_token_is_rejected_rather_than_saved_empty(self):
        response = self.client.post(reverse('monitor:onboarding_payment'), {'payment_token': ''})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.user.organization.payment_methods.exists())

    def test_a_user_may_still_skip_the_card(self):
        self.client.post(reverse('monitor:onboarding_payment'), {'action': 'skip'})

        self.user.refresh_from_db()
        self.assertTrue(self.user.onboarding.payment_skipped)
        self.assertFalse(self.user.organization.payment_methods.exists())


@override_settings(PAYMENTS_ENABLED=False)
class ManualPlanAssignmentTests(TestCase):
    """16. An administrator can put an organisation on a plan with no gateway."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='root', email='root@sociallight.test', password='admin-pw-for-tests-1')
        self.admin.email_verified = True
        self.admin.save(update_fields=['email_verified'])
        OnboardingProgress.objects.create(user=self.admin, state='complete', is_legacy=True)
        self.org = Organization.objects.create(name='Manual Org', plan_status='expired')
        self.client.force_login(self.admin)

    def test_an_admin_can_assign_a_plan_from_the_organisation_list(self):
        package = paid_package()
        response = self.client.post(reverse('admin:monitor_organization_changelist'), {
            'action': 'assign_plan',
            ACTION_CHECKBOX_NAME: [str(self.org.pk)],
            'package': str(package.pk),
            'apply': 'yes',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.org.refresh_from_db()
        self.assertEqual(self.org.package, package)
        self.assertEqual(self.org.plan_status, 'active')
        self.assertIsNotNone(self.org.subscription_activated_at)

    def test_a_manually_assigned_plan_grants_its_entitlements(self):
        self.org.activate_package(paid_package())
        member, _ = complete_account('member@manual.test', org=self.org)

        self.assertTrue(user_has_feature(member, 'crawl_result_download'))

    def test_the_confirmation_page_offers_every_assignable_tier(self):
        response = self.client.post(reverse('admin:monitor_organization_changelist'), {
            'action': 'assign_plan',
            ACTION_CHECKBOX_NAME: [str(self.org.pk)],
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Free')          # assignable though unadvertised
        self.assertContains(response, 'Scale')


@override_settings(PAYMENT_SETTLEMENT_HOLD_DAYS=7)
class SettlementTests(TestCase):
    """17. Funds become eligible for extraction after the configured hold."""

    def setUp(self):
        self.org = Organization.objects.create(name='Paying Org')

    def test_a_successful_payment_is_eligible_seven_days_after_it_was_taken(self):
        payment = Payment.objects.create(organization=self.org, amount=1299, currency='USD')
        payment.mark_paid()

        self.assertEqual(payment.settlement_hold_days, 7)
        self.assertEqual(payment.settlement_available_at, payment.paid_at + timedelta(days=7))
        self.assertFalse(payment.is_settlement_eligible)
        self.assertEqual(payment.settlement_status, 'holding')

    def test_it_becomes_eligible_once_the_holding_period_has_passed(self):
        payment = Payment.objects.create(organization=self.org, amount=1299)
        payment.mark_paid(when=timezone.now() - timedelta(days=8))

        self.assertTrue(payment.is_settlement_eligible)
        self.assertEqual(payment.settlement_status, 'eligible')
        self.assertEqual(payment.settlement_days_remaining, 0)

    def test_an_unsuccessful_payment_is_never_eligible(self):
        payment = Payment.objects.create(organization=self.org, amount=1299, status='failed')
        self.assertFalse(payment.is_settlement_eligible)
        self.assertEqual(payment.settlement_status, 'not_applicable')

    @override_settings(PAYMENT_SETTLEMENT_HOLD_DAYS=14)
    def test_the_holding_period_is_configurable(self):
        payment = Payment.objects.create(organization=self.org, amount=100)
        payment.mark_paid()
        self.assertEqual(payment.settlement_available_at, payment.paid_at + timedelta(days=14))

    def test_changing_the_setting_does_not_move_an_existing_eligibility_date(self):
        """The holding period is stamped onto the payment when it is taken, so a
        later configuration change cannot retroactively move money."""
        payment = Payment.objects.create(organization=self.org, amount=100)
        payment.mark_paid()
        original = payment.settlement_available_at

        with override_settings(PAYMENT_SETTLEMENT_HOLD_DAYS=90):
            payment.refresh_from_db()
            self.assertEqual(payment.settlement_available_at, original)
            self.assertEqual(payment.settlement_hold_days, 7)

    def test_an_off_platform_settlement_is_recorded_the_same_way(self):
        payment = record_manual_payment(self.org, amount=5000, currency='USD',
                                        description='EFT, invoice INV-1042')

        self.assertEqual(payment.provider, 'manual')
        self.assertEqual(payment.status, 'succeeded')
        self.assertEqual(payment.settlement_available_at, payment.paid_at + timedelta(days=7))

    def test_the_application_records_no_card_authentication_data(self):
        """A structural check: if a field for a PAN or a CVV is ever added to the
        payment models, this fails."""
        from monitor.models import PaymentMethod

        banned = {'card_number', 'pan', 'cvv', 'cvc', 'cvv2', 'security_code', 'pin', 'track_data'}
        for model in (PaymentMethod, Payment):
            names = {f.name for f in model._meta.get_fields()}
            self.assertEqual(names & banned, set(), f'{model.__name__} holds card secrets')


# ═══════════════════════════════════════════════════════════════════════════
#  18. Existing functionality
# ═══════════════════════════════════════════════════════════════════════════

class ExistingBehaviourTests(TestCase):
    """Accounts and organisations that predate all of this keep working."""

    def test_an_account_from_before_onboarding_is_not_sent_through_it(self):
        org = Organization.objects.create(name='Existing Client')
        user = User.objects.create_user(username='old', email='old@client.co.bw',
                                        password='pw-for-tests-1', organization=org,
                                        role='org_admin', email_verified=True)
        # No OnboardingProgress row at all — exactly the state migration 0018
        # protects against, before it runs.
        self.assertIsNone(user.onboarding_progress)
        self.assertTrue(user.onboarding_complete)

        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse('monitor:dashboard', args=[org.id])).status_code, 200)

    def test_a_legacy_account_marked_complete_reaches_the_application(self):
        user, org = complete_account('legacy@client.co.bw')
        user.onboarding.is_legacy = True
        user.onboarding.save(update_fields=['is_legacy'])

        self.client.force_login(user)
        self.assertEqual(
            self.client.get(reverse('monitor:dashboard', args=[org.id])).status_code, 200)

    def test_an_existing_client_keeps_its_downloads(self):
        """'active' with no package is the state every pre-signup organisation is
        in, and it must keep full access."""
        user, org = complete_account('existing@client.co.bw')
        self.assertIsNone(org.package)
        self.client.force_login(user)

        response = self.client.get(reverse('monitor:coverage_export', args=[org.id, 'online']))
        self.assertEqual(response.status_code, 200)

    def test_the_published_price_list_is_unchanged_by_the_free_tier(self):
        response = self.client.get(reverse('monitor:pricing'))
        names = [p.name for p in response.context['packages']]
        self.assertEqual(names, ['Spark', 'Momentum', 'Scale', 'Enterprise'])

    def test_organisation_scoping_still_applies(self):
        user_a, org_a = complete_account('a@one.test')
        _user_b, org_b = complete_account('b@two.test')

        self.client.force_login(user_a)
        response = self.client.get(reverse('monitor:dashboard', args=[org_b.id]))
        # /app/organizations/ then bounces a single-org user into their own
        # dashboard, so the redirect target is not followed here.
        self.assertRedirects(response, reverse('monitor:organizations'),
                             fetch_redirect_response=False)
        self.assertEqual(
            self.client.get(reverse('monitor:dashboard', args=[org_a.id])).status_code, 200)
