"""Add regulator / fy_end_month / framework / metadata JSON to Company.

Lifts the ADSA pipeline (entities.json) per-entity metadata into a
canonical home on core.Company. CFO directive 2026-05-18.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_merge_rbac_and_company_chains'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='regulator',
            field=models.CharField(
                max_length=40, blank=True, default='',
                help_text='e.g. NBFIRA (BW), FSCA (ZA), IRA (UG)',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='fy_end_month',
            field=models.PositiveSmallIntegerField(
                default=6,
                help_text='1-12 — month the fiscal year closes. Alpha Direct group default: 6 (June).',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='framework',
            field=models.CharField(
                max_length=40, blank=True, default='IFRS',
                help_text='Reporting framework: IFRS, IFRS for SMEs, etc.',
            ),
        ),
        migrations.AddField(
            model_name='company',
            name='metadata',
            field=models.JSONField(
                default=dict, blank=True,
                help_text='Optional dict — directors, FSP, CIPC, VAT state, etc.',
            ),
        ),
    ]
