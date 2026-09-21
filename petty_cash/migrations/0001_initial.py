"""Initial migration for petty_cash app."""
import uuid
from decimal import Decimal

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('ledger', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PettyCashLocation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120, unique=True)),
                ('address', models.TextField(blank=True, help_text='Where the cash physically sits.')),
                ('float_amount', models.DecimalField(
                    decimal_places=2, default=Decimal('15000.00'), max_digits=12,
                    validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
                    help_text='Maximum cash float held at this location (Pula).',
                )),
                ('is_active', models.BooleanField(default=True)),
                ('custodian', models.ForeignKey(
                    null=True, blank=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_locations',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('petty_cash_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='petty_cash_float_for',
                    to='ledger.account',
                )),
                ('reimbursing_bank_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='petty_cash_reimburses_from',
                    to='ledger.account',
                )),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='PettyCashReimbursement',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reimbursement_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('reimbursement_date', models.DateField(default=timezone.now)),
                ('total_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('voucher_count', models.PositiveIntegerField(default=0)),
                ('notes', models.TextField(blank=True)),
                ('status', models.CharField(
                    choices=[('draft', 'Draft'), ('posted', 'Posted')],
                    default='draft', max_length=10,
                )),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
                ('location', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='reimbursements', to='petty_cash.pettycashlocation',
                )),
                ('period', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_reimbursements',
                    to='ledger.fiscalperiod',
                )),
                ('journal_entry', models.OneToOneField(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_reimbursement',
                    to='ledger.journalentry',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_reimbursements_created',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('posted_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_reimbursements_posted',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['-reimbursement_date', '-reimbursement_number']},
        ),
        migrations.CreateModel(
            name='PettyCashVoucher',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('voucher_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('voucher_date', models.DateField(default=timezone.now)),
                ('payee', models.CharField(max_length=200)),
                ('amount', models.DecimalField(
                    decimal_places=2, max_digits=10,
                    validators=[django.core.validators.MinValueValidator(Decimal('0.01'))],
                )),
                ('description', models.TextField()),
                ('receipt_reference', models.CharField(blank=True, max_length=120)),
                ('receipt_attached', models.BooleanField(default=False)),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Draft'),
                        ('pending_approval', 'Pending Approval'),
                        ('posted', 'Posted'),
                        ('reimbursed', 'Reimbursed'),
                        ('rejected', 'Rejected'),
                    ],
                    default='draft', max_length=20,
                )),
                ('rejection_reason', models.TextField(blank=True)),
                ('submitted_at', models.DateTimeField(blank=True, null=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('location', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='vouchers', to='petty_cash.pettycashlocation',
                )),
                ('expense_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='petty_cash_vouchers',
                    to='ledger.account',
                )),
                ('reimbursement', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='vouchers',
                    to='petty_cash.pettycashreimbursement',
                )),
                ('journal_entry', models.OneToOneField(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_voucher',
                    to='ledger.journalentry',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_vouchers_created',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('submitted_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_vouchers_submitted',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('approved_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='petty_cash_vouchers_approved',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-voucher_date', '-voucher_number'],
                'indexes': [
                    models.Index(fields=['status'], name='petty_cash_status_idx'),
                    models.Index(fields=['location', 'status'], name='petty_cash_loc_status_idx'),
                    models.Index(fields=['voucher_date'], name='petty_cash_date_idx'),
                ],
            },
        ),
    ]
