# Click & Drive trips for the customer app — 2026-06-26.
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0004_customer_auth'),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomerDriveTrip',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField()),
                ('distance_km', models.FloatField(default=0)),
                ('duration_min', models.FloatField(default=0)),
                ('idle_minutes', models.FloatField(default=0)),
                ('harsh_events', models.IntegerField(default=0)),
                ('max_speed', models.FloatField(default=0, help_text='km/h')),
                ('score', models.IntegerField(default=0, help_text='0-100 trip safety score')),
                ('points_awarded', models.IntegerField(default=0)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='drive_trips', to='rewards.rewardmember')),
            ],
            options={'verbose_name': 'Customer Drive Trip', 'ordering': ['-started_at'], 'abstract': False},
        ),
    ]
