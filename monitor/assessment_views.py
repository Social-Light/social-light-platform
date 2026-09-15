"""The public media intelligence assessment.

Three views. One renders the page, one accepts a completed assessment, one
records that the visitor asked for the follow-up they were offered.

The page itself is a single template with the questions embedded as JSON, so
answering ten questions costs no round trips. The submission at the end is the
only write, and it is scored on the server: the browser may compute a score to
show while the visitor works through the questions, but it never gets to decide
what is stored.

Contact details are asked for after the last question rather than before the
first. The existing free assessment on the landing page promises no sign-up, and
a visitor who has answered ten questions is both more likely to hand over an
address and worth more when they do.
"""
import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import assessment, assessment_email, attribution, meta_pixel
from .assessment_models import ACTION_CHOICES, AssessmentSubmission

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ('first_name', 'last_name', 'email', 'company', 'industry')
OPTIONAL_FIELDS = ('role', 'country')

# Generous, but not unbounded. Everything here is typed by a stranger into a
# public form, and a field with no ceiling is an invitation.
MAX_FIELD_LENGTH = 200
MAX_NOTE_LENGTH = 4000


def assessment_page(request):
    return render(request, 'monitor/assessment.html', {
        'questions': assessment.QUESTIONS,
        'questions_json': json.dumps(assessment.QUESTIONS),
        'max_score': assessment.MAX_SCORE,
    })


@require_http_methods(['POST'])
def assessment_submit(request):
    """Accept a completed assessment and return the graded result.

    The row is written before either email is attempted. A mail failure then
    costs the visitor their report but not their place in the pipeline, and the
    row records that the report never went out so somebody can see it.
    """
    try:
        payload = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'Malformed submission.'}, status=400)

    contact = payload.get('contact') or {}
    answers = payload.get('answers') or {}
    if not isinstance(contact, dict) or not isinstance(answers, dict):
        return JsonResponse({'error': 'Malformed submission.'}, status=400)

    cleaned, error = _clean_contact(contact)
    if error:
        return JsonResponse({'error': error}, status=400)

    result = assessment.score(answers)
    submission = AssessmentSubmission.objects.create(
        answers=answers,
        # Where the visit began, carried through from the landing page.
        **attribution.submission_fields(request),
        note=result['note'][:MAX_NOTE_LENGTH],
        score=result['score'],
        tier=result['tier'],
        fit=result['fit'],
        urgency=result['urgency'],
        budget=result['budget'],
        timeline=result['timeline'],
        platforms=result['platforms'],
        **cleaned,
    )

    now = timezone.now()
    if assessment_email.send_report(submission):
        submission.report_sent_at = now
    if assessment_email.notify_sales(submission):
        submission.sales_notified_at = now
    submission.save(update_fields=['report_sent_at', 'sales_notified_at'])

    logger.info('Assessment %s scored %s%% (%s) for %s from %s',
                submission.pk, submission.score, submission.fit, submission.company,
                submission.source_label)

    # Report the conversion to Meta from the server as well as the browser. The
    # two carry the same event_id, so Meta counts one Lead however many arrive.
    event_id = meta_pixel.new_event_id()
    meta_pixel.send_event(
        request, 'Lead', event_id=event_id,
        event_source_url=request.build_absolute_uri('/assessment/'),
        user=meta_pixel.user_data(
            request,
            email=submission.email,
            first_name=submission.first_name,
            last_name=submission.last_name,
            country=submission.country,
        ),
        custom={
            'content_name': 'Media intelligence assessment',
            'content_category': submission.industry or 'unspecified',
            # The score and fit tier are what make one lead worth more than
            # another. Passing them lets Meta optimise toward the visitors who
            # actually qualify rather than toward whoever fills a form fastest.
            'value': float(submission.score),
            'currency': 'BWP',
            'lead_score': submission.score,
            'lead_fit': submission.fit,
        },
    )

    return JsonResponse({
        'id': str(submission.pk),
        # Handed back so the browser pixel can fire the matching Lead event.
        'event_id': event_id,
        'result': result,
        'headline': assessment.HEADLINES[result['tier']],
        'summary': assessment.SUMMARIES[result['tier']],
        'fit_message': assessment.FIT_MESSAGES[result['fit']],
        'insights': assessment.insights(result, answers),
        'cta': assessment.CALLS_TO_ACTION[result['fit']],
        # Said plainly rather than hidden: if the report did not go out, the page
        # must not tell the visitor to go and look for it.
        'report_sent': submission.report_delivered,
    })


@require_http_methods(['POST'])
def assessment_action(request, submission_id):
    """Record that the visitor asked for the follow-up they were offered."""
    submission = AssessmentSubmission.objects.filter(pk=submission_id).first()
    if submission is None:
        return JsonResponse({'error': 'Unknown submission.'}, status=404)

    try:
        action = (json.loads(request.body or '{}').get('action') or '').strip()
    except ValueError:
        return JsonResponse({'error': 'Malformed request.'}, status=400)

    if action not in dict(ACTION_CHOICES):
        return JsonResponse({'error': 'Unknown action.'}, status=400)

    submission.requested_action = action
    submission.requested_action_at = timezone.now()
    submission.save(update_fields=['requested_action', 'requested_action_at'])

    assessment_email.notify_sales(submission, requested_action=action)

    # A lead who asks for a call is worth more than one who merely finished the
    # questions, and is reported as a separate, further-down-funnel event.
    event_id = meta_pixel.new_event_id()
    meta_pixel.send_event(
        request, 'Schedule', event_id=event_id,
        user=meta_pixel.user_data(
            request,
            email=submission.email,
            first_name=submission.first_name,
            last_name=submission.last_name,
            country=submission.country,
        ),
        custom={'content_name': dict(ACTION_CHOICES)[action],
                'lead_score': submission.score,
                'lead_fit': submission.fit},
    )
    return JsonResponse({'ok': True, 'event_id': event_id})


def _clean_contact(contact):
    """Validate and trim the contact block. Returns ``(fields, error)``."""
    cleaned = {}
    for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
        value = contact.get(field)
        cleaned[field] = value.strip()[:MAX_FIELD_LENGTH] if isinstance(value, str) else ''

    missing = [f for f in REQUIRED_FIELDS if not cleaned[f]]
    if missing:
        return None, 'Please fill in all required fields.'

    email = cleaned['email']
    if '@' not in email or '.' not in email.split('@')[-1] or ' ' in email:
        return None, 'Please enter a valid email address.'

    return cleaned, None
