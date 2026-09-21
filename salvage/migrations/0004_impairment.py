"""
salvage.0004_impairment

Adds `impaired_at` + `impaired_total_bwp` to SalvageItem so write-downs
to NRV are auditable.

CFO directive 2026-05-24 (Track-B audit): IAS 2 / IFRS 17 expect a
periodic comparison of carrying value vs net realisable value, with
the loss booked when NRV drops.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('salvage', '0003_salvage_cost_basis_intake_je'),
    ]

    operations = [
        migrations.AddField(
            model_name='salvageitem',
            name='impaired_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salvageitem',
            name='impaired_total_bwp',
            field=models.DecimalField(
                decimal_places=2, default=0, max_digits=18,
                help_text='Cumulative impairment recognised on this item.',
            ),
        ),
    ]
