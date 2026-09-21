# Generated 2026-07-13 — task-management feature (CFO 2026-07-13).
# Hand-trimmed to ONLY the task-model changes; unrelated model drift
# (index renames, emaillogincode.id, userprofile.title choices) deliberately
# excluded so this migration cannot alter approval-critical fields.
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_privacy_notice_ack"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskFeedback",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("body", models.TextField(
                    help_text="The feedback message shown to the employee.")),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "Task feedback",
                "verbose_name_plural": "Task feedback",
                "ordering": ["-created_at"],
                "abstract": False,
            },
        ),
        migrations.AddField(
            model_name="omnitask",
            name="source",
            field=models.CharField(
                blank=True, default="", max_length=30,
                help_text="e.g. 'planning_meeting' when auto-created from the weekly plan."),
        ),
        migrations.AddField(
            model_name="omnitask",
            name="week_of",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="omnitaskcomment",
            name="evidence",
            field=models.FileField(
                blank=True, null=True, upload_to="task_evidence/",
                help_text="Proof of completion or of the blocker (screenshot / doc). Max 10 MB."),
        ),
        migrations.AddIndex(
            model_name="omnitask",
            index=models.Index(fields=["week_of"], name="core_omnita_week_of_f156ab_idx"),
        ),
        migrations.AddField(
            model_name="taskfeedback",
            name="from_user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="task_feedback_given", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="taskfeedback",
            name="task",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="feedback", to="core.omnitask"),
        ),
        migrations.AddField(
            model_name="taskfeedback",
            name="to_user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="task_feedback_received", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddIndex(
            model_name="taskfeedback",
            index=models.Index(fields=["to_user", "acknowledged_at"],
                               name="core_taskfe_to_user_5d193b_idx"),
        ),
    ]
