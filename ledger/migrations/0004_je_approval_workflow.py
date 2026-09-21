# Hand-authored migration — adds the approval workflow to JournalEntry:
#   - extends Status with PENDING_APPROVAL and REJECTED
#   - widens status max_length from 10 to 20
#   - adds submitted_by, submitted_at, approved_at, rejection_reason

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0003_journalentry_company'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='journalentry',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('pending_approval', 'Pending Approval'),
                    ('rejected', 'Rejected'),
                    ('posted', 'Posted'),
                    ('reversed', 'Reversed'),
                ],
                default='draft',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='submitted_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='journal_entries_submitted',
                to=settings.AUTH_USER_MODEL,
                help_text='Who submitted the entry for approval.',
            ),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='submitted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='journalentry',
            name='rejection_reason',
            field=models.TextField(
                blank=True, null=True,
                help_text='Reason given by the approver when an entry is rejected.',
            ),
        ),
        migrations.AlterField(
            model_name='journalentry',
            name='approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='journal_entries_approved',
                to=settings.AUTH_USER_MODEL,
                help_text='Who approved the entry. Must differ from created_by '
                          '(segregation of duties).',
            ),
        ),
    ]
