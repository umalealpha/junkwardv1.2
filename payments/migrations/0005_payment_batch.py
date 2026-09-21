"""
payments 0005 — PaymentBatch + PaymentBatchLine (pay-run).
"""
import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0004_payment_secondary_approved_at_and_more'),
        ('billing', '0001_initial'),
        ('ledger', '0001_initial'),
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentBatch',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('run_date', models.DateField(help_text='The cash-out date stamped onto every Payment created from this batch.')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('proposed', 'Proposed'), ('committed', 'Committed'), ('cancelled', 'Cancelled')], default='draft', max_length=10)),
                ('eft_file_name', models.CharField(blank=True, default='', help_text='Name of the EFT file generated from this batch (populated when EFT export runs).', max_length=200)),
                ('total_bwp', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Sum of included PaymentBatchLine.amount_proposed.', max_digits=18)),
                ('notes', models.TextField(blank=True, default='')),
                ('committed_at', models.DateTimeField(blank=True, null=True)),
                ('cancelled_at', models.DateTimeField(blank=True, null=True)),
                ('bank_account', models.ForeignKey(help_text='Source bank account for every payment in this batch.', on_delete=django.db.models.deletion.PROTECT, related_name='payment_batches', to='ledger.account')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_batches', to='core.company')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='payment_batches_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Payment Batch',
                'verbose_name_plural': 'Payment Batches',
                'ordering': ['-run_date', '-created_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PaymentBatchLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('amount_proposed', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='BWP amount to pay on this bill, net of any early-payment discount taken.', max_digits=18)),
                ('amount_discount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='BWP discount captured if amount_proposed reflects an early-pay discount.', max_digits=18)),
                ('included', models.BooleanField(default=True, help_text='Finance Manager can deselect a line before commit — excluded lines do not produce a Payment.')),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='payments.paymentbatch')),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='pay_run_lines', to='billing.invoice')),
                ('payment', models.ForeignKey(blank=True, help_text='Populated after commit_batch creates the Payment for this line.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='from_pay_run_lines', to='payments.payment')),
            ],
            options={
                'verbose_name': 'Payment Batch Line',
                'verbose_name_plural': 'Payment Batch Lines',
                'ordering': ['created_at'],
                'abstract': False,
            },
        ),
        migrations.AddConstraint(
            model_name='paymentbatchline',
            constraint=models.UniqueConstraint(fields=('batch', 'invoice'), name='uq_payment_batch_line_per_invoice'),
        ),
    ]
