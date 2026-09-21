from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('supplier_recon', '0007_recon_owner'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplierreconline',
            name='overdue',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'),
                                      max_digits=18),
        ),
        migrations.AddField(
            model_name='supplierreconline',
            name='max_days_past_due',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
