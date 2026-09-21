"""StuckWorkEscalation — the per-(queue, day) claim row that keeps
core.stuck_work.sweep() from re-alarming the CFO on a re-run.

CFO 2026-08-20: "can we create auto reminders and tasks if someone is sitting on
something".
"""
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0059_bugreport_status_check'),
    ]

    operations = [
        migrations.CreateModel(
            name='StuckWorkEscalation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('watcher_key', models.CharField(db_index=True, max_length=40)),
                ('sent_on', models.DateField(db_index=True)),
            ],
            options={
                'verbose_name': 'Stuck-work escalation',
                'verbose_name_plural': 'Stuck-work escalations',
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddConstraint(
            model_name='stuckworkescalation',
            constraint=models.UniqueConstraint(fields=('watcher_key', 'sent_on'),
                                               name='uniq_stuck_escalation_per_day'),
        ),
    ]
