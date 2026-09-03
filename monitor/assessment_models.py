"""Storage for the public media intelligence assessment.

Every completed assessment is a sales lead, so it is a database row before it is
an email. The email is a convenience for whoever is watching an inbox; the row is
the record. If the mail server is down, or someone deletes the message, or the
person who was watching the inbox leaves, the lead is still here and still
workable from the admin.

Nothing here is tied to an Organization or a User. The whole point of the
assessment is that it runs before anybody signs up.
"""
import uuid

from django.db import models

from . import assessment

TIER_CHOICES = [(key, label) for key, label in assessment.TIER_LABELS.items()]
FIT_CHOICES = [(key, label) for key, label in assessment.FIT_LABELS.items()]

STATUS_CHOICES = [
    ('new', 'New'),
    ('contacted', 'Contacted'),
    ('qualified', 'Qualified'),
    ('closed', 'Closed'),
]

ACTION_CHOICES = [
    ('call', 'Discovery call'),
    ('webinar', 'Strategy session'),
    ('guide', 'Foundation guide'),
]


class AssessmentSubmission(models.Model):
    """One completed assessment.

    ``answers`` holds the raw submission keyed by question key. The scored fields
    beside it are denormalised copies, written once at submission time, so that
    the admin can filter and sort on them and so a later change to the scoring
    weights cannot silently restate what a lead was told months ago.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ── Who ──────────────────────────────────────────────────────────────────
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    company = models.CharField(max_length=200)
    industry = models.CharField(max_length=100, blank=True)
    role = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)

    # ── What they said ───────────────────────────────────────────────────────
    answers = models.JSONField(default=dict)
    note = models.TextField(blank=True)

    # ── What it scored ───────────────────────────────────────────────────────
    score = models.PositiveSmallIntegerField(default=0, help_text='Percentage, 0–100.')
    tier = models.CharField(max_length=20, choices=TIER_CHOICES, blank=True)
    fit = models.CharField(max_length=20, choices=FIT_CHOICES, blank=True)
    urgency = models.CharField(max_length=20, blank=True)
    budget = models.CharField(max_length=20, blank=True)
    timeline = models.CharField(max_length=20, blank=True)
    platforms = models.JSONField(default=list)

    # ── What happened next ───────────────────────────────────────────────────
    requested_action = models.CharField(max_length=20, choices=ACTION_CHOICES, blank=True)
    requested_action_at = models.DateTimeField(null=True, blank=True)
    report_sent_at = models.DateTimeField(null=True, blank=True)
    sales_notified_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['fit', '-created_at']),
        ]

    def __str__(self):
        return f'{self.full_name} · {self.company} ({self.score}%)'

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def report_delivered(self):
        """Whether the visitor actually received their report.

        Worth surfacing in the admin: a lead whose report never arrived is one
        somebody should follow up by hand rather than assume was served.
        """
        return self.report_sent_at is not None
