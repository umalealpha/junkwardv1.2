# Hand-authored migration — adds dual-authorisation columns + status values
# to AssetImportBatch.
#
# State-only since 2026-06-05 audit: 0001_initial was regenerated and now
# creates all of these columns in its CreateModel, so the AddField ops below
# collided ("duplicate column name: first_approved_by_id") on fresh-DB / test
# / DR builds. Production already has 0003 recorded as applied. Wrapped in
# SeparateDatabaseAndState to preserve migration state while emitting no SQL.
# See assets/migrations/0002 for the same fix and full rationale.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_smart_assets_capital_allowances'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        migrations.AlterField(
            model_name='assetimportbatch',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft (preview only)'),
                    ('partially_approved', 'Awaiting second approval'),
                    ('approved', 'Fully approved — ready to commit'),
                    ('committed', 'Committed'),
                    ('rejected', 'Rejected'),
                    ('failed', 'Failed'),
                ],
                default='draft', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='assetimportbatch',
            name='first_approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='asset_imports_first_approved',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='assetimportbatch',
            name='first_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assetimportbatch',
            name='second_approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='asset_imports_second_approved',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='assetimportbatch',
            name='second_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assetimportbatch',
            name='rejected_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='asset_imports_rejected',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='assetimportbatch',
            name='rejected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assetimportbatch',
            name='rejection_reason',
            field=models.TextField(blank=True, default=''),
        ),
        ]),
    ]
