"""ARIA persistent chat history (CFO directive 2026-05-24).

Adds two tables so ARIA chat threads survive page reloads and can be
resumed by passing ?conversation_id= back to AriaChatView. Append-only
log; the tool-use loop persists every iteration (user prompt, assistant
tool_calls, tool result, final assistant reply) as its own AriaMessage.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_tasks_presence'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='AriaConversation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('mood', models.CharField(default='sharp', max_length=20)),
                ('last_activity_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='aria_conversations',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name':        'ARIA conversation',
                'verbose_name_plural': 'ARIA conversations',
                'ordering':            ['-last_activity_at'],
            },
        ),
        migrations.AddIndex(
            model_name='ariaconversation',
            index=models.Index(
                fields=['user', '-last_activity_at'],
                name='core_aria_conv_user_idx',
            ),
        ),
        migrations.CreateModel(
            name='AriaMessage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('role', models.CharField(
                    choices=[
                        ('user',      'User'),
                        ('assistant', 'Assistant'),
                        ('tool',      'Tool'),
                        ('system',    'System'),
                    ],
                    max_length=16,
                )),
                ('content', models.TextField(blank=True, default='')),
                ('tool_calls', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('conversation', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages',
                    to='core.ariaconversation',
                )),
            ],
            options={
                'verbose_name':        'ARIA message',
                'verbose_name_plural': 'ARIA messages',
                'ordering':            ['created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='ariamessage',
            index=models.Index(
                fields=['conversation', 'created_at'],
                name='core_aria_msg_conv_idx',
            ),
        ),
    ]
