import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Agent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160, unique=True)),
                ('is_active', models.BooleanField(default=True)),
                ('streams', models.JSONField(blank=True, default=list)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='CommissionCycle',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('label', models.CharField(max_length=80)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('status', models.CharField(choices=[('open', 'Open'), ('approved', 'Approved (pay-run signed off)'), ('paid', 'Paid / exported')], default='open', max_length=10)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-end_date']},
        ),
        migrations.CreateModel(
            name='AgentBankAccount',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('bank_name', models.CharField(max_length=120)),
                ('account_name', models.CharField(blank=True, default='', max_length=160)),
                ('account_number', models.CharField(max_length=40)),
                ('branch_code', models.CharField(blank=True, default='', max_length=20)),
                ('agent', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='bank', to='agent_portal.agent')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='PayoutBatch',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('agent_count', models.PositiveIntegerField(default=0)),
                ('total_bwp', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('held_count', models.PositiveIntegerField(default=0)),
                ('fmt', models.CharField(default='csv', max_length=20)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('cycle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payouts', to='agent_portal.commissioncycle')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='CommissionLine',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('stream', models.CharField(max_length=24)),
                ('basis', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('commission', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=18)),
                ('payable', models.BooleanField(default=False)),
                ('reason', models.CharField(blank=True, default='', max_length=200)),
                ('source', models.JSONField(blank=True, default=list)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='lines', to='agent_portal.agent')),
                ('cycle', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='agent_portal.commissioncycle')),
            ],
            options={'ordering': ['agent__name', 'stream']},
        ),
        migrations.AddIndex(
            model_name='commissionline',
            index=models.Index(fields=['cycle', 'stream'], name='ap_line_cycle_stream_idx'),
        ),
        migrations.AddIndex(
            model_name='commissionline',
            index=models.Index(fields=['cycle', 'payable'], name='ap_line_cycle_payable_idx'),
        ),
    ]
