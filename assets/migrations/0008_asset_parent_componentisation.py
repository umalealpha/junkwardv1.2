"""
0008_asset_parent_componentisation — IFRS componentisation backbone.

Adds Asset.parent_asset (self-FK, nullable) so a composite asset can
be modelled as a parent shell plus child components that depreciate
on their own schedules.

The depreciation run (assets/services.py::run_monthly_depreciation) is
updated in code to:
  • iterate only parent_asset__isnull=True rows
  • for each one, post depreciation summed across its child components
    via assets/component_models.py::walk_total_monthly_depreciation.

For assets with no children the behaviour is identical to today.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0007_asset_status_migrated_duplicate'),
    ]

    operations = [
        migrations.AddField(
            model_name='asset',
            name='parent_asset',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='child_components',
                to='assets.asset',
                help_text='Parent composite asset. Leave blank for '
                          'standalone assets and for parents themselves. '
                          'Children depreciate independently; the parent '
                          'aggregates via the components property.',
            ),
        ),
    ]
