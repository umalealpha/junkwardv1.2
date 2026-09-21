# Phone telematics ping — personal phone (GPSLogger) feeding Nexus live (CFO 2026-06-25).
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0002_nexustrip_external_trip_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='PhonePing',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('device', models.CharField(db_index=True, max_length=80)),
                ('lat', models.FloatField(blank=True, null=True)),
                ('lng', models.FloatField(blank=True, null=True)),
                ('speed_kmh', models.FloatField(default=0)),
                ('recorded_at', models.DateTimeField(blank=True, null=True)),
                ('received_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('raw', models.JSONField(blank=True, default=dict)),
            ],
            options={'verbose_name': 'Nexus Phone Ping', 'ordering': ['-received_at']},
        ),
        migrations.AddIndex(
            model_name='phoneping',
            index=models.Index(fields=['device', '-received_at'], name='nexus_ping_dev_rcv_idx'),
        ),
    ]
