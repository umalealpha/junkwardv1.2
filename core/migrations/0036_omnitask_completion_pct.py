"""Add OmniTask.completion_pct — per-task percent complete for the dashboard
status control (CFO 2026-07-13). Hand-written (not makemigrations) so no
unrelated model drift is bundled in."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_omnitask_due_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="omnitask",
            name="completion_pct",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                help_text="Percent complete (0-100). Null = not yet reported.",
            ),
        ),
    ]
