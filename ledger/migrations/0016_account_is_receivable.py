"""
ledger.0016_account_is_receivable

Adds Account.is_receivable boolean — the single source of truth used
by /api/v1/reports/receivables-summary/, the dashboard "Total
Receivables" tile, the BS "Receivables" line, AR aging, and any other
report that wants to sum receivables.

CFO directive 2026-05-24: "all the tables should talk the same".

Backfill is performed in a separate management command
(`backfill_receivables_flag`) so DBAs can re-run it on subsidiary
companies without rolling another schema migration.

Bank-classification fix lives in the same migration so the two
related cleanups ship as one unit (CFO instruction: "fix in same PR").
"""
from django.db import migrations, models


def _backfill(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')

    # Receivables prefixes — derived from the CFO's MA layout +
    # 24-May-2026 live GL inspection on ADIC.
    RECEIVABLE_PREFIXES = ('201', '202', '203', '208', '212', '240', '260')

    upd_recv = 0
    for acc in Account.objects.filter(account_type='asset'):
        if any(acc.code.startswith(p) for p in RECEIVABLE_PREFIXES):
            if not acc.is_receivable:
                acc.is_receivable = True
                acc.save(update_fields=['is_receivable'])
                upd_recv += 1

    # Bank reclassification — 280xxx accounts found posting cash but
    # sitting in other_assets per legacy BS section split. Same PR
    # per CFO ask. Only nudge `sub_type` if it isn't already 'bank',
    # so subsidiary companies with non-standard codes are untouched.
    upd_bank = 0
    for acc in Account.objects.filter(code__startswith='280', account_type='asset'):
        if acc.sub_type != 'bank':
            acc.sub_type = 'bank'
            if not acc.is_bank_account:
                acc.is_bank_account = True
                acc.save(update_fields=['sub_type', 'is_bank_account'])
            else:
                acc.save(update_fields=['sub_type'])
            upd_bank += 1

    print(f'[ledger.0016] is_receivable=True set on {upd_recv} accounts')
    print(f'[ledger.0016] reclassified {upd_bank} bank accounts (280xxx)')


def _noop_reverse(apps, schema_editor):
    # Schema rollback removes the column; no per-row reversal needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0015_fiscal_year_and_period_start'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='is_receivable',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RunPython(_backfill, _noop_reverse),
    ]
