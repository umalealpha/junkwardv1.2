import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="NexusDriver",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("full_name", models.CharField(max_length=120)),
                ("external_ref", models.CharField(blank=True, default="", max_length=64)),
                ("total_points", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"verbose_name": "Nexus Driver", "ordering": ["full_name"]},
        ),
        migrations.CreateModel(
            name="NexusTrip",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("started_at", models.DateTimeField()),
                ("label", models.CharField(blank=True, default="", max_length=120)),
                ("distance_km", models.DecimalField(decimal_places=2, default=0, max_digits=8)),
                ("duration_minutes", models.IntegerField(default=0)),
                ("idle_minutes", models.IntegerField(default=0)),
                ("harsh_brakes", models.IntegerField(default=0)),
                ("speeding_events", models.IntegerField(default=0)),
                ("score", models.IntegerField(default=0)),
                ("points", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("driver", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="trips", to="nexus.nexusdriver")),
            ],
            options={"verbose_name": "Nexus Trip", "ordering": ["-started_at"]},
        ),
        migrations.CreateModel(
            name="NexusRewardLedger",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("description", models.CharField(max_length=200)),
                ("points", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("driver", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="ledger", to="nexus.nexusdriver")),
                ("trip", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="nexus.nexustrip")),
            ],
            options={"verbose_name": "Nexus Reward Ledger Entry", "ordering": ["-created_at"]},
        ),
    ]
