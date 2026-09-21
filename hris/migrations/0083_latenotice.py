import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Rule 1b — the "I'm running late" notice (CFO 2026-09-09)."""

    dependencies = [
        ('hris', '0082_rehire_transfer_leaver_settlement'),
    ]

    operations = [
        migrations.CreateModel(
            name='LateNotice',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('notice_date', models.DateField(db_index=True, help_text='The morning this excuses. Always the day it was filed.')),
                ('kind', models.CharField(choices=[('late', 'Running late'), ('sick', 'Sick today'), ('client', 'With a client first'), ('other', 'Other')], default='late', max_length=8)),
                ('reason', models.TextField(blank=True, default='', help_text='In their own words. Shown to the line manager and listed on the monthly review.')),
                ('filed_local_time', models.TimeField(blank=True, help_text='Botswana wall-clock time the notice was filed.', null=True)),
                ('in_time', models.BooleanField(default=True, help_text='Filed before the 09:00 cutoff. A late notice is kept on the record but never forgives the morning.')),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='late_notices', to='hris.hrisprofile')),
            ],
            options={
                'verbose_name': 'Late Notice',
                'verbose_name_plural': 'Late Notices',
                'ordering': ['-notice_date'],
                'abstract': False,
                'unique_together': {('profile', 'notice_date')},
            },
        ),
    ]
