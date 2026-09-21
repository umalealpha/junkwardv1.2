# Generated 2026-06-10 — "Report a System Bug" channel (CFO directive).
# BugReport model lives at core/models.py (after ChatMessage). Hand-authored
# to match the model exactly (no local Django env to run makemigrations); the
# deploy entrypoint applies it. Depends on 0023_chatmessage + AUTH_USER_MODEL.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_chatmessage'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BugReport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reporter_email', models.EmailField(blank=True, max_length=254)),
                ('description', models.TextField()),
                ('word_count', models.PositiveIntegerField(default=0)),
                ('screenshot_count', models.PositiveIntegerField(default=0)),
                ('page_url', models.CharField(blank=True, max_length=500)),
                ('status', models.CharField(choices=[('new', 'New'), ('triaged', 'Triaged'), ('in_progress', 'In Progress'), ('resolved', 'Resolved'), ('wont_fix', "Won't Fix")], db_index=True, default='new', max_length=20)),
                ('emailed_ok', models.BooleanField(default=False)),
                ('reporter', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bug_reports', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Bug Report',
                'verbose_name_plural': 'Bug Reports',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
    ]
