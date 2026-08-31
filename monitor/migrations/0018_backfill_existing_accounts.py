"""Grandfather every account that existed before onboarding did.

Email verification and the onboarding wizard are new obligations. Applying them
retrospectively would lock out every current user — including the platform's own
staff — behind a verification email for an address that was confirmed by other
means years ago, and behind consent steps for documents that did not exist when
they signed up.

So on the way in, every existing user is marked verified and given an
``OnboardingProgress`` row already at ``complete`` with ``is_legacy=True``. The
flag is what tells an administrator that this account never actually walked
through the flow, which matters when auditing consent: a legacy account has no
consent records, and that is a fact about the account rather than a gap in the
data. Whether historical users must be asked to accept the new documents is a
business and legal decision, and the ``is_legacy`` flag is what makes it possible
to find them and ask.

Accounts created after this migration start at ``registered`` with
``email_verified=False`` and go through the flow normally.
"""
from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    User = apps.get_model('monitor', 'User')
    OnboardingProgress = apps.get_model('monitor', 'OnboardingProgress')

    now = timezone.now()
    stamp = now.isoformat()

    User.objects.filter(email_verified=False).update(email_verified=True, email_verified_at=now)

    existing = set(OnboardingProgress.objects.values_list('user_id', flat=True))
    OnboardingProgress.objects.bulk_create([
        OnboardingProgress(
            user_id=user_id,
            state='complete',
            is_legacy=True,
            history={'complete': stamp},
            completed_at=now,
        )
        for user_id in User.objects.exclude(id__in=existing).values_list('id', flat=True)
    ])


def unbackfill(apps, schema_editor):
    OnboardingProgress = apps.get_model('monitor', 'OnboardingProgress')
    OnboardingProgress.objects.filter(is_legacy=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0017_user_country_user_email_verified_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
