# FA-001 intercompany asset transfer (CFO directive 2026-05-29).
# Adds: AssetDisposal.recipient_company (nullable), AssetDisposal.transfer_value
# (nullable; null = use NBV at posting); Asset.transferred_from_asset (self FK,
# audit link from receiver row back to sender row).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0008_asset_parent_componentisation'),
        ('core',   '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='assetdisposal',
            name='recipient_company',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='asset_disposals_received',
                to='core.company',
                help_text='Receiving company on a TRANSFER disposal. Required '
                          'when disposal_type=transfer and the transfer is '
                          'intercompany (sender ≠ recipient).'),
        ),
        migrations.AddField(
            model_name='assetdisposal',
            name='transfer_value',
            field=models.DecimalField(
                blank=True, null=True,
                max_digits=18, decimal_places=2,
                help_text='Override transfer value for intercompany transfers. '
                          'NULL = transfer at NBV (no gain/loss). When set, the '
                          'difference vs NBV posts as gain or loss.'),
        ),
        migrations.AddField(
            model_name='asset',
            name='transferred_from_asset',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='transferred_to',
                to='assets.asset',
                help_text='Audit link: the sender-side Asset row this asset was '
                          'transferred FROM. Only set on receiver rows created '
                          'by an intercompany TRANSFER.'),
        ),
    ]
