"""PayslipComponent.is_recurring — tells a salary from a one-off.

The month roll-forward (payroll/amendment_views.apply_amendment_batch, step 1)
copied every baseline payslip line into the next period and excluded exactly
one component code, LOAN_REPAYMENT. Nothing on the component said whether a
line was recurring pay or a one-off, so COMMISSION / INCENTIVE / LEAVE_PAY /
SEVERANCE were PAID AGAIN the following month (2026-09-20).

Defaults True, so every existing component keeps its current behaviour. The
data step marks only the known one-off codes; anything the CFO adds later is
recurring until somebody says otherwise.
"""
from django.db import migrations, models


ONE_OFF_CODES = [
    'COMMISSION', 'INCENTIVE', 'LEAVE_PAY', 'SEVERANCE',
    'BONUS', 'OT', 'ARREARS', 'REIMBURSEMENT',
]


def mark_one_offs(apps, schema_editor):
    PayslipComponent = apps.get_model('payroll', 'PayslipComponent')
    PayslipComponent.objects.filter(code__in=ONE_OFF_CODES).update(is_recurring=False)


def unmark_one_offs(apps, schema_editor):
    PayslipComponent = apps.get_model('payroll', 'PayslipComponent')
    PayslipComponent.objects.filter(code__in=ONE_OFF_CODES).update(is_recurring=True)


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0029_employmentcontract_retirement_fund_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='payslipcomponent',
            name='is_recurring',
            field=models.BooleanField(
                default=True,
                help_text='If True this component rolls forward into the next '
                          'period with the baseline copy. Set False for ONE-OFF '
                          'pay (commission, incentive, leave pay, severance, '
                          'bonus, overtime, arrears, reimbursement) — it belongs '
                          'to the month it was earned and must never be re-paid.',
            ),
        ),
        migrations.RunPython(mark_one_offs, unmark_one_offs),
    ]
