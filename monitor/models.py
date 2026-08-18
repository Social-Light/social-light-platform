import re
import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.contrib.contenttypes.fields import GenericForeignKey


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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class User(AbstractUser):
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name='members'
    )
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default='viewer')

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username

    def get_initials(self):
        parts = self.get_full_name().split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return self.get_full_name()[:2].upper()

    def get_role_display_name(self):
        return dict(ROLE_CHOICES).get(self.role, self.role)


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
