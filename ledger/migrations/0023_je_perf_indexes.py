"""High-volume readiness — composite indexes on JournalEntry hot-path.

Reports aggregate JournalEntryLine filtered by journal_entry__{status,
company, entry_date}. At 18k rows this is fast on a seq-scan; as daily
transaction volume grows into the millions it degrades. These composite
indexes keep the per-company and all-company report filters index-driven.

AddIndex on the current row count is a sub-second operation; applied via the
entrypoint migrate on deploy.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0022_bs_typo_account_renames'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='journalentry',
            index=models.Index(
                fields=['company', 'status', 'entry_date'],
                name='je_company_status_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='journalentry',
            index=models.Index(
                fields=['status', 'entry_date'],
                name='je_status_date_idx',
            ),
        ),
    ]
