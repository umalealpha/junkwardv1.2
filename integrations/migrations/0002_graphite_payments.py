# Graphite V2 Finance payment-transaction feed (read-only mirror).
# Hand-written to match integrations/models.py — no local makemigrations env.

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('integrations', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='GraphitePaymentSyncRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('running', 'Running'), ('success', 'Success'), ('partial', 'Partial (some windows failed)'), ('failed', 'Failed'), ('skipped', 'Skipped (not configured)')], db_index=True, default='pending', max_length=10)),
                ('window_from', models.DateField()),
                ('window_to', models.DateField()),
                ('partner', models.CharField(blank=True, default='', max_length=50)),
                ('dry_run', models.BooleanField(default=False)),
                ('pages_fetched', models.PositiveIntegerField(default=0)),
                ('rows_seen', models.PositiveIntegerField(default=0)),
                ('rows_created', models.PositiveIntegerField(default=0)),
                ('rows_updated', models.PositiveIntegerField(default=0)),
                ('error_message', models.TextField(blank=True, default='')),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('triggered_by_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='graphite_payment_sync_runs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Graphite Payment Sync Run',
                'verbose_name_plural': 'Graphite Payment Sync Runs',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='GraphitePaymentTransaction',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('graphite_id', models.BigIntegerField(db_index=True, help_text='payment_transactions.id in Graphite V2', unique=True)),
                ('policy_number', models.CharField(blank=True, db_index=True, default='', max_length=64)),
                ('reference_number', models.CharField(blank=True, db_index=True, default='', max_length=191)),
                ('amount', models.DecimalField(decimal_places=2, default=0, max_digits=18)),
                ('payment_method', models.CharField(blank=True, default='', help_text='Payment partner: RealPay / DPO / Orange / manual / VCS / cash', max_length=50)),
                ('status', models.CharField(blank=True, default='', help_text='Raw status as shipped (mixed casing).', max_length=30)),
                ('status_norm', models.CharField(blank=True, default='', help_text='Lower-cased status for filtering.', max_length=30)),
                ('is_refund', models.BooleanField(default=False)),
                ('is_reverse', models.BooleanField(default=False)),
                ('payment_frequency', models.CharField(blank=True, default='', help_text='FIRST / RECURRING / ...', max_length=30)),
                ('note', models.TextField(blank=True, default='')),
                ('paid_at', models.DateTimeField(blank=True, null=True)),
                ('paid_on', models.DateTimeField(blank=True, null=True)),
                ('source_recorded_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('source_updated_at', models.DateTimeField(blank=True, null=True)),
                ('policy_id', models.BigIntegerField(blank=True, null=True)),
                ('product_id', models.BigIntegerField(blank=True, null=True)),
                ('plan_id', models.BigIntegerField(blank=True, null=True)),
                ('product_name', models.CharField(blank=True, default='', max_length=191)),
                ('plan_name', models.CharField(blank=True, default='', max_length=191)),
                ('customer_name', models.CharField(blank=True, default='', max_length=191)),
                ('agent_name', models.CharField(blank=True, default='', max_length=191)),
                ('dpo_trans_id', models.CharField(blank=True, default='', max_length=191)),
                ('dpo_company_ref', models.CharField(blank=True, default='', max_length=191)),
                ('dpo_token', models.CharField(blank=True, default='', max_length=191)),
                ('raw', models.JSONField(blank=True, default=dict, help_text='Full API row as received.')),
                ('synced_at', models.DateTimeField(blank=True, null=True)),
                ('sync_run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='transactions', to='integrations.graphitepaymentsyncrun')),
            ],
            options={
                'verbose_name': 'Graphite Payment Transaction',
                'verbose_name_plural': 'Graphite Payment Transactions',
                'ordering': ['-source_recorded_at', '-graphite_id'],
            },
        ),
        migrations.AddIndex(
            model_name='graphitepaymentsyncrun',
            index=models.Index(fields=['status', '-created_at'], name='intg_gpsr_status_idx'),
        ),
        migrations.AddIndex(
            model_name='graphitepaymentsyncrun',
            index=models.Index(fields=['window_from', 'window_to'], name='intg_gpsr_window_idx'),
        ),
        migrations.AddIndex(
            model_name='graphitepaymenttransaction',
            index=models.Index(fields=['payment_method', '-source_recorded_at'], name='intg_gpt_method_idx'),
        ),
        migrations.AddIndex(
            model_name='graphitepaymenttransaction',
            index=models.Index(fields=['status_norm', '-source_recorded_at'], name='intg_gpt_statusn_idx'),
        ),
        migrations.AddIndex(
            model_name='graphitepaymenttransaction',
            index=models.Index(fields=['is_refund'], name='intg_gpt_refund_idx'),
        ),
    ]
