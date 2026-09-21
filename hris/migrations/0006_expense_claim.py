"""
0006_expense_claim — ExpenseClaim bridge model.

CFO directive 2026-05-24 — Payroll + HR upgrades pass, item #4.

Bible-check guard: hris/ touch only. The bridge SERVICE (services_expense.py)
calls into billing.Invoice via its public ORM API — no billing migrations.
"""

import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0005_public_holiday'),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ExpenseClaim',
            fields=[
                ('id',         models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('expense_date', models.DateField()),
                ('category',     models.CharField(max_length=80, blank=True, default='')),
                ('amount',       models.DecimalField(max_digits=18, decimal_places=2, default=0)),
                ('currency',     models.CharField(max_length=3, default='BWP')),
                ('attachment',   models.FileField(upload_to='expense_claims/', null=True, blank=True)),
                ('description',  models.TextField(blank=True, default='')),
                ('status', models.CharField(
                    max_length=12,
                    default='draft',
                    choices=[
                        ('draft',     'Draft'),
                        ('submitted', 'Submitted'),
                        ('approved',  'Approved'),
                        ('rejected',  'Rejected'),
                        ('paid',      'Paid'),
                    ],
                )),
                ('reimbursement_method', models.CharField(
                    max_length=10,
                    default='ap',
                    choices=[
                        ('ap',      'Accounts Payable (draft vendor bill)'),
                        ('payroll', 'Payroll (next-period PayslipLine)'),
                    ],
                )),
                ('approved_at', models.DateTimeField(null=True, blank=True)),
                ('profile', models.ForeignKey(
                    on_delete=models.PROTECT,
                    related_name='expense_claims',
                    to='core.userprofile',
                )),
                ('approved_by', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=models.SET_NULL,
                    related_name='expense_claims_approved',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name':        'Expense Claim',
                'verbose_name_plural': 'Expense Claims',
                'ordering': ['-expense_date', '-created_at'],
            },
        ),
    ]
