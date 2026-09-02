import math
import re
import uuid
from datetime import timedelta

from django.conf import settings as django_settings
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils import timezone


def trial_period_days():
    """Length of the free trial, in days. Settable per-deployment via the
    TRIAL_PERIOD_DAYS setting so the 14 days can be changed without a code
    change."""
    return int(getattr(django_settings, 'TRIAL_PERIOD_DAYS', 14))


SENTIMENT_CHOICES = [
    ('positive', 'Positive'),
    ('neutral', 'Neutral'),
    ('negative', 'Negative'),
    ('mixed', 'Mixed'),
]


COVERAGE_CHOICES = [
    ('Earned', 'Earned'),
    ('Incidental', 'Incidental'),
    ('Advocated', 'Advocated'),
    ('Not Set', 'Not Set'),
]

PLATFORM_CHOICES = [
    ('Facebook', 'Facebook'),
    ('X', 'X'),
    ('Instagram', 'Instagram'),
    ('LinkedIn', 'LinkedIn'),
    ('YouTube', 'YouTube'),
    ('TikTok', 'TikTok'),
    ('Other', 'Other'),
]

BROADCAST_TYPE_CHOICES = [
    ('RADIO', 'Radio'),
    ('TV', 'TV'),
    ('PODCAST', 'Podcast'),
    ('ONLINE', 'Online'),
]

ROLE_CHOICES = [
    ('platform_admin', 'Platform Admin'),
    ('org_admin', 'Org Admin'),
    ('viewer', 'Viewer'),
]

INDUSTRY_CHOICES = [
    ('Banking & Financial Services', 'Banking & Financial Services'),
    ('Mining & Metals', 'Mining & Metals'),
    ('Telecommunications', 'Telecommunications'),
    ('Retail', 'Retail'),
    ('Education & Research', 'Education & Research'),
    ('Healthcare', 'Healthcare'),
    ('Government', 'Government'),
    ('Energy', 'Energy'),
    ('Technology', 'Technology'),
    ('Marketing', 'Marketing'),
    ('Cosmetics, Beauty & Personal Care', 'Cosmetics, Beauty & Personal Care'),
    ('Public Relations', 'Public Relations'),
    ('Other', 'Other'),
]


BILLING_PERIOD_CHOICES = [
    ('monthly', 'Per month'),
    ('quarterly', 'Per quarter'),
    ('annual', 'Per year'),
]

#: How many calendar months each billing period covers. Used to work out when a
#: paid period ends, so the answer follows the calendar rather than a fixed
#: number of days — a monthly subscription taken on 31 January renews on 28
#: February, not on 2 March.
BILLING_PERIOD_MONTHS = {'monthly': 1, 'quarterly': 3, 'annual': 12}


def add_billing_period(start, billing_period):
    """``start`` advanced by one whole billing period.

    ``relativedelta`` is used rather than ``timedelta`` because months are not a
    fixed length: it clamps 31 January + 1 month to the last day of February
    instead of overflowing into March, which is the behaviour a customer expects
    from a monthly subscription.
    """
    from dateutil.relativedelta import relativedelta

    months = BILLING_PERIOD_MONTHS.get(billing_period, 1)
    return start + relativedelta(months=months)


CURRENCY_SYMBOLS = {'USD': '$', 'ZAR': 'R', 'GBP': '£', 'EUR': '€'}


class Package(models.Model):
    """A purchasable subscription tier. Prices, bullets and card styling all live
    here rather than in the templates, so the public price list is maintained
    from the Django admin — the platform advertises one public price list, the
    same for every client, so there are no per-organisation prices anywhere in
    the system."""
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    eyebrow = models.CharField(max_length=60, blank=True,
                               help_text='Small label above the name, e.g. "Full scope".')
    tagline = models.CharField(max_length=200, blank=True,
                               help_text='One line under the package name, e.g. "Growing organisations and agencies".')

    # ── Price ────────────────────────────────────────────────────────────────
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=8, default='USD')
    billing_period = models.CharField(max_length=20, choices=BILLING_PERIOD_CHOICES, default='monthly')
    price_override = models.CharField(max_length=40, blank=True,
                                      help_text='Shown instead of a figure, e.g. "Custom". Hides the period.')
    price_note = models.CharField(max_length=160, blank=True,
                                  help_text='Line under the price, e.g. "Billed annually · $588 per year".')

    # ── Card contents ────────────────────────────────────────────────────────
    # Plain strings, one per bullet, shown in order with a tick.
    features = models.JSONField(default=list, blank=True)
    # Bullets shown greyed out with a dash — what this tier does *not* include.
    excluded_features = models.JSONField(default=list, blank=True)
    exclusion_note = models.CharField(max_length=60, blank=True,
                                      help_text='Small note under each exclusion, e.g. "Enterprise only".')
    highlight_title = models.CharField(max_length=60, blank=True,
                                       help_text='Callout box heading, e.g. "Only on Enterprise".')
    highlight_body = models.CharField(max_length=300, blank=True, help_text='Callout box text.')

    # ── Presentation & call to action ────────────────────────────────────────
    accent_color = models.CharField(max_length=7, default='#3891C7',
                                    help_text='Colour of the bar across the top of the card.')
    is_dark = models.BooleanField(default=False, help_text='Render this card on a dark background.')
    cta_label = models.CharField(max_length=40, blank=True,
                                 help_text='Button text. Defaults to "Start free trial".')
    contact_only = models.BooleanField(
        default=False,
        help_text='This tier is arranged with sales rather than picked self-service.')

    is_featured = models.BooleanField(default=False, help_text='Highlights this package as the recommended tier.')
    is_active = models.BooleanField(default=True, help_text='Uncheck to retire the tier without deleting it. '
                                                            'Inactive tiers cannot be chosen or assigned.')
    is_public = models.BooleanField(
        default=True,
        help_text='Show on the published price list. Untick for a tier that is assignable '
                  '(onboarding, admin) but not advertised — the Free tier, for example.')
    sort_order = models.IntegerField(default=0)

    # ── Entitlements ──────────────────────────────────────────────────────
    # What this tier actually unlocks, as codes from monitor.entitlements.FEATURES.
    # Deliberately NOT `features` above: that is marketing copy shown on the price
    # card and is reworded freely in the admin, so it must never decide access.
    entitlements = models.JSONField(
        default=list, blank=True,
        help_text='Feature codes this package grants, e.g. ["report_download", "premium_reports"]. '
                  'The Entitlements section of this page lists every available code.')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sort_order', 'price', 'name']

    def __str__(self):
        return self.name

    def _bullets(self, raw):
        """Bullets as a clean list of strings, tolerating a newline-separated
        string having been saved into the JSON field by hand in the admin."""
        raw = raw or []
        if isinstance(raw, str):
            raw = raw.splitlines()
        return [str(f).strip() for f in raw if str(f).strip()]

    @property
    def feature_list(self):
        return self._bullets(self.features)

    @property
    def exclusion_list(self):
        return self._bullets(self.excluded_features)

    @property
    def currency_symbol(self):
        return CURRENCY_SYMBOLS.get(self.currency, f'{self.currency} ')

    @property
    def price_display(self):
        """The headline figure — or the override ("Custom") for tiers that are
        quoted rather than listed."""
        if self.price_override:
            return self.price_override
        if not self.price:
            return 'Talk to us'
        return f"{self.currency_symbol}{self.price:,.0f}"

    @property
    def shows_period(self):
        return bool(self.price) and not self.price_override

    @property
    def period_suffix(self):
        return {'monthly': '/ month', 'quarterly': '/ quarter', 'annual': '/ year'}.get(self.billing_period, '')

    @property
    def period_display(self): 
        return dict(BILLING_PERIOD_CHOICES).get(self.billing_period, self.billing_period)

    @property
    def button_label(self):
        return self.cta_label or ('Contact sales' if self.contact_only else 'Start free trial')

    # ── Entitlements ──────────────────────────────────────────────────────
    @property
    def entitlement_codes(self):
        """Valid feature codes this tier grants. Tolerates a comma/newline
        separated string having been typed into the JSON field by hand in the
        admin, and silently drops codes no longer in the registry."""
        from .entitlements import FEATURES
        raw = self.entitlements or []
        if isinstance(raw, str):
            raw = re.split(r'[,\n]', raw)
        return [c for c in (str(x).strip() for x in raw) if c in FEATURES]

    @property
    def entitlement_labels(self):
        from .entitlements import FEATURES
        return [FEATURES[c].label for c in self.entitlement_codes]

    def grants(self, code):
        return code in self.entitlement_codes


PLAN_STATUS_CHOICES = [
    ('trial', 'Free trial'),
    ('active', 'Paid subscription'),
    ('pending', 'Awaiting activation'),
    ('expired', 'Trial ended'),
    # Distinct from 'expired' on purpose. 'expired' means a trial ran out and
    # nothing was ever paid; 'past_due' means an organisation *was* paying and a
    # renewal did not go through. They need different wording in the admin and on
    # the paywall, and they are recovered from differently — one is a first sale,
    # the other is a card that needs replacing.
    ('past_due', 'Payment overdue'),
]


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    industry = models.CharField(max_length=100, choices=INDUSTRY_CHOICES, blank=True)
    country = models.CharField(max_length=100, default='Botswana')
    status = models.CharField(max_length=20, choices=[('active', 'Active'), ('inactive', 'Inactive')], default='active')
    address = models.CharField(max_length=300, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    website = models.URLField(blank=True, max_length=500)
    facebook_url = models.URLField(blank=True, max_length=500)
    linkedin_url = models.URLField(blank=True, max_length=500)
    x_handle = models.CharField(max_length=100, blank=True)
    logo = models.FileField(upload_to='logos/', blank=True, null=True)
    gradient_color1 = models.CharField(max_length=7, default='#1d4ed8')
    gradient_color2 = models.CharField(max_length=7, default='#0f172a')

    # ── Subscription / free trial ────────────────────────────────────────────
    # Defaults to 'active' so every organisation that existed before self-signup
    # was introduced keeps unrestricted access; only organisations created
    # through the public trial signup start as 'trial'.
    plan_status = models.CharField(max_length=20, choices=PLAN_STATUS_CHOICES, default='active')
    package = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='organizations')
    trial_started_at = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    subscription_activated_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(
        null=True, blank=True,
        help_text='When the paid period runs out. Access stops here unless a renewal '
                  'extends it. Left blank for a subscription activated by hand with no '
                  'agreed end date, which never lapses on its own.')

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']

    # ── Trial helpers ────────────────────────────────────────────────────────
    def start_trial(self, days=None):
        """Put the organisation on a fresh free trial. Called at signup."""
        days = trial_period_days() if days is None else days
        now = timezone.now()
        self.plan_status = 'trial'
        self.trial_started_at = now
        self.trial_ends_at = now + timedelta(days=days)

    def activate_package(self, package, period_end=None, extend=False):
        """Move the organisation onto a paid package.

        Called from two places: a platform admin activating a package once an
        off-platform payment has landed, and the payment gateway once a charge is
        verified.

        The paid period runs from now to one billing period ahead, so a
        subscription actually lapses instead of staying active forever. Pass
        ``period_end`` explicitly for a term agreed off-platform that does not
        match the package's own period — an annual invoice against a monthly
        tier, say — or ``None`` with ``extend=False`` on a package with no
        billing period to leave it open-ended.

        ``extend=True`` renews rather than restarts: the new period runs from the
        end of the one being replaced, not from today, so a renewal charged a day
        early does not cost the customer a day.
        """
        now = timezone.now()
        self.package = package
        self.plan_status = 'active'
        self.subscription_activated_at = now

        if period_end is not None:
            self.current_period_end = period_end
        elif package is not None:
            start = self.current_period_end if (extend and self.current_period_end) else now
            self.current_period_end = add_billing_period(start, package.billing_period)

        self.save(update_fields=['package', 'plan_status', 'subscription_activated_at',
                                 'current_period_end'])

    @property
    def paid_period_has_lapsed(self):
        """Whether a paid subscription has run past the period it paid for.

        An organisation with no ``current_period_end`` never lapses — that is how
        a subscription activated by hand, with no agreed end date, keeps working.
        """
        return bool(self.current_period_end and timezone.now() >= self.current_period_end)

    @property
    def effective_plan_status(self):
        """The real status right now.

        Two states are computed on read rather than stored, so access is cut off
        exactly on time whether or not a scheduled job has run: a trial flips to
        'expired' the moment ``trial_ends_at`` passes, and a paid subscription
        flips to 'past_due' the moment ``current_period_end`` does.

        Deriving the lapse here rather than in the middleware means every caller
        — the access check, the templates, the admin — agrees about it, and a
        renewal job that fails to run cannot leave an unpaid organisation with
        access it has not paid for.
        """
        now = timezone.now()
        if self.plan_status == 'trial' and self.trial_ends_at and now >= self.trial_ends_at:
            return 'expired'
        if self.plan_status == 'active' and self.paid_period_has_lapsed:
            return 'past_due'
        return self.plan_status

    @property
    def is_on_trial(self):
        return self.effective_plan_status == 'trial'

    @property
    def trial_has_expired(self):
        """Whether the organisation is currently behind the paywall and needs to
        pay. Named for the trial because that was once the only way in, but a
        lapsed paid subscription puts an organisation in the same place."""
        return self.effective_plan_status in ('expired', 'pending', 'past_due')

    @property
    def trial_days_left(self):
        """Whole days of trial remaining, rounded up — a trial with six hours to
        run reads as "1 day left", never "0 days left" while still usable."""
        if not self.trial_ends_at:
            return 0
        remaining = (self.trial_ends_at - timezone.now()).total_seconds()
        if remaining <= 0:
            return 0
        return max(1, math.ceil(remaining / 86400))

    @property
    def has_platform_access(self):
        """False once the trial has run out and no package has been activated —
        the single check the access middleware and templates both read."""
        return self.effective_plan_status in ('trial', 'active')


class User(AbstractUser):
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name='members'
    )
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default='viewer')

    # ── Profile ──────────────────────────────────────────────────────────────
    # Collected at registration and on the onboarding profile step. Blank by
    # default so every account that predates onboarding stays valid.
    phone = models.CharField(max_length=50, blank=True)
    job_title = models.CharField(max_length=150, blank=True)
    country = models.CharField(max_length=100, blank=True)

    # ── Email verification ───────────────────────────────────────────────────
    # False for anyone who signs up from now on until they follow the link in
    # their verification email. Every account that existed before verification
    # was introduced is backfilled to True by migration — they were never asked,
    # and must not be locked out retrospectively.
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username

    def get_initials(self):
        parts = self.get_full_name().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.get_full_name()[:2].upper()

    def get_role_display_name(self):
        return dict(ROLE_CHOICES).get(self.role, self.role)

    # ── Entitlements ─────────────────────────────────────────────────────────
    def has_feature(self, code):
        """True when this account's plan entitles it to `code`. The one call the
        whole application makes to decide access to a paid capability — see
        monitor/entitlements.py for how it resolves."""
        from .entitlements import user_has_feature
        return user_has_feature(self, code)

    @property
    def entitlements(self):
        from .entitlements import entitlements_for_user
        return entitlements_for_user(self)

    # ── Onboarding ───────────────────────────────────────────────────────────
    @property
    def onboarding_progress(self):
        """This user's onboarding record, or None. A missing record means the
        account predates onboarding entirely; callers treat that as complete."""
        try:
            return self.onboarding
        except OnboardingProgress.DoesNotExist:
            return None

    @property
    def onboarding_complete(self):
        progress = self.onboarding_progress
        return progress is None or progress.is_complete


KEYWORD_CATEGORY_CHOICES = [
    ('brand', 'Brand Keywords'),
    ('personnel', 'Personnel'),
    ('campaign', 'Campaigns'),
]


class Keyword(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='keywords')
    keyword = models.CharField(max_length=200)
    category = models.CharField(max_length=100, default='brand')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.keyword

    class Meta:
        ordering = ['category', 'keyword']
        unique_together = ['organization', 'keyword', 'category']


class Competitor(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='competitors')
    name = models.CharField(max_length=200)
    aliases = models.TextField(blank=True, default='',
                               help_text='Comma-separated alternative names/keywords used to match coverage')
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def match_terms(self):
        """All terms that should match this competitor in coverage: the name plus
        any comma/newline-separated aliases. De-duplicated (case-insensitive),
        empties dropped, original casing preserved."""
        terms = [self.name] + re.split(r'[,\n]', self.aliases or '')
        seen, out = set(), []
        for t in terms:
            t = t.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
        return out

    class Meta:
        ordering = ['name']


class CompetitorArticle(models.Model):
    """Online coverage *about* a competitor, loaded in bulk via CSV. Distinct from
    OnlineArticle, which is the organisation's own coverage."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='competitor_articles')
    competitor = models.ForeignKey(Competitor, on_delete=models.SET_NULL, null=True, blank=True, related_name='articles')
    company_name = models.CharField(max_length=200)
    headline = models.TextField()
    url = models.URLField(blank=True, max_length=2000)
    summary = models.TextField(blank=True)
    source = models.CharField(max_length=200, blank=True)
    date_published = models.DateField(null=True, blank=True)
    country = models.CharField(max_length=100, blank=True)
    matched_keywords = models.CharField(max_length=300, blank=True)
    sentiment_score = models.FloatField(default=0)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default='neutral')
    reach = models.IntegerField(default=0)
    cpm = models.FloatField(default=0)
    ave = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    rank = models.FloatField(default=0)
    coverage_type = models.CharField(max_length=50, blank=True, default='Not Set')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.company_name}: {self.headline[:50]}"

    class Meta:
        ordering = ['-date_published', '-created_at']


class OnlineArticle(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='online_articles')
    source = models.CharField(max_length=200)
    source_logo = models.URLField(blank=True)
    headline = models.TextField()
    summary = models.TextField(blank=True)
    url = models.URLField(blank=True, max_length=2000)
    date_published = models.DateField()
    country = models.CharField(max_length=100, blank=True)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default='neutral')
    ave = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    coverage = models.CharField(max_length=50, choices=COVERAGE_CHOICES, blank=True, default='Not Set')
    reach = models.IntegerField(default=0)
    relevancy = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.headline[:60]

    class Meta:
        ordering = ['-date_published', '-created_at']


class PrintArticle(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='print_articles')
    source = models.CharField(max_length=200)
    headline = models.TextField()
    summary = models.TextField(blank=True)
    author = models.CharField(max_length=200, blank=True)
    section = models.CharField(max_length=100, blank=True)
    url = models.URLField(blank=True, max_length=2000)
    date_published = models.DateField()
    country = models.CharField(max_length=100, blank=True)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default='neutral')
    ave = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reach = models.IntegerField(default=0, help_text='Estimated readership (circulation × readers-per-copy).')
    relevancy = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.headline[:60]

    class Meta:
        ordering = ['-date_published', '-created_at']


class SocialMediaPost(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='social_posts')
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES, default='Facebook')
    page_name = models.TextField(blank=True)
    headline = models.TextField()
    summary = models.TextField(blank=True)
    url = models.URLField(blank=True, max_length=2000)
    date_published = models.DateField()
    country = models.CharField(max_length=100, blank=True)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default='neutral')
    ave = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    rank = models.FloatField(default=0)
    reach = models.IntegerField(default=0)
    relevancy = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.headline[:60]

    class Meta:
        ordering = ['-date_published', '-created_at']


class BroadcastMention(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='broadcast_mentions')
    source = models.CharField(max_length=200)
    headline = models.TextField()
    summary = models.TextField(blank=True)
    url = models.URLField(blank=True, max_length=2000)
    date_published = models.DateField()
    country = models.CharField(max_length=100, blank=True)
    sentiment = models.CharField(max_length=20, choices=SENTIMENT_CHOICES, default='neutral')
    ave = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    relevancy = models.FloatField(default=0)
    duration = models.CharField(max_length=50, blank=True)
    broadcast_type = models.CharField(max_length=20, choices=BROADCAST_TYPE_CHOICES, default='RADIO')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.headline[:60]

    class Meta:
        ordering = ['-date_published', '-created_at']


SOURCE_TYPE_CHOICES = [
    ('online', 'Online'),
    ('social', 'Social'),
    ('print', 'Print'),
    ('broadcast', 'Broadcast'),
]


class MediaSource(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='media_sources')
    name = models.CharField(max_length=200)
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPE_CHOICES, default='online')
    url = models.URLField(blank=True, max_length=500)
    handle = models.CharField(max_length=200, blank=True)
    country = models.CharField(max_length=100, blank=True)
    reach = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class GeneratedReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='generated_reports')
    title = models.CharField(max_length=300)
    report_type = models.CharField(max_length=100)
    modules = models.JSONField(default=list)
    scope = models.JSONField(default=list)
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    _TAG_MAP = {'posts': 'social', 'articles': 'online', 'printmedia': 'print', 'broadcast': 'broadcast'}
    _VALID = {'social', 'online', 'broadcast', 'print'}

    @property
    def media_type_keys(self):
        seen, keys = set(), []
        for m in (self.modules or []):
            key = m.split(':')[0] if ':' in m else m
            key = self._TAG_MAP.get(key, key)
            if key in self._VALID and key not in seen:
                seen.add(key)
                keys.append(key)
        return keys

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class ReportAnalysis(models.Model):
    """Durable store for the AI-generated report analysis (ESG / stakeholder /
    sectorial competitor), so it survives server restarts and is reused for a
    week before the user is prompted to regenerate."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='report_analyses')
    date_from = models.DateField()
    date_to = models.DateField()

    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('organization', 'date_from', 'date_to')]
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.organization.name} analysis {self.date_from}–{self.date_to}"


class IssueReport(models.Model):
    """A saga/issue-focused special report: from all mentions in a period, an AI
    selects only those relevant to a named issue (issue_query) and writes the
    issue-specific narrative (executive summary, timeline, framing, risks,
    recommendations).

    Unlike ReportAnalysis (keyed only by period), each IssueReport is a distinct
    saga. ``candidate_ids`` holds every mention the keyword pre-filter surfaced;
    ``selected_ids`` holds the AI's on-topic subset, editable afterwards so a
    human can add back an excluded candidate or drop a wrong include. Displayed
    figures are always recomputed from the selected DB rows — ``payload`` never
    carries coverage the model invented.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='issue_reports')
    title = models.CharField(max_length=300)
    # 'issue' for a manually-created special edition; 'campaign' when generated
    # from a Campaign — controls the type label shown in the reports list.
    kind = models.CharField(max_length=20, default='issue')
    issue_query = models.TextField(help_text='The saga/issue the report isolates coverage for.')
    date_from = models.DateField()
    date_to = models.DateField()
    # {"online": [id, …], "print": […], "social": […], "broadcast": […]}
    candidate_ids = models.JSONField(default=dict)
    selected_ids = models.JSONField(default=dict)
    payload = models.JSONField(default=dict)
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.date_from}–{self.date_to})"

    @property
    def media_type_keys(self):
        """Media types with at least one selected mention — for the reports list."""
        order = ['social', 'online', 'print', 'broadcast']
        return [k for k in order if (self.selected_ids or {}).get(k)]

    @property
    def type_label(self):
        return 'Campaign' if self.kind == 'campaign' else 'Issue-Focused'


class Campaign(models.Model):
    """A tracked marketing/PR campaign for an organisation. Coverage is matched by
    the campaign's own ``terms`` (keywords and #hashtags) in the message/summary,
    within its optional date window. Distinct from the org-wide ``campaign``
    keyword category: each Campaign is a named entity with its own terms so its
    mentions, reach and sentiment can be tracked and reported on individually.
    A campaign report reuses the Issue-Focused report engine, seeded with these
    terms.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='campaigns')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Keywords and #hashtags matched against message/summary text.
    terms = models.JSONField(default=list)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        """True when today falls within the campaign's window (open-ended if a
        bound is unset)."""
        from datetime import date as _date
        today = _date.today()
        if self.date_from and today < self.date_from:
            return False
        if self.date_to and today > self.date_to:
            return False
        return True


EVENT_CATEGORY_CHOICES = [
    ('mention', 'Media Mention'),
    ('report', 'Report / Analysis'),
    ('system', 'System Update'),
]


class Alert(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='alerts')
    name = models.CharField(max_length=200)
    keywords = models.TextField(blank=True, help_text='Comma-separated keywords')
    email = models.EmailField(blank=True)
    recipients = models.TextField(blank=True, help_text='Comma-separated recipient emails')
    frequency = models.CharField(
        max_length=20,
        choices=[('immediate', 'Immediate'), ('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
        default='daily'
    )
    email_subject = models.CharField(max_length=300, blank=True)
    start_date = models.DateField(null=True, blank=True)
    delivery_time = models.TimeField(null=True, blank=True)
    banner_image = models.FileField(upload_to='alert_banners/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    last_sent_at = models.DateTimeField(null=True, blank=True, help_text='When the digest was last sent (watermark for new records)')
    # Which Event categories this alert's digest includes (see EVENT_CATEGORY_CHOICES).
    # Empty list = no restriction = every category, so existing alerts created before
    # this field existed keep behaving exactly as they did (mentions only, since that
    # was all there was to send) while still picking up new categories automatically.
    categories = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    def recipient_list(self):
        raw = self.recipients or self.email or ''
        return [e.strip() for e in raw.split(',') if e.strip()]

    def wants_category(self, category):
        """True when this alert's digest should include events of `category`.
        An empty `categories` list means "everything" (see field docstring)."""
        return not self.categories or category in self.categories

    class Meta:
        ordering = ['name']


class Event(models.Model):
    """A unified, source-agnostic log of things worth notifying an organisation's
    users about — a new AI-generated report, a saga/issue report, a system update,
    or (in future) a new item from any social-media API integration.

    This is deliberately separate from the raw coverage tables (OnlineArticle,
    SocialMediaPost, etc.) — those remain the durable store for mention content and
    are unaffected by this model. Event exists purely so the notification pipeline
    (Alert + alert_email.gather) has one place to look for "what's new" regardless
    of which part of the system produced it, instead of every new content type
    needing its own bespoke wiring into the digest.

    `related_object` is an optional generic pointer back to the record the event is
    about (e.g. the IssueReport itself), so an email can deep-link to it.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='events')
    category = models.CharField(max_length=20, choices=EVENT_CATEGORY_CHOICES)
    event_type = models.CharField(max_length=100, help_text="e.g. 'report_generated', 'issue_report_created'")
    title = models.CharField(max_length=300)
    summary = models.TextField(blank=True)
    url = models.URLField(blank=True, max_length=500)
    content_type = models.ForeignKey(
        'contenttypes.ContentType', on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    related_object = GenericForeignKey('content_type', 'object_id')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.category}] {self.title}"


REQUEST_STATUS_CHOICES = [
    ('pending', 'Pending'),
    ('approved', 'Approved & activated'),
    ('declined', 'Declined'),
]


class SubscriptionRequest(models.Model):
    """An organisation asking to be put on a paid package, raised from the
    paywall the user hits when their free trial ends.

    Payment is settled off-platform (invoice / EFT) and a platform admin then
    activates the package from the Django admin, which is what actually restores
    access. This record is the audit trail of who asked for what and when.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='subscription_requests')
    package = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='requests')
    requested_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=REQUEST_STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey('User', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='handled_subscription_requests')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.organization.name} → {self.package.name if self.package else 'no package'}"


# ── Public marketing content ─────────────────────────────────────────────────
# Everything below feeds the public landing page. It is deliberately NOT derived
# from OnlineArticle/PrintArticle/BroadcastMention: those are scoped to an
# Organization, so publishing them would disclose which clients we monitor and
# what we monitor them for. Editors promote items here by hand instead, and
# nothing appears publicly until is_published is set.

class Sector(models.Model):
    """A tab on the public "Sector Intelligence" page."""
    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    heading = models.CharField(
        max_length=200, blank=True,
        help_text='Heading above the story list. Defaults to "Top <name> intelligence this week".')
    shows_ticker = models.BooleanField(
        default=False, help_text='Show the commodities ticker while this sector is selected.')
    is_published = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return self.name

    @property
    def display_heading(self):
        return self.heading or f'Top {self.name.lower()} intelligence this week'


class SectorStory(models.Model):
    """One ranked story under a sector. Written or approved by an editor."""
    sector = models.ForeignKey(Sector, on_delete=models.CASCADE, related_name='stories')
    title = models.CharField(max_length=300)
    summary = models.TextField(blank=True)
    source_label = models.CharField(
        max_length=120, help_text='How the source is credited, e.g. "Regional business press".')
    url = models.URLField(blank=True, max_length=2000,
                          help_text='Optional link out to the story.')
    published_on = models.DateField()
    display_order = models.PositiveIntegerField(
        default=0, help_text='Rank within the sector. Lower numbers appear first.')
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', '-published_on']
        verbose_name_plural = 'Sector stories'

    def __str__(self):
        return self.title[:70]


class CommodityQuote(models.Model):
    """A row in the commodities ticker.

    price_display is free text so a quote can be a price, an index level or a
    range without the model guessing at units. change_percent drives the arrow
    and its colour; leave it at zero for a flat reading.
    """
    name = models.CharField(max_length=80)
    price_display = models.CharField(max_length=40, help_text='Shown as written, e.g. "$2,412.30".')
    change_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    is_published = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']

    def __str__(self):
        return f'{self.name} {self.price_display}'

    @property
    def is_up(self):
        return self.change_percent >= 0

    @property
    def change_display(self):
        return f'{"▲" if self.is_up else "▼"} {abs(self.change_percent):.1f}%'


class Publication(models.Model):
    """A card on the public "Publications & news" page."""
    kind = models.CharField(max_length=80, help_text='e.g. "Weekly digest", "Sector report".')
    title = models.CharField(max_length=200)
    blurb = models.TextField(blank=True)
    url = models.URLField(blank=True, max_length=2000)
    published_on = models.DateField()
    is_published = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', '-published_on']

    def __str__(self):
        return self.title


# ── Onboarding, legal consent and payment ────────────────────────────────────
# Declared in their own modules to keep this file readable, and imported here so
# Django registers them against the `monitor` app exactly as if they were inline.
# They reference Organization and User by string, so this import is not circular.
from .onboarding_models import (          # noqa: E402,F401
    ACCOUNT_TYPE_CHOICES, CONSENT_DECISIONS, LEGAL_DOCUMENT_TYPES, ONBOARDING_STATES,
    STATE_ORDER, AgencyDeclaration, ConsentRecord, EmailVerificationToken,
    LegalDocument, OnboardingProgress,
)
from .payment_models import (             # noqa: E402,F401
    PAYMENT_STATUS_CHOICES, Payment, PaymentMethod,
)
from .assessment_models import (          # noqa: E402,F401
    ACTION_CHOICES, FIT_CHOICES, STATUS_CHOICES as ASSESSMENT_STATUS_CHOICES,
    TIER_CHOICES, AssessmentSubmission,
)
