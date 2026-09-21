"""Internal tasking + presence (CFO directive 2026-05-24)."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_user_company_access'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OmniTask',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=200)),
                ('body', models.TextField(blank=True, default='')),
                ('due_at', models.DateField(blank=True, null=True)),
                ('priority', models.CharField(
                    choices=[('low', 'Low'), ('normal', 'Normal'),
                             ('high', 'High'), ('urgent', 'Urgent')],
                    default='normal', max_length=10)),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'),
                             ('in_progress', 'In progress'),
                             ('done', 'Done'),
                             ('partial', 'Partially complete'),
                             ('blocked', 'Blocked'),
                             ('cancelled', 'Cancelled')],
                    db_index=True, default='pending', max_length=15)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('seen_at', models.DateTimeField(blank=True, null=True)),
                ('assignee', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='tasks_received',
                    to=settings.AUTH_USER_MODEL)),
                ('assigner', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='tasks_assigned',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Omni task',
                'verbose_name_plural': 'Omni tasks',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='omnitask',
            index=models.Index(fields=['assignee', 'status'],
                               name='core_omnit_assigne_a_idx'),
        ),
        migrations.AddIndex(
            model_name='omnitask',
            index=models.Index(fields=['assigner', 'status'],
                               name='core_omnit_assigne_b_idx'),
        ),
        migrations.CreateModel(
            name='OmniTaskComment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('body', models.TextField(blank=True, default='')),
                ('new_status', models.CharField(
                    blank=True, default='',
                    choices=[('pending', 'Pending'),
                             ('in_progress', 'In progress'),
                             ('done', 'Done'),
                             ('partial', 'Partially complete'),
                             ('blocked', 'Blocked'),
                             ('cancelled', 'Cancelled')],
                    max_length=15)),
                ('author', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    to=settings.AUTH_USER_MODEL)),
                ('task', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='comments', to='core.omnitask')),
            ],
            options={
                'verbose_name': 'Omni task comment',
                'verbose_name_plural': 'Omni task comments',
                'ordering': ['created_at'],
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='OnlinePresence',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_seen', models.DateTimeField(db_index=True)),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='presence',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Online presence',
                'verbose_name_plural': 'Online presence',
                'abstract': False,
            },
        ),
    ]
