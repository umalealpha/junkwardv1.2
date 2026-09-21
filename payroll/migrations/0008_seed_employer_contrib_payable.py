"""
payroll 0008 — Seed CoA 2151 "Employer Contributions Payable".

CFO directive 2026-06-05. Employer-cost payroll components
(COMPANY_CONTRIBUTION — e.g. employer pension, training levy) debit an expense
but are not withheld from the employee's net pay, so they need their own
liability credit. Without this account, post_payroll_period() could not balance
the JE and payroll with any employer contribution failed to post.

Idempotent (get_or_create) — no-op where the account already exists.
"""
from django.db import migrations


def _seed_contrib_payable(apps, schema_editor):
    Account = apps.get_model('ledger', 'Account')
    if Account.objects.filter(code='2151').exists():
        return
    Account.objects.create(
        code='2151',
        name='Employer Contributions Payable',
        account_type='liability',
        sub_type='current_liability',
        # NOTE: `statement_class` was a field on Account when this migration was
        # first written; it has since been removed from the model (reporting now
        # buckets by account_type / fs_line_item). Passing it crashed a FRESH-DB
        # migrate (CI hard-gate red on every push 2026-06-27) with
        # "Account() got unexpected keyword arguments: 'statement_class'". Prod
        # already ran 0008 so it is unaffected; dropped here to unbreak fresh builds.
        is_bank_account=False,
        is_active=True,
        description=(
            'Employer-side payroll contributions owed to funds / authorities '
            '(e.g. employer pension match, training levy). Credited from '
            'payroll.services.post_payroll_period() for COMPANY_CONTRIBUTION '
            'components; cleared when the contribution is remitted.'
        ),
    )


def _noop_reverse(apps, schema_editor):
    """Leave the account in place on reverse — safer than nuking GL rows."""
    return


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0007_employee_housing_benefit'),
        ('ledger', '0001_initial'),
        # Account.currency_code defaults to 'BWP' (FK, PROTECT) — currencies
        # must be seeded before any Account is created on a fresh DB.
        ('core', '0021_seed_currencies'),
    ]

    operations = [
        migrations.RunPython(_seed_contrib_payable, _noop_reverse),
    ]
