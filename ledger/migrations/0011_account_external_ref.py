# Adds Account.external_ref — idempotency key for Odoo migration.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ledger', '0010_fiscalperiod_dual_lock'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='external_ref',
            field=models.CharField(blank=True, db_index=True, default='', max_length=100),
        ),
    ]
