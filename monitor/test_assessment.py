"""Tests for the public media intelligence assessment.

The assessment is a sales funnel, so most of what matters here is not the page
rendering. It is that a completed assessment becomes a durable row, that the
score stored against it was computed by the server rather than supplied by the
browser, that the visitor is never told an email went out when it did not, and
that a mail outage costs a report but never a lead.

Everything runs against Django's in-memory mail backend.
"""
import json
from unittest import mock

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from monitor import assessment
from monitor.models import AssessmentSubmission

LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'
SALES = ['sales@sociallight.africa']

CONTACT = {
    'first_name': 'Naledi',
    'last_name': 'Mokgadi',
    'email': 'naledi@ministry.co.bw',
    'company': 'Ministry of Health',
    'industry': 'Government and public sector',
    'role': 'PR and communications',
    'country': 'Botswana',
}

# A well-resourced organisation in a hurry: top of the scale nearly everywhere,
# which is what the qualification rules are meant to pick out.
STRONG_ANSWERS = {
    'setup': 4,
    'platforms': [0, 2, 3],
    'urgency': 0,
    'caught_out': 'no',
    'budget': 4,
    'timeline': 0,
    'team_size': 4,
    'challenge': 1,
    'goal': 0,
    'note': 'We had a story break last month before we saw it.',
}

# Nothing in place, no money, no hurry.
WEAK_ANSWERS = {
    'setup': 0,
    'platforms': [0],
    'urgency': 4,
    'caught_out': 'yes',
    'budget': 0,
    'timeline': 4,
    'team_size': 0,
    'challenge': 0,
    'goal': 0,
    'note': '',
}


def submit(client, answers, contact=None):
    return client.post(
        reverse('monitor:assessment_submit'),
        data=json.dumps({'contact': contact or CONTACT, 'answers': answers}),
        content_type='application/json',
    )


class ScoringTests(TestCase):
    """The scoring rules, exercised directly."""

    def test_the_maximum_is_derived_from_the_questions(self):
        """Written down by hand it would go stale the first time a scored
        question was added, silently deflating every score."""
        self.assertEqual(assessment.MAX_SCORE, 60)

    def test_the_strongest_possible_answers_score_full_marks(self):
        self.assertEqual(assessment.score(STRONG_ANSWERS)['score'], 100)

    def test_a_high_scoring_urgent_well_funded_lead_is_high_value(self):
        result = assessment.score(STRONG_ANSWERS)
        self.assertEqual(result['fit'], assessment.FIT_HIGH)
        self.assertEqual(result['tier'], assessment.TIER_STRONG)

    def test_no_budget_means_no_fit_however_urgent_the_need(self):
        """Budget gates both bands. An organisation that will not spend is not a
        lead, and saying so beats a sales call that goes nowhere."""
        answers = dict(STRONG_ANSWERS, budget=0)
        self.assertEqual(assessment.score(answers)['fit'], assessment.FIT_EARLY)

    def test_the_weakest_answers_land_in_the_early_band(self):
        result = assessment.score(WEAK_ANSWERS)
        self.assertEqual(result['fit'], assessment.FIT_EARLY)
        self.assertEqual(result['tier'], assessment.TIER_ATTENTION)

    def test_derived_bands_are_reported_alongside_the_score(self):
        result = assessment.score(STRONG_ANSWERS)
        self.assertEqual(result['urgency'], 'High')
        self.assertEqual(result['budget'], 'Enterprise')
        self.assertEqual(result['timeline'], 'Immediate')

    def test_selected_platforms_come_back_as_their_wording(self):
        result = assessment.score(STRONG_ANSWERS)
        self.assertIn('Print media and newspapers', result['platforms'])
        self.assertIn('Radio and TV broadcast', result['platforms'])

    def test_rubbish_from_a_browser_scores_low_instead_of_raising(self):
        """Everything in a submission is attacker-controlled. An out-of-range
        index or a string where an integer belongs must not be a 500."""
        for answers in ({'urgency': 99}, {'urgency': 'nine'}, {'urgency': None},
                        {'platforms': 'all of them'}, {'budget': -1}, {'caught_out': 'maybe'}):
            with self.subTest(answers=answers):
                result = assessment.score(answers)
                self.assertIsInstance(result['score'], int)
                self.assertGreaterEqual(result['score'], 0)
                self.assertLessEqual(result['score'], 100)

    def test_answers_are_keyed_by_question_not_by_position(self):
        """A stored answer keyed by index would be re-interpreted the moment a
        question was inserted, quietly rewriting past submissions."""
        for question in assessment.QUESTIONS:
            self.assertTrue(question['key'])
        self.assertEqual(len({q['key'] for q in assessment.QUESTIONS}),
                         len(assessment.QUESTIONS))


@override_settings(EMAIL_BACKEND=LOCMEM, SALES_NOTIFICATION_EMAILS=SALES)
class SubmissionTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_the_page_renders_for_a_stranger(self):
        response = self.client.get(reverse('monitor:assessment'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Free assessment')

    def test_the_page_carries_the_questions_for_the_browser(self):
        response = self.client.get(reverse('monitor:assessment'))
        self.assertContains(response, assessment.QUESTIONS[0]['text'])

    def test_no_unrendered_template_syntax_reaches_the_page(self):
        """A multi-line {# … #} is not a Django comment — it is only a comment
        on one line, and across several it renders literally, notes to the
        content team and all. The same mistake was caught once already on the
        verify step; this stops it recurring here."""
        body = self.client.get(reverse('monitor:assessment')).content.decode()

        for marker in ('{#', '#}', '{%', '%}', 'TODO('):
            self.assertNotIn(marker, body,
                             f'unrendered template syntax {marker!r} on the page')

    def test_a_completed_assessment_is_stored(self):
        response = submit(self.client, STRONG_ANSWERS)

        self.assertEqual(response.status_code, 200)
        submission = AssessmentSubmission.objects.get()
        self.assertEqual(submission.email, CONTACT['email'])
        self.assertEqual(submission.company, 'Ministry of Health')
        self.assertEqual(submission.score, 100)
        self.assertEqual(submission.fit, assessment.FIT_HIGH)
        self.assertEqual(submission.answers, STRONG_ANSWERS)

    def test_the_free_text_note_is_kept(self):
        submit(self.client, STRONG_ANSWERS)
        self.assertIn('story break', AssessmentSubmission.objects.get().note)

    def test_the_score_is_the_servers_and_not_the_browsers(self):
        """A number posted alongside the answers must be ignored outright. It is
        the one field a lead could otherwise inflate about itself."""
        payload = json.dumps({'contact': CONTACT, 'answers': WEAK_ANSWERS,
                              'score': 100, 'fit': 'high_value'})
        self.client.post(reverse('monitor:assessment_submit'), data=payload,
                         content_type='application/json')

        submission = AssessmentSubmission.objects.get()
        self.assertLess(submission.score, 50)
        self.assertEqual(submission.fit, assessment.FIT_EARLY)

    def test_the_response_carries_everything_the_page_needs(self):
        data = submit(self.client, STRONG_ANSWERS).json()
        for key in ('id', 'result', 'headline', 'summary', 'fit_message', 'insights',
                    'cta', 'report_sent'):
            self.assertIn(key, data)
        self.assertEqual(data['cta']['action'], 'call')

    def test_missing_required_details_are_refused(self):
        for field in ('first_name', 'last_name', 'email', 'company', 'industry'):
            with self.subTest(field=field):
                response = submit(self.client, STRONG_ANSWERS,
                                  contact=dict(CONTACT, **{field: ''}))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(AssessmentSubmission.objects.count(), 0)

    def test_an_invalid_email_address_is_refused(self):
        response = submit(self.client, STRONG_ANSWERS, contact=dict(CONTACT, email='naledi'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AssessmentSubmission.objects.count(), 0)

    def test_a_malformed_body_is_a_bad_request_not_a_crash(self):
        response = self.client.post(reverse('monitor:assessment_submit'),
                                    data='not json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_the_endpoint_refuses_a_get(self):
        self.assertEqual(self.client.get(reverse('monitor:assessment_submit')).status_code, 405)


@override_settings(EMAIL_BACKEND=LOCMEM, SALES_NOTIFICATION_EMAILS=SALES)
class EmailTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_the_visitor_gets_their_report(self):
        submit(self.client, STRONG_ANSWERS)
        reports = [m for m in mail.outbox if m.to == [CONTACT['email']]]
        self.assertEqual(len(reports), 1)
        self.assertIn('100%', reports[0].subject)

    def test_the_report_states_the_score_and_what_they_answered(self):
        submit(self.client, STRONG_ANSWERS)
        report = [m for m in mail.outbox if m.to == [CONTACT['email']]][0]
        self.assertIn('100%', report.body)
        self.assertIn('Print media and newspapers', report.body)
        self.assertIn('100%', report.alternatives[0][0])

    def test_sales_are_told_about_the_lead(self):
        submit(self.client, STRONG_ANSWERS)
        notices = [m for m in mail.outbox if m.to == SALES]
        self.assertEqual(len(notices), 1)
        self.assertIn('Ministry of Health', notices[0].subject)
        self.assertIn('naledi@ministry.co.bw', notices[0].body)

    def test_delivery_is_recorded_on_the_row(self):
        submit(self.client, STRONG_ANSWERS)
        submission = AssessmentSubmission.objects.get()
        self.assertIsNotNone(submission.report_sent_at)
        self.assertIsNotNone(submission.sales_notified_at)

    def test_nothing_goes_to_sales_when_no_address_is_configured(self):
        with override_settings(SALES_NOTIFICATION_EMAILS=[]):
            submit(self.client, STRONG_ANSWERS)
        self.assertIsNone(AssessmentSubmission.objects.get().sales_notified_at)
        self.assertEqual([m for m in mail.outbox if m.to == SALES], [])

    def test_the_lead_survives_a_mail_outage(self):
        """The row is written before either send is attempted, so an outage
        costs the visitor their copy of the report and nothing else."""
        with mock.patch('django.core.mail.backends.locmem.EmailBackend.send_messages',
                        side_effect=OSError('mail server unreachable')):
            response = submit(self.client, STRONG_ANSWERS)

        self.assertEqual(response.status_code, 200)
        submission = AssessmentSubmission.objects.get()
        self.assertEqual(submission.score, 100)
        self.assertIsNone(submission.report_sent_at)
        self.assertIsNone(submission.sales_notified_at)

    def test_the_page_is_told_the_report_did_not_go_out(self):
        """It must not say "check your inbox" for an email that never left."""
        with mock.patch('django.core.mail.backends.locmem.EmailBackend.send_messages',
                        side_effect=OSError('mail server unreachable')):
            data = submit(self.client, STRONG_ANSWERS).json()
        self.assertFalse(data['report_sent'])

    def test_the_page_is_told_when_the_report_did_go_out(self):
        self.assertTrue(submit(self.client, STRONG_ANSWERS).json()['report_sent'])


@override_settings(EMAIL_BACKEND=LOCMEM, SALES_NOTIFICATION_EMAILS=SALES)
class FollowUpTests(TestCase):
    """The call to action at the end of the report does something real."""

    def setUp(self):
        self.client = Client()
        self.submission_id = submit(self.client, STRONG_ANSWERS).json()['id']
        mail.outbox.clear()

    def _request(self, action):
        return self.client.post(
            reverse('monitor:assessment_action', args=[self.submission_id]),
            data=json.dumps({'action': action}), content_type='application/json')

    def test_a_requested_call_is_recorded_against_the_lead(self):
        response = self._request('call')

        self.assertEqual(response.status_code, 200)
        submission = AssessmentSubmission.objects.get()
        self.assertEqual(submission.requested_action, 'call')
        self.assertIsNotNone(submission.requested_action_at)

    def test_sales_are_told_when_someone_asks_to_be_contacted(self):
        self._request('call')
        notices = [m for m in mail.outbox if m.to == SALES]
        self.assertEqual(len(notices), 1)
        self.assertIn('Discovery call', notices[0].body)

    def test_an_unknown_action_is_refused(self):
        self.assertEqual(self._request('free-holiday').status_code, 400)
        self.assertEqual(AssessmentSubmission.objects.get().requested_action, '')

    def test_an_unknown_submission_is_a_404(self):
        response = self.client.post(
            reverse('monitor:assessment_action',
                    args=['00000000-0000-0000-0000-000000000000']),
            data=json.dumps({'action': 'call'}), content_type='application/json')
        self.assertEqual(response.status_code, 404)


class ReachabilityTests(TestCase):
    """The assessment is public marketing and has to stay reachable."""

    def test_the_landing_page_links_to_it(self):
        response = self.client.get(reverse('monitor:home'))
        self.assertContains(response, reverse('monitor:assessment'))

    def test_a_signed_in_but_unverified_account_is_not_bounced_away(self):
        """It is a campaign landing page. Someone part-way through signup who
        follows a link to it should see it, not be redirected to their next
        onboarding step."""
        from monitor.models import User

        user = User.objects.create_user(username='half@example.com', email='half@example.com',
                                        password='pw-for-tests-1')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('monitor:assessment')).status_code, 200)


@override_settings(EMAIL_BACKEND=LOCMEM, SALES_NOTIFICATION_EMAILS=SALES)
class AdminTests(TestCase):
    """The team works these leads from the admin, so the admin has to open.

    The answer sheet in particular renders every stored answer back through the
    question definitions, and a stored index that no longer has an option behind
    it must not take the page down.
    """

    def setUp(self):
        from monitor.models import User

        self.client = Client()
        staff = User.objects.create_superuser(username='admin@example.com',
                                              email='admin@example.com',
                                              password='pw-for-tests-1')
        staff.email_verified = True
        staff.save(update_fields=['email_verified'])
        self.client.force_login(staff)
        submit(Client(), STRONG_ANSWERS)
        self.submission = AssessmentSubmission.objects.get()

    def test_the_lead_list_opens(self):
        response = self.client.get('/admin/monitor/assessmentsubmission/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ministry of Health')

    def test_a_lead_opens_and_shows_the_answers_as_the_visitor_saw_them(self):
        response = self.client.get(
            f'/admin/monitor/assessmentsubmission/{self.submission.pk}/change/')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # A fragment without an apostrophe: the rendered page escapes them, so
        # matching the question verbatim would fail on the escaping rather than
        # on the question being absent.
        self.assertIn('current media monitoring setup?', body)
        self.assertIn('We have a fully integrated, real-time monitoring system', body)

    def test_an_answer_with_no_option_behind_it_does_not_break_the_page(self):
        self.submission.answers = dict(STRONG_ANSWERS, setup=99, urgency='nonsense')
        self.submission.save(update_fields=['answers'])

        response = self.client.get(
            f'/admin/monitor/assessmentsubmission/{self.submission.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_leads_cannot_be_created_by_hand(self):
        """They are a record of what somebody actually answered. A hand-typed one
        would carry a score that nothing computed."""
        response = self.client.get('/admin/monitor/assessmentsubmission/add/')
        self.assertEqual(response.status_code, 403)
