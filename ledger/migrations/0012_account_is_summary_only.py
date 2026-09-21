# Adds Account.is_summary_only — guards against posting JE lines to
# calculated subtotal accounts (e.g. 4300 Net Earned Premium, 5300 Net
# Claims Incurred). These are MA P&L roll-up labels that exist as scaffold
# accounts but must never accept direct postings — doing so would double-
# count their constituent leaf accounts in the MA P&L roll-up.
#
# Data step: mark 4300 (Net Earned Premium) and 5300 (Net Claims Incurred)
# as summary-only since those are the only two existing accounts in the
# seed CoA that represent calculated subtotals rather than postable leaves.

from django.db import migrations, models


SUMMARY_ONLY_CODES = ['4300', '5300']


def _mark_existing_subtotals(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    Account.objects.filter(code__in=SUMMARY_ONLY_CODES).update(is_summary_only=True)


def _unmark_existing_subtotals(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    Account.objects.filter(code__in=SUMMARY_ONLY_CODES).update(is_summary_only=False)


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0011_account_external_ref'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='is_summary_only',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(_mark_existing_subtotals, _unmark_existing_subtotals),
    ]
