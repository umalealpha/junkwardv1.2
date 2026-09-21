"""Initial migration for bank_feeds app."""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('ledger', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='BankFeedConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('protocol', models.CharField(choices=[
                    ('sftp_csv',  'SFTP — CSV file drop'),
                    ('sftp_ofx',  'SFTP — OFX file drop'),
                    ('sftp_bai2', 'SFTP — BAI2 file drop'),
                    ('api_fnb',   'Live API (FNB Botswana)'),
                    ('manual',    'Manual upload (no automation)'),
                ], max_length=16)),
                ('env_prefix', models.CharField(max_length=40)),
                ('remote_path', models.CharField(blank=True, max_length=240)),
                ('file_pattern', models.CharField(blank=True, default='*.csv', max_length=120)),
                ('schedule_cron', models.CharField(blank=True, default='0 6 * * *', max_length=60)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('last_run_status', models.CharField(blank=True, max_length=20)),
                ('status', models.CharField(choices=[
                    ('active', 'Active'), ('paused', 'Paused'), ('error', 'Error'),
                ], default='paused', max_length=10)),
                ('notes', models.TextField(blank=True)),
                ('bank_account', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='bank_feed_configs',
                    to='ledger.account',
                )),
            ],
            options={
                'ordering': ['name'],
                'unique_together': {('bank_account', 'env_prefix')},
            },
        ),
        migrations.CreateModel(
            name='BankFeedRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(default=timezone.now)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('outcome', models.CharField(choices=[
                    ('success', 'Success'),
                    ('empty', 'Empty (no new files)'),
                    ('error', 'Error'),
                ], default='success', max_length=10)),
                ('files_seen', models.PositiveIntegerField(default=0)),
                ('files_processed', models.PositiveIntegerField(default=0)),
                ('statements_created', models.PositiveIntegerField(default=0)),
                ('lines_created', models.PositiveIntegerField(default=0)),
                ('duplicates_skipped', models.PositiveIntegerField(default=0)),
                ('error_message', models.TextField(blank=True)),
                ('file_hashes', models.JSONField(blank=True, default=list)),
                ('config', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='runs',
                    to='bank_feeds.bankfeedconfig',
                )),
                ('triggered_by', models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='bank_feed_runs_triggered',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['-started_at'],
                'indexes': [
                    models.Index(fields=['outcome'], name='bank_feed_outcome_idx'),
                    models.Index(fields=['config', 'started_at'], name='bank_feed_cfg_start_idx'),
                ],
            },
        ),
    ]
