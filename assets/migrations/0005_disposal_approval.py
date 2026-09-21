# Hand-authored — adds approval workflow fields to AssetDisposal.
#
# State-only since 2026-06-05 audit: 0001_initial was regenerated and now
# creates all of these columns, so these AddField ops collided on fresh-DB /
# test / DR builds. Production already has 0005 recorded as applied. Wrapped in
# SeparateDatabaseAndState to preserve migration state while emitting no SQL.
# See assets/migrations/0002 for the same fix and full rationale.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0004_asset_signoff'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        migrations.AddField(
            model_name='assetdisposal',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending_approval', 'Pending approval'),
                    ('approved', 'Approved (posted to GL)'),
                    ('rejected', 'Rejected'),
                ],
                default='pending_approval', max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='requested_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='asset_disposals_requested',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='requested_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='approved_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='asset_disposals_approved',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='rejected_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='asset_disposals_rejected',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='rejected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='rejection_reason',
            field=models.TextField(blank=True, default=''),
        ),
        ]),
    ]
