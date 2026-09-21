# 5-day deferred auto-lock (CFO directive 2026-06-09).
# A TB import schedules an automatic lock at now + 5 days. Anyone can
# post JEs during the 5-day grace window; after the timestamp passes,
# the period is treated as effectively locked.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0023_je_perf_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='fiscalperiod',
            name='auto_lock_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='When set in the past, the period is treated as locked '
                          'even if status=OPEN. Set by TB import = now() + 5 days, '
                          'so users have a 5-day grace window to amend before lock-in.',
            ),
        ),
    ]
