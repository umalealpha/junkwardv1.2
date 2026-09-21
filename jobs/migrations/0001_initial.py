import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='ScheduledJob',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=80, unique=True)),
                ('what_it_does', models.TextField(blank=True, default='')),
                ('who_it_affects', models.CharField(blank=True, default='', max_length=200)),
                ('if_switched_off', models.TextField(blank=True, default='')),
                ('category', models.CharField(choices=[('sends', 'Sends a message'), ('moves-data', 'Moves or writes data'), ('safety', 'A safety net'), ('report', 'Prepares a report'), ('housekeeping', 'Housekeeping')], default='sends', max_length=16)),
                ('schedule', models.CharField(blank=True, default='', max_length=60)),
                ('is_enabled', models.BooleanField(default=True)),
                ('off_until', models.DateField(blank=True, null=True)),
                ('off_reason', models.CharField(blank=True, default='', max_length=200)),
                ('is_protected', models.BooleanField(default=False)),
                ('host_present', models.BooleanField(default=True)),
                ('host_disabled', models.BooleanField(default=False)),
                ('last_seen_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'verbose_name': 'Scheduled job', 'ordering': ['name'], 'abstract': False},
        ),
        migrations.CreateModel(
            name='JobRun',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('started_at', models.DateTimeField(db_index=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('ok', 'Ran'), ('failed', 'Failed'), ('skipped', 'Skipped — switched off')], max_length=8)),
                ('exit_code', models.IntegerField(blank=True, null=True)),
                ('output_tail', models.TextField(blank=True, default='')),
                ('switch_was_on', models.BooleanField(default=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='runs', to='jobs.scheduledjob')),
            ],
            options={'ordering': ['-started_at'], 'abstract': False},
        ),
        migrations.CreateModel(
            name='ScheduledJobChange',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('changed_by', models.CharField(blank=True, default='', max_length=120)),
                ('turned_on', models.BooleanField()),
                ('reason', models.CharField(blank=True, default='', max_length=200)),
                ('at_ip', models.CharField(blank=True, default='', max_length=64)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='changes', to='jobs.scheduledjob')),
            ],
            options={'ordering': ['-created_at'], 'abstract': False},
        ),
    ]
