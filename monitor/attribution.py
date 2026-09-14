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


# Meta's apps open links in their own embedded browser, and that browser names
# itself in the User-Agent even though it sends no referrer. This is the only
# way to see Instagram traffic at all: an Instagram bio click arrives with the
# referrer stripped, so without this it is indistinguishable from someone typing
# the address, and the whole account looks like it sends nobody.
#
# The markers are Meta's own and have been stable for years. FBAN/FBAV/FB_IAB
# are set by the Facebook app, `Instagram` by the Instagram app.
_INAPP_BROWSERS = (
    (('instagram',), 'instagram'),
    (('fban', 'fbav', 'fb_iab', 'fbios', 'fb4a'), 'facebook'),
    (('linkedinapp',), 'linkedin'),
    (('twitter',), 'x'),
)


def in_app_source(user_agent):
    """The app whose embedded browser this is, or '' for an ordinary browser.

    Weaker than a tagged link — it says which app they were in, not which post
    or bio link they tapped — but it is the difference between counting a
    visitor against Instagram and losing them into "direct".
    """
    agent = (user_agent or '').lower()
    for markers, source in _INAPP_BROWSERS:
        if any(marker in agent for marker in markers):
            return source
    return ''


def _host(url):
    """The hostname of `url`, lowercased, or '' if there isn't one."""
    if not url:
        return ''
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or '').lower()
    except ValueError:
        return ''


def channel(source, referrer, request_host='', user_agent=''):
    """The bucket this visit belongs in, for counting.

    Reporting needs a small fixed set of channels, not the open-ended strings a
    campaign builder can type. Three signals, in descending order of how much
    they can be trusted:

    1. An explicit ``utm_source``, because whoever tagged the link meant it.
    2. The referring site, where the browser sent one.
    3. The app whose in-app browser this is, which is all that survives when
       the referrer has been stripped.

    A visit with none of the three is direct.
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
    internal = bool(request_host and host
                    and host.endswith(request_host.lower().split(':')[0]))
    if host and not internal:
        for hosts, name in _REFERRER_SOURCES:
            if any(h in host for h in hosts):
                return name
        return 'referral'

    # No usable referrer. Before calling this direct, ask whether they are
    # sitting inside an app that simply does not send one.
    app = in_app_source(user_agent)
    if app:
        return channel(app, '')
    return 'direct'


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
    agent = request.META.get('HTTP_USER_AGENT', '')
    data = {
        'landing_path': request.path[:MAX_VALUE_LENGTH],
        'referrer': referrer,
        'channel': channel(params.get('utm_source'), referrer, request.get_host(), agent),
        **{k: params.get(k, '') for k in TRACKED_PARAMS},
    }

    # An untagged visit from inside an app gets the app recorded as its source,
    # so the admin shows "meta · in-app · instagram" rather than a blank row in
    # "direct". A tagged link always wins: the tag is the better evidence.
    if not data['utm_source']:
        app = in_app_source(agent)
        if app:
            data['utm_source'] = app
            data['utm_medium'] = data['utm_medium'] or 'in-app'
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


# ── Counting the visits, not just the leads ──────────────────────────────────
# The session flag below is what makes this a count of people rather than of
# page views. It is set the first time a visit is counted and checked on every
# request after, so reading nine pages counts once.

COUNTED_KEY = 'attribution_counted'

# Crawlers are most of the traffic on a small marketing site and none of the
# audience. Counting Googlebot and Meta's own link scraper as visitors would
# inflate every number here, and the inflation would land almost entirely on
# "direct" — making the one bucket that is already hard to read useless.
_BOT_MARKERS = (
    'bot', 'crawl', 'spider', 'slurp', 'fetch', 'monitor', 'preview',
    'facebookexternalhit', 'headless', 'python-requests', 'curl', 'wget',
    'lighthouse', 'pingdom', 'uptime', 'scan',
)


def looks_like_a_bot(user_agent):
    """Whether this user agent is a machine rather than a person.

    Deliberately generous: a real visitor wrongly excluded costs one from a
    count, while a crawler wrongly included corrupts the comparison the count
    exists for. Nothing depends on being exactly right, so err toward excluding.
    """
    return any(marker in (user_agent or '').lower() for marker in _BOT_MARKERS)


def count_visit(request, data):
    """Add one to today's tally for where this visit came from. Never raises.

    Called from the middleware after `capture`. Failure here must not cost the
    visitor their page — a counter is not worth a 500 — so the whole thing is
    swallowed and logged.
    """
    session = getattr(request, 'session', None)
    if session is None or session.get(COUNTED_KEY):
        return False
    if looks_like_a_bot(request.META.get('HTTP_USER_AGENT', '')):
        return False

    try:
        from django.db.models import F
        from django.utils import timezone

        from .visit_models import VisitCount

        bucket, created = VisitCount.objects.get_or_create(
            date=timezone.localdate(),
            channel=data.get('channel') or 'direct',
            utm_source=data.get('utm_source', ''),
            utm_medium=data.get('utm_medium', ''),
            utm_campaign=data.get('utm_campaign', ''),
            landing_path=data.get('landing_path', ''),
            defaults={'visits': 1},
        )
        if not created:
            # F() rather than read-modify-write, so simultaneous visitors on
            # different workers cannot overwrite each other's increment.
            VisitCount.objects.filter(pk=bucket.pk).update(visits=F('visits') + 1)
        session[COUNTED_KEY] = True
        return True
    except Exception:                     # noqa: BLE001 — a tally is never worth a 500
        import logging
        logging.getLogger(__name__).warning('Visit not counted.', exc_info=True)
        return False
