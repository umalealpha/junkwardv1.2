import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='CiRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('run_id', models.BigIntegerField(db_index=True, unique=True)),
                ('run_number', models.IntegerField(default=0)),
                ('workflow', models.CharField(blank=True, default='', max_length=120)),
                ('branch', models.CharField(db_index=True, max_length=200)),
                ('head_sha', models.CharField(blank=True, default='', max_length=40)),
                ('title', models.CharField(blank=True, default='', max_length=300)),
                ('actor', models.CharField(blank=True, default='', max_length=120)),
                ('event', models.CharField(blank=True, default='', max_length=40)),
                ('url', models.URLField(blank=True, default='')),
                ('status', models.CharField(
                    choices=[('queued', 'Queued'), ('running', 'Running'),
                             ('success', 'Passed'), ('failure', 'Failed'),
                             ('cancelled', 'Cancelled')],
                    db_index=True, default='queued', max_length=12)),
                ('started_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('machine', models.CharField(blank=True, default='', max_length=16)),
            ],
            options={'ordering': ['-started_at', '-run_id']},
        ),
        migrations.CreateModel(
            name='CiJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('job_id', models.BigIntegerField(db_index=True, unique=True)),
                ('name', models.CharField(db_index=True, max_length=120)),
                ('status', models.CharField(
                    choices=[('queued', 'Queued'), ('running', 'Running'),
                             ('success', 'Passed'), ('failure', 'Failed'),
                             ('cancelled', 'Cancelled')],
                    db_index=True, default='queued', max_length=12)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('url', models.URLField(blank=True, default='')),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                          related_name='jobs', to='ci_monitor.cirun')),
            ],
            options={'ordering': ['name']},
        ),
    ]
