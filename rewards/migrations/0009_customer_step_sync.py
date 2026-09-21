# Alpha Nexus v10 — verified step sync: device pairing + device token + per-day
# step ledger. Additive only (three new tables); no changes to existing models.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0008_customerfeedback'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerDevicePairingCode',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code_hash', models.CharField(max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('attempts', models.IntegerField(default=0)),
                ('consumed', models.BooleanField(default=False)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pairing_codes', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Device Pairing Code', 'ordering': ['-created_at'], 'abstract': False},
        ),
        migrations.CreateModel(
            name='CustomerDeviceToken',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('device_label', models.CharField(blank=True, default='', max_length=80)),
                ('revoked', models.BooleanField(default=False)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='device_tokens', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Device Token', 'ordering': ['-created_at'], 'abstract': False},
        ),
        migrations.CreateModel(
            name='CustomerStepDay',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('day', models.DateField()),
                ('steps_total', models.IntegerField(default=0, help_text='Highest synced total seen for the day')),
                ('points_awarded', models.IntegerField(default=0, help_text='Cumulative points paid for the day')),
                ('source', models.CharField(default='health_connect', max_length=24)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_days', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Step Day', 'ordering': ['-day'], 'abstract': False},
        ),
        migrations.AddConstraint(
            model_name='customerstepday',
            constraint=models.UniqueConstraint(fields=('member', 'day'), name='uniq_member_step_day'),
        ),
    ]
