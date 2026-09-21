# Generated 2026-06-10 — bug-report triage + feedback loop (CFO directive).
# Adds resolution_note / resolved_at / triaged_by to BugReport. Hand-authored
# to match the model (no local Django env); deploy entrypoint applies it.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0024_bugreport'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='bugreport',
            name='resolution_note',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='bugreport',
            name='resolved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='bugreport',
            name='triaged_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bug_reports_triaged', to=settings.AUTH_USER_MODEL),
        ),
    ]
