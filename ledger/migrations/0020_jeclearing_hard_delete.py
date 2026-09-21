"""CFO directive 2026-05-26 — escalate voucher-clearing from reversal-JE
to HARD-DELETE of the target JournalEntry, with full JSON snapshot for
audit. Also allow bulk requests (one approval wipes a date-range slice
of a company's GL).

Schema deltas vs migration 0019_jeclearingrequest:

  * ``journal_entry`` becomes nullable + on_delete=SET_NULL
    (was PROTECT, NOT NULL; a bulk request has no single JE).
  * ``bulk_scope``           — JSONField, nullable.
  * ``deleted_je_snapshot``  — JSONField, nullable.
  * ``deleted_count``        — PositiveIntegerField, default 0.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0019_jeclearingrequest'),
    ]

    operations = [
        migrations.AlterField(
            model_name='jeclearingrequest',
            name='journal_entry',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=models.SET_NULL,
                to='ledger.journalentry',
                related_name='clearing_requests',
                help_text='The POSTED JE the maker wants deleted. Null for bulk requests.',
            ),
        ),
        migrations.AddField(
            model_name='jeclearingrequest',
            name='bulk_scope',
            field=models.JSONField(
                null=True,
                blank=True,
                help_text=(
                    'Bulk-wipe scope when journal_entry is NULL. Keys: '
                    'company (UUID), from_date (ISO), to_date (ISO), '
                    'optional source_type.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='jeclearingrequest',
            name='deleted_je_snapshot',
            field=models.JSONField(
                null=True,
                blank=True,
                help_text=(
                    'Frozen JSON dump of the JE(s) deleted on approval. '
                    'Header, lines, and metadata, so an auditor can '
                    'fully reconstruct the entry after the row is gone '
                    'from the GL.'
                ),
            ),
        ),
        migrations.AddField(
            model_name='jeclearingrequest',
            name='deleted_count',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Number of journal entries hard-deleted on approval (1 for single, N for bulk).',
            ),
        ),
    ]
