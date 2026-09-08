"""The Meta Pixel and its server-side twin, the Conversions API.

Two halves of one measurement, and both are needed:

* **The browser pixel** (see ``templates/monitor/partials/tracking.html``) is
  what builds the retargeting audience. Meta can only show an ad again to
  someone it recognised, and recognition happens in the browser.
* **The Conversions API**, here, reports the same events from the server. It is
  what keeps the numbers honest. Roughly a third of the browser events never
  arrive — ad blockers, iOS tracking prevention, a tab closed on the results
  screen — and every one of those is a conversion the ad account never learns
  about, so it optimises toward the wrong people.

Both halves send the same ``event_id``. Meta deduplicates on it, so an event
that makes it through twice is counted once and an event blocked in the browser
still lands from the server.

Nothing here raises. A tracking failure must never cost a lead: the assessment
row is already written by the time this is called, and if Meta is unreachable
the visitor still gets their report.

Consent gates the browser pixel, not this. The lawful basis differs: the pixel
writes third-party cookies to a visitor's device and needs their agreement,
while a server-side conversion carries only data they typed into our own form.
Set ``META_CAPI_REQUIRE_CONSENT=True`` to gate both, which is the stricter
reading and the safer one if the site is ever assessed under GDPR rather than
Botswana's DPA.
"""
import hashlib
import logging
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_URL = 'https://graph.facebook.com/v21.0/{pixel_id}/events'
TIMEOUT = 5     # seconds; the visitor is waiting on the response behind this


def enabled():
    """Whether the browser pixel is configured at all."""
    return bool(getattr(settings, 'META_PIXEL_ID', ''))


def capi_enabled():
    """Whether server-side events can be sent. Needs the pixel id *and* a token."""
    return bool(getattr(settings, 'META_PIXEL_ID', '')
                and getattr(settings, 'META_CAPI_ACCESS_TOKEN', ''))


def new_event_id():
    """An id shared by the browser and server copies of one event."""
    return uuid.uuid4().hex


def _hashed(value):
    """SHA-256 of a normalised value, as Meta requires for identifiers.

    Meta never receives the raw email address — only this digest, which it
    matches against digests of its own. Returns None for empty input so the
    field is omitted rather than sent as a hash of nothing.
    """
    value = (value or '').strip().lower()
    if not value:
        return None
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def user_data(request, email='', first_name='', last_name='', country=''):
    """The identifiers Meta uses to match this event to a person.

    ``_fbp`` and ``_fbc`` are the pixel's own first-party cookies, set in the
    browser on the ad click. Passing them back is what lets Meta tie a
    server-side conversion to the specific ad that produced it, so they matter
    more here than the hashed contact fields do.
    """
    data = {}
    for key, value in (('em', _hashed(email)),
                       ('fn', _hashed(first_name)),
                       ('ln', _hashed(last_name)),
                       ('country', _hashed(country))):
        if value:
            data[key] = [value]

    fbp = request.COOKIES.get('_fbp')
    fbc = request.COOKIES.get('_fbc')
    if not fbc:
        # No _fbc cookie means the pixel never ran — blocked, or refused at the
        # banner. The click id is still on our session from the landing URL, and
        # Meta accepts it in the documented `fb.1.<timestamp>.<fbclid>` shape.
        from . import attribution
        fbclid = attribution.current(request).get('fbclid')
        if fbclid:
            import time
            fbc = f'fb.1.{int(time.time() * 1000)}.{fbclid}'
    if fbp:
        data['fbp'] = fbp
    if fbc:
        data['fbc'] = fbc

    ip = _client_ip(request)
    if ip:
        data['client_ip_address'] = ip
    agent = request.META.get('HTTP_USER_AGENT', '')
    if agent:
        data['client_user_agent'] = agent[:500]
    return data


def send_event(request, event_name, event_id=None, event_source_url='',
               user=None, custom=None):
    """Report one conversion to Meta from the server. Never raises.

    Returns True only when Meta accepted the event, so a caller can log a
    failure without having to inspect the response itself.
    """
    if not capi_enabled():
        return False
    if getattr(settings, 'META_CAPI_REQUIRE_CONSENT', False) and not has_consent(request):
        return False

    import time
    import requests

    payload = {
        'data': [{
            'event_name': event_name,
            'event_time': int(time.time()),
            'event_id': event_id or new_event_id(),
            'action_source': 'website',
            'event_source_url': event_source_url or request.build_absolute_uri(),
            'user_data': user if user is not None else user_data(request),
            **({'custom_data': custom} if custom else {}),
        }],
    }
    # Events sent with a test code appear in Events Manager's test tool and are
    # kept out of the ad account's real numbers. Unset in production.
    test_code = getattr(settings, 'META_CAPI_TEST_EVENT_CODE', '')
    if test_code:
        payload['test_event_code'] = test_code

    try:
        response = requests.post(
            GRAPH_URL.format(pixel_id=settings.META_PIXEL_ID),
            params={'access_token': settings.META_CAPI_ACCESS_TOKEN},
            json=payload,
            timeout=TIMEOUT,
        )
        if response.status_code >= 400:
            logger.warning('Meta CAPI rejected %s: %s %s',
                           event_name, response.status_code, response.text[:400])
            return False
        return True
    except Exception as exc:              # noqa: BLE001 — tracking must not break a lead
        logger.warning('Meta CAPI %s failed: %s', event_name, exc)
        return False


# ── Consent ──────────────────────────────────────────────────────────────────
# One cookie, one of three values: 'all' (accepted), 'essential' (declined), or
# absent (not yet asked). It is deliberately not a session key — the answer has
# to survive the session, and a visitor who declined must not be asked again on
# every visit.

CONSENT_COOKIE = 'sl_cookie_consent'
CONSENT_MAX_AGE = 60 * 60 * 24 * 180        # six months, then ask again


def has_consent(request):
    """Whether this visitor has agreed to analytics and advertising cookies."""
    return request.COOKIES.get(CONSENT_COOKIE) == 'all'


def consent_state(request):
    """'all', 'essential', or '' when the visitor has not been asked yet."""
    value = request.COOKIES.get(CONSENT_COOKIE, '')
    return value if value in ('all', 'essential') else ''
