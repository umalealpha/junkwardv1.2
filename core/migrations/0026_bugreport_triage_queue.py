# Generated 2026-06-10 — AI-triage queue (CFO directive: "Request AI fix" button
# + email trigger). Adds triage_requested / _at / _by + triage_pr_url to
# BugReport. Hand-authored to match the model; deploy entrypoint applies it.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_bugreport_triage'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='bugreport',
            name='triage_requested',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='bugreport',
            name='triage_requested_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='bugreport',
            name='triage_requested_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bug_reports_triage_requested', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='bugreport',
            name='triage_pr_url',
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
