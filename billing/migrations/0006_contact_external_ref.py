# Adds Contact.external_ref — idempotency key for Odoo migration.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0005_invoice_approval_tier_invoice_approved_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='external_ref',
            field=models.CharField(blank=True, db_index=True, default='', max_length=100),
        ),
    ]
