# WebFleet tripid on NexusTrip for idempotent live ingest (Charmaine 2026-06-25).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nexus', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='nexustrip',
            name='external_trip_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64),
        ),
    ]
