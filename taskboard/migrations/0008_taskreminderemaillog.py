"""Daily send-ledger for the task-reminder email.

Makes services.email_open_reminders() idempotent per (recipient, calendar day)
so a duplicate cron entry / manual re-run / retry can no longer resend the same
'N task(s) due or overdue' digest (Pramod, Sr IT, 2026-07-24: firing 5-6x/day).
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('taskboard', '0007_paymentrequest_two_stage_approval'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskReminderEmailLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False,
                                        primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sent_on', models.DateField(
                    help_text='Local date the digest was emailed.')),
                ('recipient', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='task_reminder_email_logs',
                    to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'abstract': False,
            },
        ),
        migrations.AddIndex(
            model_name='taskreminderemaillog',
            index=models.Index(fields=['recipient', 'sent_on'],
                               name='tb_treml_recip_senton_idx'),
        ),
        migrations.AddConstraint(
            model_name='taskreminderemaillog',
            constraint=models.UniqueConstraint(
                fields=['recipient', 'sent_on'],
                name='uniq_task_reminder_email_per_day'),
        ),
    ]
