"""PAYE on a leave encashment (CFO directive 2026-08-17).

Three additive, defaulted columns PLUS a mandatory backfill.

⚠️  The backfill is NOT optional. Prod carries live rows (checked 2026-08-17:
    6 rows — 1 pending CFO, 3 pending HR, 1 paid, 1 rejected). Leaving them on
    the 0.00 default would make every existing application render and EMAIL as
    "net payable P0.00", and Finance could load zero for an approved payout.
    So every pre-existing row gets net_amount = amount, tax_amount = 0: that is
    the literal truth about those rows — no PAYE was withheld on them — and it
    silently reprices nothing.

    In-flight rows are then taxed correctly by the separate, deliberate
    `reprice_untaxed_encashments` command, run AFTER `seed_burs_2026_2027` so it
    uses the 27.5 % schedule. Keeping that out of the migration means the
    repricing is visible, reviewable and dry-runnable instead of a silent
    side effect of a container restart.

Hand-written rather than `makemigrations` output because main carries
pre-existing help_text/index drift in core/payroll/hris that autogeneration
would sweep in (see project_leave_encashment landmine).
"""
from decimal import Decimal

from django.db import migrations, models


def backfill_net_equals_gross(apps, schema_editor):
    """Existing rows had no PAYE withheld — state that, rather than leaving a
    net of 0.00 that the UI and the approval emails would show as the payable."""
    LeaveEncashment = apps.get_model('hris', 'LeaveEncashment')
    LeaveEncashment.objects.update(
        tax_base=Decimal('0.00'),
        tax_amount=Decimal('0.00'),
    )
    # net = amount, per row (F-expression so it works on any row count).
    from django.db.models import F
    LeaveEncashment.objects.update(net_amount=F('amount'))


def unbackfill(apps, schema_editor):
    """Reverse leg — the columns are dropped by the AddField reversals anyway,
    so there is nothing to undo. Present so the migration is reversible."""
    return None


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0067_merge_disciplinary_and_corating'),
    ]

    operations = [
        migrations.AddField(
            model_name='leaveencashment',
            name='tax_base',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=18,
                help_text='Monthly taxable pay (BWP) the payout was stacked on '
                          'to find the marginal rate.'),
        ),
        migrations.AddField(
            model_name='leaveencashment',
            name='tax_amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=18,
                help_text='PAYE withheld on the payout (BWP).'),
        ),
        migrations.AddField(
            model_name='leaveencashment',
            name='net_amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=18,
                help_text='amount − tax_amount (BWP) — what the employee is '
                          'actually paid.'),
        ),
        # `amount` is now explicitly the GROSS. Help-text only — no column change
        # — but `makemigrations --check` is a hard CI gate, so it must be here.
        migrations.AlterField(
            model_name='leaveencashment',
            name='amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=18,
                help_text='daily_rate × days (BWP) — the GROSS payout.'),
        ),
        migrations.RunPython(backfill_net_equals_gross, unbackfill),
    ]
