"""PO line display order (Kao 2026-07-08): reorder lines, e.g. place a new
line above the Excess line."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0010_claims_progress'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseorderline',
            name='sequence',
            field=models.PositiveIntegerField(default=0, db_index=True),
        ),
        migrations.AlterModelOptions(
            name='purchaseorderline',
            options={'ordering': ['sequence', 'created_at'],
                     'verbose_name': 'Purchase Order Line',
                     'verbose_name_plural': 'Purchase Order Lines'},
        ),
    ]
