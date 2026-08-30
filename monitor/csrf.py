"""A CSRF failure page that says what actually went wrong.

Django's built-in CSRF failure response is a debug page in development and a bare
"403 Forbidden" in production. Neither helps: the user is told their submission
was rejected but not that reloading the page fixes it, and whoever is debugging
gets a reason string that is ambiguous.

That ambiguity is the real problem. ``"CSRF token missing."`` is raised for two
completely different situations, and the message does not distinguish them:

1. The submitted form genuinely had no ``csrfmiddlewaretoken`` field — a stale
   page, a hand-built request, or a form missing ``{% csrf_token %}``.
2. Django could not read the request body at all. If the connection breaks while
   the POST is being read, ``request.POST`` raises ``UnreadablePostError``, which
   the middleware swallows and reports as the *same* "token missing" message —
   see ``django/middleware/csrf.py``, the ``except UnreadablePostError: pass``
   branch. The form was fine; the network was not.

So this view logs the facts that tell the two apart — whether the body was
readable, what Content-Length claimed, how many bytes actually arrived, and which
fields made it — and shows the user something more useful than a 403.
"""
import logging

from django.conf import settings
from django.http import UnreadablePostError
from django.shortcuts import render
from django.views.decorators.csrf import requires_csrf_token

logger = logging.getLogger(__name__)


def _diagnose(request):
    """Everything relevant about the rejected request, gathered defensively —
    every access here can raise on a half-read request, and a diagnostic that
    crashes tells you nothing."""
    facts = {
        'path': request.path,
        'content_length': request.META.get('CONTENT_LENGTH', '(none)'),
        'content_type': request.META.get('CONTENT_TYPE', '(none)'),
        'has_csrf_cookie': 'csrftoken' in request.COOKIES,
        'has_csrf_header': 'HTTP_X_CSRFTOKEN' in request.META,
        'referer': request.META.get('HTTP_REFERER', '(none)'),
        'origin': request.META.get('HTTP_ORIGIN', '(none)'),
        'user_agent': request.META.get('HTTP_USER_AGENT', '(none)')[:120],
    }

    try:
        posted = request.POST
    except UnreadablePostError:
        # The decisive case: the form was probably fine and the connection died.
        facts['body_readable'] = False
        facts['posted_fields'] = '(body could not be read)'
        facts['likely_cause'] = ('the connection dropped before the request body '
                                 'finished arriving, NOT a missing token')
    except Exception as exc:
        facts['body_readable'] = False
        facts['posted_fields'] = f'(unreadable: {exc.__class__.__name__})'
        facts['likely_cause'] = 'the request body could not be parsed'
    else:
        facts['body_readable'] = True
        facts['posted_fields'] = sorted(posted.keys())
        facts['token_in_post'] = bool(posted.get('csrfmiddlewaretoken'))
        if facts['token_in_post']:
            facts['likely_cause'] = 'a token was sent but did not match the cookie'
        elif not facts['has_csrf_cookie']:
            facts['likely_cause'] = 'no CSRF cookie - cookies may be blocked for this site'
        elif not posted.keys():
            facts['likely_cause'] = 'the request body was empty'
        else:
            facts['likely_cause'] = ('the form submitted other fields but no token - '
                                     'a stale page, or a form missing {% csrf_token %}')
    return facts


@requires_csrf_token
def csrf_failure(request, reason=''):
    """Wired up by the CSRF_FAILURE_VIEW setting.

    ``@requires_csrf_token`` lets this page render its own working form/token, so
    the "reload and try again" link it offers actually recovers the session
    rather than looping the user through the same rejection.
    """
    facts = _diagnose(request)
    logger.warning(
        'CSRF failure on %s | reason=%r | likely cause: %s | body readable: %s | '
        'fields: %s | csrf cookie: %s | content-length: %s | referer: %s',
        facts['path'], reason, facts['likely_cause'], facts['body_readable'],
        facts['posted_fields'], facts['has_csrf_cookie'], facts['content_length'],
        facts['referer'],
    )

    return render(request, 'monitor/csrf_failure.html', {
        'reason': reason,
        'facts': facts,
        'retry_url': request.path,
        # Passed explicitly: this project does not enable the `debug` context
        # processor, so the template cannot read it on its own.
        'debug': settings.DEBUG,
    }, status=403)
