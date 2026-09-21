"""
0007_asset_status_migrated_duplicate — add MIGRATED_DUPLICATE choice.

CFO directive 2026-05-20 (FAR Defects Memo DR-002): quarantine bucket
for the 11 numeric-tag vehicles that duplicate canonical ADI-FAR-*
records. Soft-flag first, hard-delete only after CFO confirmation.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0006_rename_assets_aso_kind_status_idx_assets_asse_kind_0cf58b_idx_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='asset',
            name='status',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('active',             'Active'),
                    ('disposed',           'Disposed'),
                    ('written_off',        'Written off'),
                    ('transferred',        'Transferred'),
                    ('migrated_duplicate', 'Migrated duplicate (audit hold)'),
                ],
                default='active',
            ),
        ),
    ]
