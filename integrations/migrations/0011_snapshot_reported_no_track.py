"""Add TimeDoctorDailySnapshot.reported_no_track for the next-day correction.

Hand-written to add ONLY the new field (this app's makemigrations also emits
pre-existing drift). CFO 2026-08-01.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0010_snapshot_settle_samples'),
    ]

    operations = [
        migrations.AddField(
            model_name='timedoctordailysnapshot',
            name='reported_no_track',
            field=models.JSONField(
                default=list, blank=True,
                help_text='td_user_ids named as did-not-track (for next-day correction).'),
        ),
    ]
