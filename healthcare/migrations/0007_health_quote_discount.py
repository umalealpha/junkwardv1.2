"""Per-tier quotation discounts (Tlamelo 2026-06-25).

Adds tier_discounts (JSON map tier -> percent) plus gross_excl / discount_excl
so a quote can carry a standardised discount per plan tier. subtotal_excl / vat
/ total_incl now hold the NET (discounted) figures the client is quoted.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('healthcare', '0006_health_quote_review'),
    ]

    operations = [
        migrations.AddField(
            model_name='healthquote',
            name='tier_discounts',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='healthquote',
            name='gross_excl',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name='healthquote',
            name='discount_excl',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
