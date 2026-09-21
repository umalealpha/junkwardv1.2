from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    """Payslip source currency (CFO 2026-06-20): keep gross/paye/net/ctc as the
    BWP reporting figures; add the original-currency amounts so entities paid in
    another currency (e.g. ADRisk = INR) can issue a local-currency payslip.
    Additive + nullable/defaulted — no change to existing rows or reports."""

    dependencies = [
        ('payroll', '0008_seed_employer_contrib_payable'),
    ]

    operations = [
        migrations.AddField(
            model_name='payslip',
            name='source_currency',
            field=models.CharField(default='BWP', max_length=3,
                help_text='ISO currency the employee is actually paid in (e.g. INR for ADRisk).'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='source_gross',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True,
                help_text='Gross in the source currency (None = same as gross_amount, BWP).'),
        ),
        migrations.AddField(
            model_name='payslip',
            name='source_paye',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name='payslip',
            name='source_net',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True),
        ),
        migrations.AddField(
            model_name='payslip',
            name='fx_rate_to_bwp',
            field=models.DecimalField(decimal_places=8, default=Decimal('1'), max_digits=18,
                help_text='BWP per 1 unit of source currency (e.g. INR→BWP ≈ 0.142857 at 7:1).'),
        ),
    ]
