from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("healthcare", "0014_healthquote_emailed"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProviderDailySnapshot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("date", models.DateField(db_index=True, unique=True)),
                ("total", models.IntegerField(default=0)),
                ("afa_registered", models.IntegerField(default=0)),
                ("afa_pending", models.IntegerField(default=0)),
                ("adh_ready", models.IntegerField(default=0)),
                ("qc_confirmed", models.IntegerField(default=0)),
                ("mismatches", models.IntegerField(default=0)),
                ("pending_applications", models.IntegerField(default=0)),
            ],
            options={
                "verbose_name": "Provider daily snapshot",
                "ordering": ["-date"],
            },
        ),
    ]
