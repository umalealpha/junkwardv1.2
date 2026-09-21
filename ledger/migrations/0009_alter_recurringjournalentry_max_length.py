# Sibling to 0008 — RecurringJournalEntry.journal_type also uses the
# JournalType enum, and 'commitment_reversal' is 19 chars. Without this
# AlterField the system check (fields.E009) blocks Django startup.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0008_journaltype_payroll_commitment'),
    ]

    operations = [
        migrations.AlterField(
            model_name='recurringjournalentry',
            name='journal_type',
            field=models.CharField(
                choices=[
                    ('sales', 'Sales'),
                    ('purchases', 'Purchases'),
                    ('cash_receipts', 'Cash Receipts'),
                    ('cash_payments', 'Cash Payments'),
                    ('bank', 'Bank'),
                    ('general', 'General'),
                    ('payroll', 'Payroll'),
                    ('commitment', 'PO Commitment'),
                    ('commitment_reversal', 'PO Commitment Reversal'),
                ],
                default='general',
                max_length=25,
            ),
        ),
    ]
