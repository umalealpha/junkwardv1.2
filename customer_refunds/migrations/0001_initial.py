import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('payments', '__first__'),
        ('fnb', '0003_fnbbatchsubmission_payments_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerRefund',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('segment', models.CharField(choices=[('mis', 'MIS / UniCoin (micro-insurance)'), ('domestic', 'Domestic (personal lines)'), ('commercial', 'Commercial (business lines)')], db_index=True, default='mis', max_length=12)),
                ('graphite_ref', models.CharField(db_index=True, help_text='Graphite refund_requests id — idempotency key.', max_length=64, unique=True)),
                ('policy_number', models.CharField(db_index=True, max_length=64)),
                ('product_name', models.CharField(blank=True, default='', max_length=191)),
                ('customer_name', models.CharField(blank=True, default='', max_length=191)),
                ('agent_name', models.CharField(blank=True, default='', max_length=191)),
                ('reason', models.CharField(blank=True, default='', max_length=191)),
                ('refund_amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('currency', models.CharField(default='BWP', max_length=3)),
                ('bank_name', models.CharField(blank=True, default='', max_length=120)),
                ('branch_code', models.CharField(blank=True, default='', max_length=20)),
                ('account_number_enc', models.TextField(blank=True, default='', help_text='Fernet-encrypted customer account number.')),
                ('account_last4', models.CharField(blank=True, default='', max_length=4)),
                ('account_fingerprint', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('ai_greenlight', models.BooleanField(default=False)),
                ('ai_evidence', models.JSONField(blank=True, default=dict)),
                ('fraud_flags', models.JSONField(blank=True, default=list, help_text='List of {severity, code, detail} fraud signals.')),
                ('fraud_score', models.IntegerField(db_index=True, default=0, help_text='0 clean · higher = more/severe signals.')),
                ('fraud_reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('received', 'Received from Graphite'), ('finance_queue', 'With Finance'), ('approved', 'Finance approved'), ('fnb_loaded', 'Loaded to FNB'), ('paid', 'Paid'), ('posted_back', 'Posted back to policy'), ('rejected', 'Rejected')], db_index=True, default='received', max_length=16)),
                ('finance_approved_at', models.DateTimeField(blank=True, null=True)),
                ('reject_reason', models.TextField(blank=True, default='')),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('graphite_posted', models.BooleanField(default=False)),
                ('graphite_posted_at', models.DateTimeField(blank=True, null=True)),
                ('finance_approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_refunds_approved', to=settings.AUTH_USER_MODEL)),
                ('fnb_batch', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_refunds', to='fnb.fnbbatchsubmission')),
                ('payment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_refunds', to='payments.payment')),
            ],
            options={
                'verbose_name': 'Customer Refund',
                'verbose_name_plural': 'Customer Refunds',
                'ordering': ['-created_at'],
            },
        ),
    ]
