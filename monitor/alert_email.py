"""
Shared logic for building and sending the media-digest alert email.

Used by both the scheduled management command (send_daily_alerts) and the
"Send test now" button in the UI, so the two paths stay identical.
"""
from datetime import datetime, time
from email.mime.image import MIMEImage

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .relevancy import filter_relevant


def _country_sort_key(obj, org_country):
    """Items from the org's country sort to the front (key=0), others to back (key=1)."""
    country = (getattr(obj, 'country', '') or '').strip().lower()
    return 0 if country == org_country.strip().lower() else 1


def _pub_ordinal(obj):
    """Ordinal of the publication date (0 when unset) — used for newest-first ordering."""
    d = getattr(obj, 'date_published', None)
    return d.toordinal() if d else 0


def gather(org, since, alert=None):
    """
    Return (online, print, social, broadcast) lists of records that came through
    since `since` (a datetime watermark, by created_at), each sorted with the
    org's country first and then newest publication date first.

    When `alert` is given and its categories exclude 'mention' (see
    Alert.wants_category), all four lists come back empty — the alert has been
    configured to skip raw media mentions entirely (e.g. reports/system only).
    """
    if alert is not None and not alert.wants_category('mention'):
        return [], [], [], []

    oc = org.country or ''

    def collect(manager):
        items = list(
            filter_relevant(manager.all())
            .filter(created_at__gte=since).order_by('-date_published', '-created_at')[:50]
        )
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


# Used when a daily alert has no delivery_time set on the form.
DEFAULT_DELIVERY_TIME = time(8, 0)


def daily_alert_due(alert, now=None):
    """
    True when a daily alert should be sent on this run: the current local time has
    reached the alert's delivery_time today (default 08:00 when unset), it hasn't
    already been sent since that time today, and we're on/after its start_date.

    The beat job runs every ~15 min and calls this for each daily alert, so each
    one fires once per day at its chosen time. The last_sent_at watermark stops
    repeats and gives automatic catch-up if a scheduled tick was missed.
    """
    now = now or timezone.localtime()
    if alert.start_date and now.date() < alert.start_date:
        return False
    delivery = alert.delivery_time or DEFAULT_DELIVERY_TIME
    scheduled = timezone.make_aware(datetime.combine(now.date(), delivery))
    if now < scheduled:
        return False
    if alert.last_sent_at and alert.last_sent_at >= scheduled:
        return False
    return True


def build_and_send(alert, *, since=None, force=False, update_watermark=True, recipients_override=None):
    """
    Build and send the digest email for a single alert.

    - `since`: watermark datetime; defaults to the alert's last_sent_at (or the
      start of today on the first run).
    - `force`: send even when there are no new records (used by the test button
      and daily digests); when False, an immediate alert with nothing new is skipped.
    - `update_watermark`: advance alert.last_sent_at after a successful send.
    - `recipients_override`: send to these addresses instead of the alert's
      configured recipients (used by the "send test to me" button).

    Returns a dict describing the outcome. Raises if the email backend fails to send.
    """
    org = alert.organization
    recipients = recipients_override if recipients_override else alert.recipient_list()
    if not recipients:
        return {'sent': False, 'reason': 'no recipients', 'total': 0}

    now = timezone.now()
    if since is None:
        since = alert.last_sent_at or start_of_today()

    online, print_arts, social, broadcast = gather(org, since, alert)
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
        msg.mixed_subtype = 'related'

    msg.send()  # let failures propagate to the caller

    if update_watermark:
        alert.last_sent_at = now
        alert.save(update_fields=['last_sent_at'])

    return {'sent': True, 'total': total, 'recipients': recipients,
            'counts': (len(online), len(print_arts), len(social), len(broadcast))}
