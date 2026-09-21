# Customer self-service auth for the customer app (email OTP) — 2026-06-26.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0003_healthmetric_vitals'),
    ]

    operations = [
        migrations.AddField(
            model_name='rewardmember',
            name='email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
        migrations.CreateModel(
            name='CustomerLoginCode',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('code_hash', models.CharField(max_length=64)),
                ('expires_at', models.DateTimeField()),
                ('attempts', models.IntegerField(default=0)),
                ('consumed', models.BooleanField(default=False)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='login_codes', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Login Code', 'ordering': ['-created_at'], 'abstract': False},
        ),
        migrations.CreateModel(
            name='CustomerSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('revoked', models.BooleanField(default=False)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Session', 'ordering': ['-created_at'], 'abstract': False},
        ),
    ]
