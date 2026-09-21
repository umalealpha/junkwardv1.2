# Generated for Alpha Thrive vitals (HR + HRV-stress + respiration) — 2026-06-26.
# Adds nullable derived-number fields to HealthMetric. Old step-only rows stay
# valid. Wellness only, never diagnosis; no blood pressure; raw frames never
# stored — only these scalars.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('rewards', '0002_healthconsent_healthmetric'),
    ]

    operations = [
        migrations.AddField(
            model_name='healthmetric',
            name='resting_hr',
            field=models.IntegerField(blank=True, help_text='Beats per minute from the scan.', null=True),
        ),
        migrations.AddField(
            model_name='healthmetric',
            name='hrv_sdnn',
            field=models.FloatField(blank=True, help_text='HRV SDNN (ms).', null=True),
        ),
        migrations.AddField(
            model_name='healthmetric',
            name='hrv_rmssd',
            field=models.FloatField(blank=True, help_text='HRV RMSSD (ms).', null=True),
        ),
        migrations.AddField(
            model_name='healthmetric',
            name='hrv_pnn50',
            field=models.FloatField(blank=True, help_text='HRV pNN50 (%).', null=True),
        ),
        migrations.AddField(
            model_name='healthmetric',
            name='respiration_rate',
            field=models.FloatField(blank=True, help_text='Breaths per minute.', null=True),
        ),
        migrations.AddField(
            model_name='healthmetric',
            name='stress_band',
            field=models.CharField(blank=True, default='', help_text='calm / moderate / stressed (from RMSSD).', max_length=12),
        ),
        migrations.AddField(
            model_name='healthmetric',
            name='scan_confidence',
            field=models.FloatField(blank=True, help_text='0-1 signal-quality of the PPG scan.', null=True),
        ),
    ]
