"""RealPay Collections module — row-level transaction + response-code tables.

CFO directive 2026-06-05. Backs the Failed/Error Debit Tracker (Objective 1)
and the Collections Dashboard (Objective 2). Hand-authored to mirror exactly
what makemigrations would emit for realpay.models.RealPayResponseCode and
RealPayTransaction (no local Django env available; generated against the model
definitions in realpay/models.py).
"""
import uuid
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('realpay', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='RealPayResponseCode',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product_code', models.CharField(db_index=True, max_length=20)),
                ('response_code', models.CharField(max_length=10)),
                ('code_norm', models.CharField(
                    db_index=True, max_length=10,
                    help_text='Normalized response code (join key).')),
                ('description', models.CharField(blank=True, default='', max_length=200)),
            ],
            options={
                'verbose_name': 'RealPay response code',
                'verbose_name_plural': 'RealPay response codes',
                'ordering': ['product_code', 'code_norm'],
                'abstract': False,
                'unique_together': {('product_code', 'response_code')},
            },
        ),
        migrations.CreateModel(
            name='RealPayTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source', models.CharField(
                    choices=[('transaction', 'Transaction Report'),
                             ('billing', 'Client Billing detail')],
                    db_index=True, max_length=12)),
                ('txn_date', models.DateField(
                    blank=True, null=True, db_index=True,
                    help_text='Installment Date (txn) / Transaction Date (billing).')),
                ('client_number', models.CharField(
                    blank=True, db_index=True, default='', max_length=40)),
                ('client_name', models.CharField(
                    blank=True, default='', max_length=160,
                    help_text='PII — view/export only, never to any AI pipeline.')),
                ('product', models.CharField(
                    blank=True, default='', max_length=40,
                    help_text='RealPay product code (billing only; blank on txn report).')),
                ('beneficiary_number', models.CharField(blank=True, default='', max_length=20)),
                ('merchant', models.CharField(blank=True, default='', max_length=120)),
                ('contract_number', models.CharField(blank=True, default='', max_length=40)),
                ('contract_sequence', models.CharField(blank=True, default='', max_length=20)),
                ('inst_seq', models.CharField(blank=True, default='', max_length=20)),
                ('installment_amount', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('total_amount', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('amount_requested', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('collected_amount', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('current_status', models.CharField(
                    blank=True, db_index=True, default='', max_length=20,
                    help_text='SUCCESSFUL/FAILED/PROCESSING/ERROR/CANCELLED.')),
                ('result_code', models.CharField(blank=True, default='', max_length=20)),
                ('result_code_norm', models.CharField(
                    blank=True, db_index=True, default='', max_length=10)),
                ('client_bank', models.CharField(blank=True, default='', max_length=60)),
                ('import_batch', models.CharField(
                    blank=True, default='', max_length=40,
                    help_text='Importer run tag, for traceability.')),
            ],
            options={
                'verbose_name': 'RealPay transaction',
                'verbose_name_plural': 'RealPay transactions',
                'ordering': ['-txn_date', 'client_number'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='realpayresponsecode',
            index=models.Index(fields=['product_code', 'code_norm'],
                               name='rp_rc_prod_codenorm_idx'),
        ),
        migrations.AddIndex(
            model_name='realpayresponsecode',
            index=models.Index(fields=['code_norm'], name='rp_rc_codenorm_idx'),
        ),
        migrations.AddIndex(
            model_name='realpaytransaction',
            index=models.Index(fields=['source', 'txn_date'], name='rp_txn_src_date_idx'),
        ),
        migrations.AddIndex(
            model_name='realpaytransaction',
            index=models.Index(fields=['source', 'current_status'], name='rp_txn_src_status_idx'),
        ),
        migrations.AddIndex(
            model_name='realpaytransaction',
            index=models.Index(fields=['txn_date'], name='rp_txn_date_idx'),
        ),
        migrations.AddIndex(
            model_name='realpaytransaction',
            index=models.Index(fields=['client_number'], name='rp_txn_client_idx'),
        ),
        migrations.AddIndex(
            model_name='realpaytransaction',
            index=models.Index(fields=['result_code_norm'], name='rp_txn_codenorm_idx'),
        ),
    ]
