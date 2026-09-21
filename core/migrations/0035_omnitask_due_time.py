# Generated 2026-07-13 — OmniTask.due_time (CFO: "all tasks due at 4pm").
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_task_mgmt_2026_07_13"),
    ]

    operations = [
        migrations.AddField(
            model_name="omnitask",
            name="due_time",
            field=models.TimeField(null=True, blank=True),
        ),
    ]
