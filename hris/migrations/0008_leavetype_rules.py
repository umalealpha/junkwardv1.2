"""Add CoS-2023 §7 rule fields to LeaveType.

PAY-009. CFO directive 2026-06-03 after Unami Butale uploaded the
Conditions of Service. Five new fields encode rules that were
previously only carried in the name string.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hris', '0007_hris_alert'),
    ]

    operations = [
        migrations.AddField(
            model_name='leavetype',
            name='carry_over_cap_days',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Max accumulated balance — §7.2 default = 2× annual.',
            ),
        ),
        migrations.AddField(
            model_name='leavetype',
            name='max_carry_over_years',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Successive years a balance may roll — §7.2.3 = 3.',
            ),
        ),
        migrations.AddField(
            model_name='leavetype',
            name='probation_months',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Months of tenure required before taking this leave.',
            ),
        ),
        migrations.AddField(
            model_name='leavetype',
            name='requires_medical_cert',
            field=models.BooleanField(
                default=False,
                help_text='True for SICK / MATERNITY (CoS §7.6, §7.8).',
            ),
        ),
        migrations.AddField(
            model_name='leavetype',
            name='paid_pct',
            field=models.PositiveSmallIntegerField(
                default=100,
                help_text='Pay percent during this leave — 50 for MATERNITY, 50 for HOSP_HALF.',
            ),
        ),
    ]
