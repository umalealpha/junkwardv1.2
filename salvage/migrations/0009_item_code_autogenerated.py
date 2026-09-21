"""item_code stops being typed by hand.

Bharath, 17-Sep-2026: "Code must be auto generated for each salvage/part.
Manually doing this might end up in getting duplicates."

Only the field's `blank`/`default` change — the UNIQUE index stays exactly as
it was. The number itself is drawn in SalvageItem.save() under a row lock, so
nothing here backfills or renumbers the 51 existing ML-#### items.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('salvage', '0008_parts_register_manual_entry'),
    ]

    operations = [
        migrations.AlterField(
            model_name='salvageitem',
            name='item_code',
            field=models.CharField(blank=True, default='', max_length=40, unique=True),
        ),
    ]
