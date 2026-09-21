"""B9 Subrogation — the recovery-possible flag, the demand letter and the
48-hour escalation stamps.

All four columns are nullable with no default backfill, so this is safe on the
live register: existing rows simply have no flag, no letter and no escalation,
which is the truth about them.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('claims', '0009_claimsreconciliationrun'),
    ]

    operations = [
        migrations.AddField(
            model_name='subrogation',
            name='recovery_flagged_at',
            field=models.DateTimeField(
                blank=True, db_index=True, null=True,
                help_text='When Claims flagged this claim as recovery-possible. '
                          'Stamped once; the 48-hour escalation clock runs from here.'),
        ),
        migrations.AddField(
            model_name='subrogation',
            name='recovery_flag_source',
            field=models.CharField(
                blank=True, default='', max_length=20,
                help_text="'graphite' when it rode the nightly feed, 'manual' when keyed in Omni."),
        ),
        migrations.AddField(
            model_name='subrogation',
            name='demand_letter_sent_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='When the demand letter went to the third party. '
                          'This is the action that clears the alert.'),
        ),
        migrations.AddField(
            model_name='subrogation',
            name='demand_letter_sent_to',
            field=models.CharField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='subrogation',
            name='escalated_at',
            field=models.DateTimeField(
                blank=True, null=True,
                help_text='When the 48-hour escalation to the Finance Manager fired. '
                          'Set once, so it can never fire twice.'),
        ),
    ]
