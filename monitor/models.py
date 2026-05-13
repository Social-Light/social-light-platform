import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser


SENTIMENT_CHOICES = [
    ('positive', 'Positive'),
    ('neutral', 'Neutral'),
    ('negative', 'Negative'),
]

COVERAGE_CHOICES = [
    ('Earned', 'Earned'),
    ('Incidental', 'Incidental'),
    ('Advocated', 'Advocated'),
    ('Not Set', 'Not Set'),
]

PLATFORM_CHOICES = [
    ('Facebook', 'Facebook'),
    ('Twitter', 'Twitter'),
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
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.headline[:60]

    class Meta:
        ordering = ['-date_published', '-created_at']


class SocialMediaPost(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='social_posts')
    platform = models.CharField(max_length=50, choices=PLATFORM_CHOICES, default='Facebook')
    page_name = models.CharField(max_length=200, blank=True)
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


class Alert(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='alerts')
    name = models.CharField(max_length=200)
    keywords = models.TextField(blank=True, help_text='Comma-separated keywords')
    email = models.EmailField(blank=True)
    frequency = models.CharField(
        max_length=20,
        choices=[('immediate', 'Immediate'), ('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')],
        default='daily'
    )
    email_subject = models.CharField(max_length=300, blank=True)
    start_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']
