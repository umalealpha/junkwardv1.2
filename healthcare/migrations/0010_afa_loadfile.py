import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('billing', '0001_initial'),
        ('healthcare', '0009_healthquote_uniq_healthquote_invoice_no'),
    ]

    operations = [
        migrations.CreateModel(
            name='AfaGroupNameMap',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('employer_group_id', models.CharField(db_index=True, max_length=50, unique=True)),
                ('graphite_name', models.CharField(blank=True, default='', max_length=255)),
                ('imed_group_name', models.CharField(max_length=255)),
                ('region_name', models.CharField(blank=True, default='', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('confirmed_by', models.CharField(blank=True, default='', max_length=150)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('billing_contact', models.ForeignKey(blank=True, help_text='The Omni contact invoiced for this employer group.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='afa_group_maps', to='billing.contact')),
            ],
            options={
                'verbose_name': 'AFA group name mapping',
                'ordering': ['imed_group_name'],
            },
        ),
        migrations.CreateModel(
            name='AfaMemberSnapshot',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('policy_number', models.CharField(db_index=True, max_length=60)),
                ('dependant_no', models.PositiveIntegerField(default=0)),
                ('employer_group_id', models.CharField(blank=True, db_index=True, default='', max_length=50)),
                ('graphite_policy_id', models.BigIntegerField(blank=True, null=True)),
                ('graphite_beneficiary_id', models.BigIntegerField(blank=True, null=True)),
                ('row_hash', models.CharField(blank=True, default='', max_length=64)),
                ('state', models.CharField(choices=[('active', 'On cover'), ('resigned', 'Resigned / cancelled'), ('suspended', 'Suspended')], default='active', max_length=12)),
                ('first_sent_run', models.CharField(blank=True, default='', max_length=40)),
                ('last_sent_run', models.CharField(blank=True, default='', max_length=40)),
                ('last_sent_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['policy_number', 'dependant_no'],
            },
        ),
        migrations.AddConstraint(
            model_name='afamembersnapshot',
            constraint=models.UniqueConstraint(
                fields=('policy_number', 'dependant_no'),
                name='uniq_afa_snapshot_policy_dependant',
            ),
        ),
        migrations.CreateModel(
            name='AfaLoadFileRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('run_date', models.DateField(db_index=True, unique=True)),
                ('status', models.CharField(choices=[('built', 'Built — awaiting release'), ('released', 'Released by a person'), ('sent', 'Delivered to AFA'), ('failed', 'Transfer failed'), ('aborted', 'Aborted by the safety fence')], default='built', max_length=12)),
                ('row_count', models.PositiveIntegerField(default=0)),
                ('new_count', models.PositiveIntegerField(default=0)),
                ('changed_count', models.PositiveIntegerField(default=0)),
                ('departure_count', models.PositiveIntegerField(default=0)),
                ('held_count', models.PositiveIntegerField(default=0)),
                ('held_reasons', models.JSONField(blank=True, default=dict)),
                ('file_name', models.CharField(blank=True, default='', max_length=200)),
                ('file_sha256', models.CharField(blank=True, default='', max_length=64)),
                ('file_body', models.TextField(blank=True, default='')),
                ('sent_keys', models.JSONField(blank=True, default=dict)),
                ('departure_keys', models.JSONField(blank=True, default=list)),
                ('source_row_count', models.PositiveIntegerField(default=0)),
                ('replica_lag_seconds', models.IntegerField(blank=True, null=True)),
                ('abort_reason', models.TextField(blank=True, default='')),
                ('built_at', models.DateTimeField(auto_now_add=True)),
                ('released_at', models.DateTimeField(blank=True, null=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('send_error', models.TextField(blank=True, default='')),
                ('ack_status', models.CharField(blank=True, default='', max_length=40)),
                ('ack_detail', models.TextField(blank=True, default='')),
                ('released_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='afa_runs_released', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-run_date'],
            },
        ),
    ]
