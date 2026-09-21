"""Payment authorisation requests (CFO 2026-07-15 "task issues").

Structured payment requests raised by Finance and routed to the CFO's task
inbox — replaces the ad-hoc authorisation emails. Hand-written (not
makemigrations) to keep it scoped to this one model and avoid bundling
unrelated field drift.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0037_omnitask_performance_points'),
        ('taskboard', '0003_whatsapp_waba_slot'),
    ]

    operations = [
        migrations.CreateModel(
            name='PaymentRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('ref', models.CharField(max_length=48, unique=True)),
                ('entity', models.CharField(default='Alpha Direct Insurance Company', max_length=120)),
                ('currency', models.CharField(default='BWP', help_text='BWP / ZAR / USD / INR', max_length=3)),
                ('subject', models.CharField(max_length=200)),
                ('payee', models.CharField(blank=True, default='', max_length=200)),
                ('line_items', models.JSONField(default=list)),
                ('total', models.DecimalField(decimal_places=2, default=0, max_digits=16)),
                ('account_name', models.CharField(blank=True, default='', max_length=120)),
                ('account_number', models.CharField(blank=True, default='', max_length=64)),
                ('bank_name', models.CharField(blank=True, default='', max_length=120)),
                ('opening_balance', models.DecimalField(blank=True, decimal_places=2, max_digits=16, null=True)),
                ('due_date', models.DateField(blank=True, null=True)),
                ('inputter', models.CharField(blank=True, default='', max_length=160)),
                ('verifier', models.CharField(blank=True, default='', max_length=160)),
                ('summary', models.TextField(blank=True, default='')),
                ('formatted_html', models.TextField(blank=True, default='')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payment_requests_created', to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='payment_request', to='core.omnitask')),
            ],
            options={
                'verbose_name': 'Payment request',
                'verbose_name_plural': 'Payment requests',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
    ]
