"""
0006_contracts_loans_formula — payroll + HR upgrades pass.

CFO directive 2026-05-24:
  - EmploymentContract       (one row per employee per contract window)
  - EmployeeLoan             (loan principal facility)
  - LoanRepayment            (one row per loan per period)
  - PayslipComponent.computation_kind + .formula  (formula engine fields)

Bible-check guard: payroll/ only. No ledger / reporting / billing changes.
"""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0005_payroll_amendments'),
        ('hris',    '0005_public_holiday'),
        ('core',    '0001_initial'),
    ]

    operations = [
        # ------------------------------------------------------------------
        # PayslipComponent — formula engine fields
        # ------------------------------------------------------------------
        migrations.AddField(
            model_name='payslipcomponent',
            name='computation_kind',
            field=models.CharField(
                max_length=12,
                default='fixed',
                choices=[
                    ('fixed',      'Fixed amount (no formula)'),
                    ('percent_of', 'Percent of another line'),
                    ('formula',    'Custom safe formula'),
                ],
            ),
        ),
        migrations.AddField(
            model_name='payslipcomponent',
            name='formula',
            field=models.CharField(max_length=200, blank=True, default=''),
        ),

        # ------------------------------------------------------------------
        # EmploymentContract
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name='EmploymentContract',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('start_date', models.DateField()),
                ('end_date',   models.DateField(null=True, blank=True)),
                ('basic',      models.DecimalField(max_digits=18, decimal_places=2, default=0)),
                ('frequency',  models.CharField(
                    max_length=10,
                    default='monthly',
                    choices=[
                        ('monthly',  'Monthly'),
                        ('biweekly', 'Bi-weekly'),
                        ('weekly',   'Weekly'),
                    ],
                )),
                ('status',     models.CharField(
                    max_length=12,
                    default='active',
                    choices=[
                        ('active',     'Active'),
                        ('terminated', 'Terminated'),
                        ('pending',    'Pending'),
                    ],
                )),
                ('allowance_template', models.JSONField(default=dict, blank=True)),
                ('employee',     models.ForeignKey(
                    on_delete=models.PROTECT,
                    related_name='contracts',
                    to='payroll.employee',
                )),
                ('currency_code', models.ForeignKey(
                    on_delete=models.PROTECT,
                    related_name='employment_contracts',
                    default='BWP',
                    to='core.currency',
                )),
                ('grade',        models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.SET_NULL,
                    related_name='contracts',
                    to='hris.grade',
                )),
            ],
            options={
                'verbose_name': 'Employment Contract',
                'verbose_name_plural': 'Employment Contracts',
                'ordering': ['-start_date', 'employee__full_name'],
            },
        ),

        # ------------------------------------------------------------------
        # EmployeeLoan
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name='EmployeeLoan',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('principal',       models.DecimalField(max_digits=18, decimal_places=2, default=0)),
                ('annual_rate_pct', models.DecimalField(max_digits=6,  decimal_places=3, default=0)),
                ('term_months',     models.PositiveIntegerField(default=12)),
                ('status',          models.CharField(
                    max_length=12,
                    default='active',
                    choices=[
                        ('active',    'Active'),
                        ('paid',      'Paid in full'),
                        ('cancelled', 'Cancelled'),
                    ],
                )),
                ('outstanding',     models.DecimalField(max_digits=18, decimal_places=2, default=0)),
                ('employee', models.ForeignKey(
                    on_delete=models.PROTECT,
                    related_name='loans',
                    to='payroll.employee',
                )),
                ('start_period', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.PROTECT,
                    related_name='loans_starting_here',
                    to='payroll.payrollperiod',
                )),
            ],
            options={
                'verbose_name': 'Employee Loan',
                'verbose_name_plural': 'Employee Loans',
                'ordering': ['-created_at'],
            },
        ),

        # ------------------------------------------------------------------
        # LoanRepayment
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name='LoanRepayment',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('principal',  models.DecimalField(max_digits=18, decimal_places=2, default=0)),
                ('interest',   models.DecimalField(max_digits=18, decimal_places=2, default=0)),
                ('posted_at',  models.DateTimeField(null=True, blank=True)),
                ('loan', models.ForeignKey(
                    on_delete=models.CASCADE,
                    related_name='repayments',
                    to='payroll.employeeloan',
                )),
                ('period', models.ForeignKey(
                    on_delete=models.PROTECT,
                    related_name='loan_repayments',
                    to='payroll.payrollperiod',
                )),
            ],
            options={
                'verbose_name':        'Loan Repayment',
                'verbose_name_plural': 'Loan Repayments',
                'ordering': ['period__start_date', 'loan__employee__full_name'],
                'unique_together': {('loan', 'period')},
            },
        ),
    ]
