"""Add StaffPointsTransaction.pillar — tag submission-less ledger rows.

CFO directive 2026-07-13: task-performance adjustments post to the ledger with
no StaffSubmission, so they need to carry their pillar directly to appear in the
dashboard's per-pillar "recent" list. Legacy rows stay blank and still attribute
via their submission's feature_code.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('staff_rewards', '0002_rename_staff_submission_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffpointstransaction',
            name='pillar',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
    ]
