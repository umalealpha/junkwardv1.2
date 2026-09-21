# Hand-authored — adds related-party flags to JournalEntry and JournalEntryLine.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0003_contact_related_party'),
        ('ledger', '0005_attachments_and_recurring_je'),
    ]

    operations = [
        migrations.AddField(
            model_name='journalentry',
            name='is_related_party',
            field=models.BooleanField(
                blank=True, default=None, null=True,
                help_text='IAS 24 classification: True if any line on this entry transacts '
                          'with a related party. MUST be set before the entry can be submitted '
                          'for approval — no default, no implicit answer.',
            ),
        ),
        migrations.AddField(
            model_name='journalentryline',
            name='is_related_party',
            field=models.BooleanField(
                default=False,
                help_text='Auto-set from contact.is_related_party at posting time, but can be '
                          'manually overridden at the line level for cases where a single JE '
                          'mixes related and unrelated parties.',
            ),
        ),
    ]
