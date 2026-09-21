# Generated for Health Points (Alpha Rewards / Project Nexus) — CFO directive.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='HealthConsent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('data_types', models.JSONField(blank=True, default=list, help_text="List of consented categories, e.g. ['steps','sleep'].")),
                ('granted_at', models.DateTimeField()),
                ('is_active', models.BooleanField(default=True)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='health_consents', to='rewards.rewardmember')),
            ],
            options={
                'verbose_name': 'Health Consent',
                'ordering': ['-granted_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='HealthMetric',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('date', models.DateField()),
                ('steps', models.IntegerField(default=0)),
                ('sleep_minutes', models.IntegerField(blank=True, null=True)),
                ('points_awarded', models.IntegerField(default=0)),
                ('source', models.CharField(default='Health App', max_length=60)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='health_metrics', to='rewards.rewardmember')),
            ],
            options={
                'verbose_name': 'Health Metric',
                'ordering': ['-date'],
                'abstract': False,
                'unique_together': {('member', 'date')},
            },
        ),
    ]
