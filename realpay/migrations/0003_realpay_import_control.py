"""RealPay import control totals (forensic-audit fix 2026-06-05).

Adds RealPayImportControl so the Collections Dashboard can reconcile its
computed grand total against the row count + collected sum actually loaded
from Client Billing (detects silent import loss). Hand-authored to mirror the
model in realpay/models.py.
"""
import uuid
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('realpay', '0002_realpay_collections'),
    ]

    operations = [
        migrations.CreateModel(
            name='RealPayImportControl',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('source', models.CharField(db_index=True, max_length=12)),
                ('batch', models.CharField(blank=True, default='', max_length=40)),
                ('row_count', models.PositiveIntegerField(default=0)),
                ('settled_count', models.PositiveIntegerField(
                    default=0, help_text='Rows with collected_amount > 0.')),
                ('collected_sum', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'), max_digits=18,
                    help_text='Sum of collected_amount over settled rows at load time.')),
            ],
            options={
                'verbose_name': 'RealPay import control',
                'verbose_name_plural': 'RealPay import controls',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
    ]
