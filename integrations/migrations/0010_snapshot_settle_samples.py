"""Add TimeDoctorDailySnapshot.settle_samples for the people-data guardrail.

Hand-written to add ONLY the new field — makemigrations on this app also emits
pre-existing help_text/index drift in core/payroll/hris (see the leave-encashment
migration note); this keeps the change surgical. CFO 2026-08-01.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0009_sync_model_state_2026_07_26'),
    ]

    operations = [
        migrations.AddField(
            model_name='timedoctordailysnapshot',
            name='settle_samples',
            field=models.JSONField(
                default=dict, blank=True,
                help_text='{pull_slot: {td_user_id: tracked_seconds}} for the settle gate.'),
        ),
    ]
