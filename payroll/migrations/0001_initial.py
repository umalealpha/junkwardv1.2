# Hand-authored initial migration for the payroll app.

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0003_userprofile_title_isadmin'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Employee ──────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='Employee',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee_number', models.CharField(blank=True, max_length=30, unique=True)),
                ('full_name', models.CharField(max_length=200)),
                ('department', models.CharField(blank=True, default='', max_length=100)),
                ('job_title', models.CharField(blank=True, default='', max_length=100)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('phone', models.CharField(blank=True, default='', max_length=50)),
                ('national_id', models.CharField(blank=True, default='', max_length=50)),
                ('hire_date', models.DateField(blank=True, null=True)),
                ('termination_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('active', 'Active'), ('on_leave', 'On leave'),
                        ('suspended', 'Suspended'), ('terminated', 'Terminated'),
                    ],
                    default='active', max_length=15,
                )),
                ('bank_name', models.CharField(blank=True, default='', max_length=100)),
                ('bank_account_no', models.CharField(blank=True, default='', max_length=50)),
                ('bank_branch', models.CharField(blank=True, default='', max_length=100)),
                ('external_ref', models.CharField(blank=True, default='', max_length=100)),
                ('company', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='employees', to='core.company')),
            ],
            options={'verbose_name': 'Employee', 'verbose_name_plural': 'Employees',
                     'ordering': ['full_name'], 'abstract': False},
        ),
        # ── TaxBracket ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='TaxBracket',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=80)),
                ('effective_from', models.DateField()),
                ('lower_bound', models.DecimalField(decimal_places=2, max_digits=18)),
                ('upper_bound', models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ('base_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('rate_pct', models.DecimalField(decimal_places=2, max_digits=5)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'verbose_name': 'Tax Bracket (PAYE)', 'verbose_name_plural': 'Tax Brackets (PAYE)',
                     'ordering': ['effective_from', 'lower_bound'], 'abstract': False},
        ),
        # ── PayslipComponent ──────────────────────────────────────────────────
        migrations.CreateModel(
            name='PayslipComponent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code', models.CharField(max_length=40, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('kind', models.CharField(
                    choices=[
                        ('earning', 'Earning (taxable)'),
                        ('earning_non_taxable', 'Earning (non-taxable)'),
                        ('employee_deduction', 'Employee deduction (post-tax)'),
                        ('employee_pretax', 'Employee deduction (pre-tax)'),
                        ('company_contribution', 'Company contribution (employer cost)'),
                        ('tax', 'Tax (PAYE)'),
                        ('computed_gross', 'Computed: Gross'),
                        ('computed_net', 'Computed: Net Salary'),
                        ('computed_ctc', 'Computed: Cost to Company'),
                    ],
                    max_length=25,
                )),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('is_taxable', models.BooleanField(default=False)),
                ('posting_account_code', models.CharField(blank=True, default='', max_length=20)),
            ],
            options={'verbose_name': 'Payslip Component', 'verbose_name_plural': 'Payslip Components',
                     'ordering': ['sort_order', 'name'], 'abstract': False},
        ),
        # ── PayrollPeriod ─────────────────────────────────────────────────────
        migrations.CreateModel(
            name='PayrollPeriod',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('period_name', models.CharField(max_length=10, unique=True)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('pay_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(
                    choices=[
                        ('open', 'Open'),
                        ('locked', 'Locked (calculated, awaiting approval)'),
                        ('approved', 'Approved (ready to pay)'),
                        ('paid', 'Paid'),
                    ],
                    default='open', max_length=15,
                )),
                ('notes', models.TextField(blank=True, default='')),
            ],
            options={'verbose_name': 'Payroll Period', 'verbose_name_plural': 'Payroll Periods',
                     'ordering': ['-start_date'], 'abstract': False},
        ),
        # ── Payslip ───────────────────────────────────────────────────────────
        migrations.CreateModel(
            name='Payslip',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('gross_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('paye_amount',  models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('net_amount',   models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('ctc_amount',   models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Draft'), ('approved', 'Approved'),
                        ('paid', 'Paid'), ('cancelled', 'Cancelled'),
                    ],
                    default='draft', max_length=12,
                )),
                ('notes', models.TextField(blank=True, default='')),
                ('company', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='payslips', to='core.company')),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='payslips', to='payroll.employee')),
                ('period', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='payslips', to='payroll.payrollperiod')),
            ],
            options={
                'verbose_name': 'Payslip', 'verbose_name_plural': 'Payslips',
                'ordering': ['-period__start_date', 'employee__full_name'],
                'unique_together': {('employee', 'period')},
                'abstract': False,
            },
        ),
        # ── PayslipLine ───────────────────────────────────────────────────────
        migrations.CreateModel(
            name='PayslipLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('notes', models.CharField(blank=True, default='', max_length=300)),
                ('component', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='lines', to='payroll.payslipcomponent')),
                ('payslip', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='lines', to='payroll.payslip')),
            ],
            options={
                'verbose_name': 'Payslip Line', 'verbose_name_plural': 'Payslip Lines',
                'ordering': ['component__sort_order'],
                'unique_together': {('payslip', 'component')},
                'abstract': False,
            },
        ),
        # ── PayrollImportBatch ────────────────────────────────────────────────
        migrations.CreateModel(
            name='PayrollImportBatch',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source', models.CharField(default='odoo', max_length=40)),
                ('file_name', models.CharField(blank=True, default='', max_length=255)),
                ('rows_total', models.PositiveIntegerField(default=0)),
                ('rows_valid', models.PositiveIntegerField(default=0)),
                ('rows_invalid', models.PositiveIntegerField(default=0)),
                ('rows_skipped_dup', models.PositiveIntegerField(default=0)),
                ('rows_imported', models.PositiveIntegerField(default=0)),
                ('parsed_rows', models.JSONField(blank=True, default=list)),
                ('validation_errors', models.JSONField(blank=True, default=list)),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Draft (preview)'),
                        ('partially_approved', 'Awaiting second approval'),
                        ('approved', 'Fully approved — ready to commit'),
                        ('committed', 'Committed'),
                        ('rejected', 'Rejected'),
                        ('failed', 'Failed'),
                    ],
                    default='draft', max_length=20,
                )),
                ('first_approved_at', models.DateTimeField(blank=True, null=True)),
                ('second_approved_at', models.DateTimeField(blank=True, null=True)),
                ('rejected_at', models.DateTimeField(blank=True, null=True)),
                ('rejection_reason', models.TextField(blank=True, default='')),
                ('committed_at', models.DateTimeField(blank=True, null=True)),
                ('company', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='payroll_import_batches', to='core.company')),
                ('period', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='import_batches', to='payroll.payrollperiod')),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='payroll_import_batches', to=settings.AUTH_USER_MODEL)),
                ('first_approved_by', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='payroll_imports_first_approved', to=settings.AUTH_USER_MODEL)),
                ('second_approved_by', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='payroll_imports_second_approved', to=settings.AUTH_USER_MODEL)),
                ('rejected_by', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='payroll_imports_rejected', to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name': 'Payroll Import Batch', 'verbose_name_plural': 'Payroll Import Batches',
                     'ordering': ['-created_at'], 'abstract': False},
        ),
    ]
