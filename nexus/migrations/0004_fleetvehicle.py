# Company-fleet vehicle register (Cartrack integration — CFO 2026-07-10).
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("nexus", "0003_phoneping"),
    ]

    operations = [
        migrations.CreateModel(
            name="FleetVehicle",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("cartrack_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("registration", models.CharField(db_index=True, max_length=32)),
                ("make", models.CharField(blank=True, default="", max_length=60)),
                ("model", models.CharField(blank=True, default="", max_length=60)),
                ("description", models.CharField(blank=True, default="", max_length=120)),
                ("last_lat", models.FloatField(blank=True, null=True)),
                ("last_lng", models.FloatField(blank=True, null=True)),
                ("last_speed_kmh", models.FloatField(blank=True, null=True)),
                ("odometer_km", models.FloatField(blank=True, null=True)),
                ("ignition_on", models.BooleanField(blank=True, null=True)),
                ("moving", models.BooleanField(blank=True, null=True)),
                ("where", models.CharField(blank=True, default="", max_length=200)),
                ("driver_name", models.CharField(blank=True, default="", max_length=120)),
                ("last_seen", models.CharField(blank=True, default="", max_length=40)),
                ("is_demo", models.BooleanField(default=False)),
                ("raw", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Fleet Vehicle",
                "ordering": ["registration"],
            },
        ),
        migrations.AddConstraint(
            model_name="fleetvehicle",
            constraint=models.UniqueConstraint(fields=["registration"], name="fleet_vehicle_reg_uniq"),
        ),
    ]
