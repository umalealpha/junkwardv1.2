# Smart Entry SoD additions (CFO/Oprah directive 2026-05-28):
# adds RETURNED status to JournalEntry. Pure choice-set extension (no DB schema
# change — choices live in Python). Migration recorded for traceability.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0020_jeclearing_hard_delete'),
    ]

    operations = [
        migrations.AlterField(
            model_name='journalentry',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('pending_approval', 'Pending Approval'),
                    ('returned', 'Returned for Correction'),
                    ('rejected', 'Rejected'),
                    ('posted', 'Posted'),
                    ('reversed', 'Reversed'),
                ],
                default='draft', max_length=20),
        ),
    ]
