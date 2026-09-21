# Per-day standings-email cap for Alpha Nexus testers — 2026-06-26.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0005_customerdrivetrip'),
    ]

    operations = [
        migrations.CreateModel(
            name='NexusEmailQuota',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('day', models.DateField()),
                ('count', models.IntegerField(default=0)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='email_quota', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Nexus Email Quota', 'ordering': ['-day'], 'abstract': False,
                     'unique_together': {('member', 'day')}},
        ),
    ]
