from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="M365ActiveUser",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("object_id", models.CharField(max_length=64, unique=True)),
                ("email", models.EmailField(db_index=True, max_length=255)),
                ("display_name", models.CharField(max_length=255)),
                ("user_principal_name", models.CharField(db_index=True, max_length=255)),
                ("job_title", models.CharField(blank=True, max_length=255)),
                ("department", models.CharField(blank=True, max_length=255)),
                ("license_count", models.PositiveIntegerField(default=0)),
                ("last_interactive_signin_at", models.DateTimeField(blank=True, null=True)),
                ("refreshed_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ("display_name",)},
        ),
        migrations.CreateModel(
            name="M365LicenseSyncRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("total_seen", models.PositiveIntegerField(default=0)),
                ("active_count", models.PositiveIntegerField(default=0)),
                ("inserted", models.PositiveIntegerField(default=0)),
                ("updated", models.PositiveIntegerField(default=0)),
                ("removed", models.PositiveIntegerField(default=0)),
                ("success", models.BooleanField(default=False)),
                ("error", models.TextField(blank=True)),
                ("cutoff_at", models.DateTimeField(blank=True, null=True)),
                ("triggered_by", models.CharField(default="cron", max_length=64)),
            ],
            options={"ordering": ("-started_at",)},
        ),
    ]
