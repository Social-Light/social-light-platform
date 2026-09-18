"""
Shared logic for building and sending the media-digest alert email.

Used by both the scheduled management command (send_daily_alerts) and the
"Send test now" button in the UI, so the two paths stay identical.
"""
from datetime import datetime, time, timedelta
from email.mime.image import MIMEImage

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .alert_xlsx import build_workbook
from .relevancy import filter_relevant


def _country_sort_key(obj, org_country):
    """Items from the org's country sort to the front (key=0), others to back (key=1)."""
    country = (getattr(obj, 'country', '') or '').strip().lower()
    return 0 if country == org_country.strip().lower() else 1


def _pub_ordinal(obj):
    """Ordinal of the publication date (0 when unset) — used for newest-first ordering."""
    d = getattr(obj, 'date_published', None)
    return d.toordinal() if d else 0



# How far back a mention's own publish date may be and still count as "new" for
# a digest, keyed by Alert.frequency. A backfill or late import can create a
# row today (created_at = now) for something published months ago — without
# this, `since` (a created_at watermark) alone would let it through as if it
# just happened. Values are generous grace windows around each cadence, not a
# strict window equal to it, so a slightly-delayed same-cycle item still shows.
#
# 'daily' has no entry (falls back to DEFAULT_MAX_PUBLISH_AGE_DAYS below) as of
# 2026-09-14 (Tony): it used to be 0 — an EXACT match on today's date_published —
# to stop a stuck watermark from ever ballooning into a multi-day backlog dump.
# That's now handled instead by daily_alert_due()/send_daily_alerts sending
# twice a day (08:00 + 15:00 CAT) off the `last_sent_at` watermark, which caps
# any gap at one missed slot and self-heals on the next tick — so the
# exact-day rule was pure downside: it silently and *permanently* dropped
# genuinely relevant coverage whenever a source's own date_published lagged its
# ingestion date by so much as a day (e.g. wire-syndicated stories, or social
# mentions the crawler only discovers after the fact) — see the 2026-09-14
# audit for BPC/L'Oréal SA examples. A grace window (like immediate's) still
# guards against an old backfill reading as "new" without that permanent loss.
MAX_PUBLISH_AGE_DAYS = {
    'immediate': 3,
    'weekly':    10,
    'monthly':   35,
}
DEFAULT_MAX_PUBLISH_AGE_DAYS = 3


def gather(org, since, alert=None, max_publish_age_days=None):
    """
    Return (online, print, social, broadcast) lists of records that came through
    since `since` (a datetime watermark, by created_at), each sorted with the
    org's country first and then newest publication date first.

    When `alert` is given and its categories exclude 'mention' (see
    Alert.wants_category), all four lists come back empty — the alert has been
    configured to skip raw media mentions entirely (e.g. reports/system only).

    max_publish_age_days: if given, also requires date_published to fall within
    that many days of today — independent of created_at. Without this, a row
    backfilled today for something published months ago passes the created_at
    watermark and reads as "new" in the digest. 0 is a special case — an EXACT
    match on today's date_published (see MAX_PUBLISH_AGE_DAYS's 'daily' entry),
    rather than a >= bound, so a stray future-dated row can't slip in under a
    >= comparison. Pass None to skip this guard entirely (e.g. for tooling that
    intentionally wants everything since the watermark).
    """
    if alert is not None and not alert.wants_category('mention'):
        return [], [], [], []

    oc = org.country or ''
    today = timezone.localdate()
    exact_today = max_publish_age_days == 0
    earliest_pub = (
        today - timedelta(days=max_publish_age_days)
        if max_publish_age_days is not None and not exact_today else None
    )

    def collect(manager):
        qs = filter_relevant(manager.all()).filter(created_at__gte=since)
        if exact_today:
            qs = qs.filter(date_published=today)
        elif earliest_pub is not None:
            qs = qs.filter(date_published__gte=earliest_pub)
        items = list(qs.order_by('-date_published', '-created_at')[:50])
        return sorted(items, key=lambda a: (_country_sort_key(a, oc), -_pub_ordinal(a)))

    return (
        collect(org.online_articles),
        collect(org.print_articles),
        collect(org.social_posts),
        collect(org.broadcast_mentions),
    )


def gather_today(org, alert=None):
    """
    Return (online, print, social, broadcast) lists of records whose own
    date_published is today (Africa/Gaborone) — an exact-date snapshot,
    independent of the alert's created_at watermark and uncapped.

    Used for the xlsx attached to the 15:00 daily send, which is meant to be
    a complete same-day record ("everything dated the 18th") rather than the
    incremental since-last-slot diff the email body shows via gather(). Kept
    separate from gather()'s watermark/grace-window logic so this doesn't
    reintroduce the permanent-loss bug fixed 2026-09-14 (see MAX_PUBLISH_AGE_DAYS
    above) — the email body's coverage is untouched by this.
    """
    if alert is not None and not alert.wants_category('mention'):
        return [], [], [], []

    oc = org.country or ''
    today = timezone.localdate()

    def collect(manager):
        qs = filter_relevant(manager.all()).filter(date_published=today)
        items = list(qs.order_by('-date_published', '-created_at'))
        return sorted(items, key=lambda a: (_country_sort_key(a, oc), -_pub_ordinal(a)))

    return (
        collect(org.online_articles),
        collect(org.print_articles),
        collect(org.social_posts),
        collect(org.broadcast_mentions),
    )


def gather_events(org, since, categories=None):
    """Return Event rows for `org` created since `since`, newest first, capped like
    the mention collections above. `categories` (an iterable of Event.category
    values) restricts which categories come back; falsy/None means all of them —
    mirrors Alert.wants_category's "empty means everything" convention."""
    from .models import Event
    qs = Event.objects.filter(organization=org, created_at__gte=since).order_by('-created_at')
    if categories:
        qs = qs.filter(category__in=categories)
    return list(qs[:50])


def start_of_today():
    return timezone.make_aware(datetime.combine(timezone.localdate(), time.min))


# Fixed twice-daily send times, Africa/Gaborone (CAT) — 2026-09-14 (Tony);
# evening slot moved 18:00 -> 15:00 same day: every daily alert fires at both,
# regardless of any per-alert delivery_time (that field is no longer consulted
# for frequency='daily'; it's still used, unchanged, by other frequencies).
# Each slot's digest covers everything since the *previous* slot via the
# last_sent_at watermark — 08:00 carries what came in overnight since the day
# before's 15:00 send, 15:00 carries what came in since that morning's 08:00
# send.
DAILY_SLOT_TIMES = [time(8, 0), time(15, 0)]


def daily_alert_due(alert, now=None):
    """
    True when a daily alert should be sent on this run: at least one of today's
    fixed slots (DAILY_SLOT_TIMES) has already passed and hasn't been sent yet,
    and we're on/after the alert's start_date.

    The beat job runs every ~15 min and calls this for each daily alert, so each
    slot fires once, at or shortly after its clock time. The last_sent_at
    watermark stops repeats and gives automatic catch-up if a tick was missed:
    e.g. if 08:00 failed to send, by 15:00 that slot is still "due" (last_sent_at
    predates it), so the 15:00 run sends one digest covering the whole gap back
    to the last successful send, rather than losing that slot's coverage.
    """
    now = now or timezone.localtime()
    if alert.start_date and now.date() < alert.start_date:
        return False
    for slot in DAILY_SLOT_TIMES:
        scheduled = timezone.make_aware(datetime.combine(now.date(), slot))
        if now >= scheduled and (not alert.last_sent_at or alert.last_sent_at < scheduled):
            return True
    return False


def build_and_send(alert, *, since=None, force=False, update_watermark=True,
                    recipients_override=None, include_xlsx=False):
    """
    Build and send the digest email for a single alert.

    - `since`: watermark datetime; defaults to the alert's last_sent_at (or the
      start of today on the first run).
    - `force`: send even when there are no new records (used by the test button
      and daily digests); when False, an immediate alert with nothing new is skipped.
    - `update_watermark`: advance alert.last_sent_at after a successful send.
    - `recipients_override`: send to these addresses instead of the alert's
      configured recipients (used by the "send test to me" button).
    - `include_xlsx`: attach the .xlsx workbook to the email. Defaults to False —
      digest emails are HTML-only; the workbook is available on demand instead
      (see views.alert_download_xlsx). send_daily_alerts sets this True on the
      15:00 slot, and the attached workbook covers today's date_published
      exactly (see gather_today), not the since-watermark window the email
      body uses.

    Returns a dict describing the outcome. Raises if the email backend fails to send.
    """
    org = alert.organization
    recipients = recipients_override if recipients_override else alert.recipient_list()
    if not recipients:
        return {'sent': False, 'reason': 'no recipients', 'total': 0}

    now = timezone.now()
    if since is None:
        since = alert.last_sent_at or start_of_today()

    max_age = MAX_PUBLISH_AGE_DAYS.get(alert.frequency, DEFAULT_MAX_PUBLISH_AGE_DAYS)
    online, print_arts, social, broadcast = gather(org, since, alert, max_publish_age_days=max_age)
    events = gather_events(org, since, alert.categories)
    reports = [e for e in events if e.category == 'report']
    system_events = [e for e in events if e.category == 'system']
    total = len(online) + len(print_arts) + len(social) + len(broadcast) + len(reports) + len(system_events)

    if not force and alert.frequency == 'immediate' and total == 0:
        return {'sent': False, 'reason': 'no new records', 'total': 0, 'recipients': recipients}

    base = getattr(settings, 'SITE_URL', 'https://sociallight.africa').rstrip('/')
    login_url = base + (getattr(settings, 'LOGIN_URL', '/login/') or '/login/')
    subject = alert.email_subject or f"Social Light: {org.name} Daily Media Update"
    banner_url = (base + alert.banner_image.url) if alert.banner_image else ''
    logo_url = (base + org.logo.url) if org.logo else ''

    # Read the banner so it can be embedded inline (CID) — this makes it display
    # even when the mail client blocks remote content.
    banner_bytes = None
    if alert.banner_image:
        try:
            with alert.banner_image.open('rb') as fh:
                banner_bytes = fh.read()
        except Exception:
            banner_bytes = None

    context = {
        'org':                org,
        'subject':            subject,
        'today':              timezone.localdate(),
        'login_url':          login_url,
        'banner_cid':         'alertbanner' if banner_bytes else '',
        'banner_url':         banner_url,
        'logo_url':           logo_url,
        'online_articles':    online,
        'print_articles':     print_arts,
        'social_posts':       social,
        'broadcast_mentions': broadcast,
        'reports':            reports,
        'system_events':      system_events,
        'online_count':       len(online),
        'print_count':        len(print_arts),
        'social_count':       len(social),
        'broadcast_count':    len(broadcast),
        'reports_count':      len(reports),
        'system_count':       len(system_events),
    }

    html_body = render_to_string('monitor/email/daily_digest.html', context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=f"{subject}\n\nOpen in an HTML-capable email client to view this message.",
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    msg.attach_alternative(html_body, "text/html")

    if banner_bytes:
        img = MIMEImage(banner_bytes)
        img.add_header('Content-ID', '<alertbanner>')
        img.add_header('Content-Disposition', 'inline', filename='banner')
        msg.attach(img)
        # Deliberately NOT setting mixed_subtype = 'related' here: that would wrap
        # the whole message (including the xlsx attachment below) in
        # multipart/related, which some clients (notably Outlook) treat as "only
        # render parts the HTML actually references via cid:" — silently dropping
        # the real, downloadable xlsx attachment. Plain multipart/mixed (Django's
        # default) still renders the inline cid-referenced banner correctly in
        # every mainstream client, while keeping the xlsx as a normal attachment.

    if include_xlsx:
        x_online, x_print, x_social, x_broadcast = gather_today(org, alert)
        xlsx_bytes = build_workbook(x_online, x_print, x_social, x_broadcast)
        xlsx_name = f"{org.name}-media-digest-{timezone.localdate().isoformat()}.xlsx"
        msg.attach(xlsx_name, xlsx_bytes,
                   'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    msg.send()  # let failures propagate to the caller

    if update_watermark:
        alert.last_sent_at = now
        alert.save(update_fields=['last_sent_at'])

    return {'sent': True, 'total': total, 'recipients': recipients,
            'counts': (len(online), len(print_arts), len(social), len(broadcast))}
