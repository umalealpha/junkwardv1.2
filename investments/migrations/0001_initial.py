"""Initial migration for investments app."""
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
        ('core', '0001_initial'),
        ('ledger', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Investment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('investment_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('isin_or_ref', models.CharField(blank=True, max_length=80)),
                ('instrument_type', models.CharField(choices=[
                    ('treasury_bill', 'Treasury Bill'),
                    ('govt_bond', 'Government Bond'),
                    ('corp_bond', 'Corporate Bond'),
                    ('equity', 'Equity / Shares'),
                    ('unit_trust', 'Unit Trust / Mutual Fund'),
                    ('fixed_deposit', 'Fixed Deposit'),
                    ('other', 'Other'),
                ], max_length=20)),
                ('classification', models.CharField(choices=[
                    ('fvtpl', 'Fair Value Through P&L'),
                    ('fvoci', 'Fair Value Through OCI'),
                    ('amortised_cost', 'Amortised Cost'),
                ], max_length=20)),
                ('issuer', models.CharField(max_length=200)),
                ('custodian', models.CharField(blank=True, max_length=200)),
                ('face_value', models.DecimalField(decimal_places=2, max_digits=18,
                    validators=[django.core.validators.MinValueValidator(Decimal('0.00'))])),
                ('cost', models.DecimalField(decimal_places=2, max_digits=18,
                    validators=[django.core.validators.MinValueValidator(Decimal('0.00'))])),
                ('current_fair_value', models.DecimalField(decimal_places=2,
                    default=Decimal('0.00'), max_digits=18)),
                ('coupon_rate_percent', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True)),
                ('purchase_date', models.DateField()),
                ('maturity_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[
                    ('open', 'Open'), ('matured', 'Matured'), ('disposed', 'Disposed'),
                ], default='open', max_length=10)),
                ('notes', models.TextField(blank=True)),
                ('currency_code', models.ForeignKey(default='BWP',
                    on_delete=django.db.models.deletion.PROTECT, to='core.currency')),
                ('investment_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='investments_carried',
                    to='ledger.account',
                )),
            ],
            options={
                'ordering': ['-purchase_date', '-investment_number'],
                'indexes': [
                    models.Index(fields=['classification'], name='inv_classif_idx'),
                    models.Index(fields=['status'], name='inv_status_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='InvestmentTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('transaction_number', models.CharField(editable=False, max_length=20, unique=True)),
                ('transaction_type', models.CharField(choices=[
                    ('purchase', 'Purchase'),
                    ('sale', 'Sale'),
                    ('coupon', 'Coupon / Dividend / Interest'),
                    ('fair_value', 'Fair Value Adjustment'),
                    ('maturity', 'Maturity / Redemption'),
                ], max_length=15)),
                ('transaction_date', models.DateField(default=timezone.now)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=18)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(choices=[
                    ('draft', 'Draft'), ('posted', 'Posted'),
                ], default='draft', max_length=10)),
                ('posted_at', models.DateTimeField(blank=True, null=True)),
                ('investment', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='transactions',
                    to='investments.investment',
                )),
                ('cash_account', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='investment_cash_legs',
                    to='ledger.account',
                )),
                ('journal_entry', models.OneToOneField(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='investment_transaction',
                    to='ledger.journalentry',
                )),
                ('posted_by', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='investment_transactions_posted',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-transaction_date', '-transaction_number'],
                'indexes': [
                    models.Index(fields=['transaction_type'], name='invtx_type_idx'),
                    models.Index(fields=['investment', 'status'], name='invtx_inv_status_idx'),
                ],
            },
        ),
    ]
