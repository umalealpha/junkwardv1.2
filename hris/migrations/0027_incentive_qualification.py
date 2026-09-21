"""Earned-incentive gate fields on IncentiveLine (CFO directive 2026-07-13)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0026_leaverequest_half_day'),
    ]

    operations = [
        migrations.AddField(
            model_name='incentiveline',
            name='beyond_normal_duties',
            field=models.BooleanField(
                default=False,
                help_text='Work went beyond the employee’s normal day-to-day duties.'),
        ),
        migrations.AddField(
            model_name='incentiveline',
            name='on_time',
            field=models.BooleanField(default=False, help_text='Delivered on time.'),
        ),
        migrations.AddField(
            model_name='incentiveline',
            name='error_free',
            field=models.BooleanField(default=False, help_text='Delivered error-free.'),
        ),
        migrations.AddField(
            model_name='incentiveline',
            name='needed_manager_fix',
            field=models.BooleanField(
                default=False,
                help_text='Manager had to spend significant time fixing it (disqualifies).'),
        ),
        migrations.AddField(
            model_name='incentiveline',
            name='justification',
            field=models.TextField(
                blank=True, default='',
                help_text='Why this exceeded normal duties (required, substantive).'),
        ),
    ]
