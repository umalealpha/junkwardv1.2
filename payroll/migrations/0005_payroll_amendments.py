"""
0005_payroll_amendments — PayrollAmendmentBatch + PayrollAmendment.

CFO directive 2026-05-21: monthly payroll = last month's baseline +
amendments uploaded as an xlsx. Models capture each change.
"""

import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0004_payrollperiod_status_posted_journal_entry'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PayrollAmendmentBatch',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file_name',  models.CharField(max_length=200, blank=True, default='')),
                ('raw_xlsx',   models.FileField(upload_to='payroll/amendments/', null=True, blank=True)),
                ('row_count',  models.PositiveIntegerField(default=0)),
                ('status',     models.CharField(
                    max_length=12,
                    default='parsed',
                    choices=[
                        ('parsed',   'Parsed (preview)'),
                        ('applied',  'Applied to period'),
                        ('rejected', 'Rejected'),
                    ],
                )),
                ('notes',      models.TextField(blank=True, default='')),
                ('applied_at', models.DateTimeField(null=True, blank=True)),
                ('target_period', models.ForeignKey(
                    on_delete=models.PROTECT,
                    related_name='amendment_batches',
                    to='payroll.payrollperiod',
                )),
                ('baseline_period', models.ForeignKey(
                    on_delete=models.PROTECT,
                    related_name='used_as_baseline',
                    to='payroll.payrollperiod',
                )),
                ('company', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.PROTECT,
                    related_name='payroll_amendment_batches',
                    to='core.company',
                )),
                ('uploaded_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.SET_NULL,
                    related_name='payroll_amendment_batches',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Payroll Amendment Batch',
                'verbose_name_plural': 'Payroll Amendment Batches',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PayrollAmendment',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee_ref', models.CharField(max_length=120, blank=True, default='')),
                ('kind', models.CharField(
                    max_length=20,
                    choices=[
                        ('hire',              'New hire'),
                        ('terminate',         'Termination'),
                        ('salary_change',     'Salary change (basic)'),
                        ('allowance_add',     'Add / change allowance'),
                        ('allowance_remove',  'Remove allowance'),
                        ('deduction_add',     'Add / change deduction'),
                        ('deduction_remove',  'Remove deduction'),
                        ('bonus',             'One-off bonus'),
                        ('overtime',          'Overtime'),
                        ('arrears',           'Arrears / back-pay'),
                        ('tax_override',      'PAYE override'),
                        ('other',             'Other (free-text reason)'),
                    ],
                )),
                ('amount',           models.DecimalField(max_digits=18, decimal_places=2, default=0)),
                ('reason',           models.CharField(max_length=300, blank=True, default='')),
                ('approver',         models.CharField(max_length=120, blank=True, default='')),
                ('applied',          models.BooleanField(default=False)),
                ('resolution_error', models.CharField(max_length=300, blank=True, default='')),
                ('batch', models.ForeignKey(
                    on_delete=models.CASCADE,
                    related_name='amendments',
                    to='payroll.payrollamendmentbatch',
                )),
                ('employee', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.PROTECT,
                    related_name='payroll_amendments',
                    to='payroll.employee',
                )),
                ('component', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.PROTECT,
                    related_name='amendments',
                    to='payroll.payslipcomponent',
                )),
            ],
            options={
                'verbose_name': 'Payroll Amendment',
                'verbose_name_plural': 'Payroll Amendments',
                'ordering': ['batch', 'employee__full_name', 'kind'],
            },
        ),
    ]
