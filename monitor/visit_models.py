"""Counting visits by where they came from.

AssessmentSubmission answers "which campaign produced a lead". It cannot answer
"how many people from our Facebook page came to the site at all", because
somebody who lands, reads and leaves never writes a row. Most visitors do
exactly that, so the assessment table is the last few percent of the traffic and
says nothing about the rest.

This is the rest. One row per day per source, holding a count — not one row per
visitor.

That shape is deliberate:

* **It stores no personal data.** No IP, no user agent, no identifier, nothing
  that could single anybody out. It is a tally, so there is no subject to have
  rights over it, nothing to disclose in the privacy notice, and nothing to
  delete on request. Under Botswana's DPA and under GDPR it sits outside the
  regime entirely.
* **It needs no consent**, for the same reason, so it keeps counting for the
  visitors who decline the cookie banner — which is the group a pixel is blind
  to and the group that makes pixel numbers under-report.
* **It stays small forever.** A busy month adds a few hundred rows, not a few
  hundred thousand, so it costs the 4 GB droplet nothing.

Counted once per visit, not once per page, because the question is how many
people came — not how many pages they read.
"""
from django.db import models


class VisitCount(models.Model):
    """Visits on one day, from one source, to one landing page.

    The unique constraint is what makes this a tally rather than a log: a second
    visit from the same source on the same day increments the row instead of
    adding one.
    """
    date = models.DateField(db_index=True)

    # Mirrors the fields on AssessmentSubmission deliberately, so a channel here
    # and a channel there mean the same thing and the two tables can be read
    # side by side — visits from Meta this month against leads from Meta this
    # month is the conversion rate, and it only works if the buckets match.
    channel = models.CharField(max_length=50, db_index=True)
    utm_source = models.CharField(max_length=200, blank=True)
    utm_medium = models.CharField(max_length=200, blank=True)
    utm_campaign = models.CharField(max_length=200, blank=True)
    landing_path = models.CharField(max_length=200, blank=True)

    visits = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'visit count'
        verbose_name_plural = 'visit counts'
        ordering = ['-date', '-visits']
        constraints = [
            models.UniqueConstraint(
                fields=['date', 'channel', 'utm_source', 'utm_medium',
                        'utm_campaign', 'landing_path'],
                name='unique_visit_bucket',
            ),
        ]
        indexes = [
            models.Index(fields=['-date', 'channel']),
        ]

    def __str__(self):
        return f'{self.date} · {self.source_label} · {self.visits}'

    @property
    def source_label(self):
        parts = [p for p in (self.channel, self.utm_medium, self.utm_campaign) if p]
        return ' · '.join(parts) if parts else 'direct'
