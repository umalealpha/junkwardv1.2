"""
billing 0008 — PaymentTerm + PaymentTermLine + Contact.payment_term FK.

Adds multi-instalment & early-payment-discount support.

The existing int Contact.payment_terms_days is preserved for backwards
compatibility — Contact.payment_term (nullable FK) is the new path.
"""
import uuid
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0007_contact_company'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentTerm',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('description', models.TextField(blank=True, default='')),
                ('is_default', models.BooleanField(default=False, help_text='True for the system-wide default term. Exactly one row should carry this flag.')),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={
                'verbose_name': 'Payment Term',
                'verbose_name_plural': 'Payment Terms',
                'ordering': ['name'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='PaymentTermLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sequence_no', models.PositiveSmallIntegerField(default=1)),
                ('pct', models.DecimalField(decimal_places=4, default=Decimal('100.0000'), help_text='Percentage of invoice total this line represents.', max_digits=7)),
                ('days_after_issue', models.PositiveIntegerField(default=30)),
                ('discount_pct', models.DecimalField(decimal_places=4, default=Decimal('0.00'), help_text='Discount the payer takes if paid within discount_days. Zero = no discount.', max_digits=7)),
                ('discount_days', models.PositiveIntegerField(default=0, help_text='Days after issue_date the discount expires.')),
                ('label', models.CharField(blank=True, default='', max_length=120)),
                ('term', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='billing.paymentterm')),
            ],
            options={
                'verbose_name': 'Payment Term Line',
                'verbose_name_plural': 'Payment Term Lines',
                'ordering': ['term', 'sequence_no'],
                'abstract': False,
            },
        ),
        migrations.AddConstraint(
            model_name='paymenttermline',
            constraint=models.UniqueConstraint(fields=('term', 'sequence_no'), name='uq_payment_term_line_seq'),
        ),
        migrations.AddField(
            model_name='contact',
            name='payment_term',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='contacts',
                to='billing.paymentterm',
                help_text='Multi-instalment / early-pay-discount term. '
                          'Overrides payment_terms_days when set.',
            ),
        ),
    ]
