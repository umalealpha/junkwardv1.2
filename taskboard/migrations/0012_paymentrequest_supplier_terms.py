"""Supplier payment terms control (CFO directive 2026-07-28).

Adds the three header fields the PAY-SUP-01 gate needs on a payment request:
the date the money actually leaves, the reason if it leaves before the invoice
is due, and a declaration that cash was moved to fund the request before it was
authorised. The per-line invoice number / invoice date / terms / due date live
in the existing `line_items` JSON field, so no column is needed for those.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0011_sync_model_state_2026_07_26'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentrequest',
            name='payment_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='early_payment_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='paymentrequest',
            name='funds_already_moved',
            field=models.BooleanField(default=False),
        ),
    ]
