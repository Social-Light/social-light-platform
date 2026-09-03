"""Email for the public assessment: the report to the visitor, the lead to sales.

Both go out over whatever backend the deployment has configured, using Django's
own mail layer, exactly like every other message this platform sends. The
original brief sent these from the browser through a third-party JavaScript
service, which meant the credentials sat in the page source where any visitor
could read them and the lead existed nowhere but one inbox. Neither is acceptable
here, and neither is necessary now that mail is configured server side.

Failures are caught rather than propagated. The submission is already saved by
the time these run, and a mail outage must not cost a lead or show the visitor an
error page for something that has already succeeded. Each function reports
whether it sent, so the caller can record that on the row and someone can see
which reports never arrived.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail
from django.template.loader import render_to_string

from . import assessment
from .verification import redact_smtp_credentials

logger = logging.getLogger(__name__)


def _site_url():
    return (getattr(settings, 'SITE_URL', '') or '').rstrip('/')


def send_report(submission):
    """Email the visitor their own results. Returns True if it went out."""
    result = assessment.score(submission.answers)
    context = {
        'submission': submission,
        'result': result,
        'headline': assessment.HEADLINES[result['tier']],
        'summary': assessment.SUMMARIES[result['tier']],
        'fit_message': assessment.FIT_MESSAGES[result['fit']],
        'insights': assessment.insights(result, submission.answers),
        'site_url': _site_url(),
    }

    message = EmailMultiAlternatives(
        subject=f'Your media intelligence assessment — {submission.score}%',
        body=render_to_string('monitor/email/assessment_report.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[submission.email],
    )
    message.attach_alternative(
        render_to_string('monitor/email/assessment_report.html', context), 'text/html')

    try:
        message.send(fail_silently=False)
    except Exception as exc:
        logger.exception('Assessment report to %s failed: %s',
                         submission.email, redact_smtp_credentials(exc))
        return False
    return True


def notify_sales(submission, requested_action=''):
    """Tell the team a lead has come in. Returns True if it went out.

    Sent as plain text on purpose. This is an internal working message that
    someone reads on a phone between meetings, and every fact in it is one they
    need in order to decide whether to pick the lead up now or later.
    """
    recipients = [e for e in getattr(settings, 'SALES_NOTIFICATION_EMAILS', []) if e]
    if not recipients:
        logger.warning('Assessment lead %s not sent to sales: SALES_NOTIFICATION_EMAILS '
                       'is empty.', submission.pk)
        return False

    result = assessment.score(submission.answers)
    heading = ('Requested: ' + submission.get_requested_action_display()
               if requested_action else f'{submission.get_fit_display()} — {submission.score}%')

    lines = [
        heading,
        '',
        f'{submission.full_name} <{submission.email}>',
        f'{submission.company}' + (f' · {submission.industry}' if submission.industry else ''),
        f'Role: {submission.role or "not given"}',
        f'Country: {submission.country or "not given"}',
        '',
        f'Score: {submission.score}% ({submission.get_tier_display()})',
        f'Fit: {submission.get_fit_display()}',
        f'Budget: {result["budget"]}',
        f'Urgency: {result["urgency"]}',
        f'Timeline: {result["timeline"]}',
        f'Team size: {result["team_size"] or "not given"}',
        '',
        'Channels wanted: ' + (', '.join(result['platforms']) or 'none selected'),
        f'Biggest challenge: {result["challenge"] or "not given"}',
        f'Goal: {result["goal"] or "not given"}',
        '',
        'What they wrote:',
        submission.note or '(nothing)',
        '',
        f'Full record: {_site_url()}/admin/monitor/assessmentsubmission/{submission.pk}/change/',
    ]

    try:
        send_mail(
            subject=f'Assessment lead — {submission.company} ({submission.score}%)',
            message='\n'.join(lines),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception as exc:
        logger.exception('Assessment lead %s not sent to sales: %s',
                         submission.pk, redact_smtp_credentials(exc))
        return False
    return True
