"""Where a visitor came from, remembered from their first page to their last.

Someone who clicks a Meta ad lands on the assessment with ``?utm_source=meta``
on the URL, then answers ten questions, and by the time they submit the query
string is long gone. Without this the lead arrives in the admin looking exactly
like one that came from a Google search, and there is no way to say what the ad
spend bought.

So the campaign parameters are read on the first page of a visit and kept in the
session. They are written onto the AssessmentSubmission row at submit time, and
because they are stored on the row rather than looked up later, a lead's origin
stays true even after the campaign is renamed or retired.

This is first-party data: our own session cookie, our own form, our own
database. It is not the Meta Pixel (monitor/meta_pixel.py), it needs no consent
banner to work, and an ad blocker cannot remove it. Attribution keeps working
for every visitor even when the pixel is refused, which is the point of doing it
this way round.

**First touch wins.** Once a visit has a source it is not overwritten, so a
visitor who arrives from an ad and then wanders to the pricing page and back is
still credited to the ad rather than to an internal link.
"""
SESSION_KEY = 'attribution'

# The five standard UTM parameters, plus the click identifiers the ad platforms
# append themselves. fbclid is what Meta puts on an outbound ad click; gclid is
# Google's. Both arrive whether or not whoever built the campaign remembered to
# tag it, which makes them the more reliable signal of the two.
UTM_PARAMS = ('utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term')
CLICK_IDS = ('fbclid', 'gclid', 'ttclid', 'li_fat_id', 'msclkid')
TRACKED_PARAMS = UTM_PARAMS + CLICK_IDS

# Everything here is typed into a URL by a stranger, so it is bounded before it
# is stored. Real campaign names are far shorter than this.
MAX_VALUE_LENGTH = 200
MAX_REFERRER_LENGTH = 500

# Referrer hosts mapped to the channel they really represent. A click from the
# Facebook app arrives as l.facebook.com, from Instagram as l.instagram.com, and
# neither says "meta" anywhere — which is why a raw referrer column is not on
# its own an answer to "how many came from Meta".
_REFERRER_SOURCES = (
    (('facebook.', 'fb.me', 'fb.com', 'instagram.', 'messenger.', 'threads.'), 'meta'),
    (('google.', 'googleadservices.'), 'google'),
    (('linkedin.', 'lnkd.in'), 'linkedin'),
    (('twitter.', 't.co', 'x.com'), 'x'),
    (('bing.',), 'bing'),
    (('youtube.', 'youtu.be'), 'youtube'),
    (('tiktok.',), 'tiktok'),
    (('whatsapp.', 'wa.me'), 'whatsapp'),
)


def _host(url):
    """The hostname of `url`, lowercased, or '' if there isn't one."""
    if not url:
        return ''
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


def channel(source, referrer, request_host=''):
    """The bucket this visit belongs in, for counting.

    Reporting needs a small fixed set of channels, not the open-ended strings a
    campaign builder can type. An explicit ``utm_source`` is trusted first
    because whoever tagged the link meant it; otherwise the referrer is read;
    a visit with neither is direct.
    """
    src = (source or '').strip().lower()
    if src:
        for hosts, name in _REFERRER_SOURCES:
            if any(h.strip('.') in src for h in hosts):
                return name
        if src in ('fb', 'ig', 'facebook', 'instagram', 'meta'):
            return 'meta'
        return src

    host = _host(referrer)
    if not host:
        return 'direct'
    if request_host and host.endswith(request_host.lower().split(':')[0]):
        return 'direct'          # an internal link is not a new source
    for hosts, name in _REFERRER_SOURCES:
        if any(h in host for h in hosts):
            return name
    return 'referral'


def capture(request):
    """Record where this visit came from, once per session.

    Called from the middleware on ordinary page loads. Returns the stored
    attribution either way, so the caller never has to care whether this
    particular request was the one that set it.
    """
    session = getattr(request, 'session', None)
    if session is None:
        return {}

    existing = session.get(SESSION_KEY)
    params = {k: v[:MAX_VALUE_LENGTH] for k in TRACKED_PARAMS
              if (v := (request.GET.get(k) or '').strip())}

    # A fresh set of campaign parameters starts a new attribution even mid
    # session: someone who returns from a second ad a week later should be
    # credited to the second ad, not to whatever brought them the first time.
    if existing and not params:
        return existing

    referrer = (request.META.get('HTTP_REFERER') or '')[:MAX_REFERRER_LENGTH]
    data = {
        'landing_path': request.path[:MAX_VALUE_LENGTH],
        'referrer': referrer,
        'channel': channel(params.get('utm_source'), referrer, request.get_host()),
        **{k: params.get(k, '') for k in TRACKED_PARAMS},
    }
    session[SESSION_KEY] = data
    return data


def current(request):
    """The attribution stored for this visit, or an empty dict."""
    session = getattr(request, 'session', None)
    return (session.get(SESSION_KEY) or {}) if session is not None else {}


def submission_fields(request):
    """The attribution as AssessmentSubmission keyword arguments.

    Only the columns that exist on the model, so this can be splatted straight
    into ``objects.create()``.
    """
    data = current(request)
    return {
        'utm_source': data.get('utm_source', ''),
        'utm_medium': data.get('utm_medium', ''),
        'utm_campaign': data.get('utm_campaign', ''),
        'utm_content': data.get('utm_content', ''),
        'utm_term': data.get('utm_term', ''),
        'click_id': (data.get('fbclid') or data.get('gclid')
                     or data.get('ttclid') or data.get('li_fat_id')
                     or data.get('msclkid') or ''),
        'referrer': data.get('referrer', ''),
        'landing_path': data.get('landing_path', ''),
        'channel': data.get('channel', '') or 'direct',
    }
