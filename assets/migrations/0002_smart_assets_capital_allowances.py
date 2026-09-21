# Hand-authored migration — adds capital-allowance / VAT-treatment fields to
# AssetCategory and Asset.
#
# This migration was originally authored against a 0001_initial that did NOT
# contain these columns. 0001_initial was later regenerated from the models and
# now creates every one of these fields in its CreateModel ops. That left the
# AddField ops below colliding on a fresh database ("duplicate column name:
# default_tax_cost_cap"), which broke the test-DB build and any DR / new-env
# rebuild. Production already has 0002 recorded as applied, so the DB ops are
# redundant everywhere they would actually run.
#
# Fix (2026-06-05 audit): keep the operations as STATE-ONLY via
# SeparateDatabaseAndState so the migration graph/state is preserved exactly,
# but emit no SQL. No-op on prod (already applied) and on fresh builds (0001
# already created the columns).

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0001_initial'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(database_operations=[], state_operations=[
        # ── AssetCategory ─────────────────────────────────────────────────────
        migrations.AddField(
            model_name='assetcategory',
            name='is_passenger_vehicle',
            field=models.BooleanField(
                default=False,
                help_text='Triggers BURS passenger-vehicle treatment: cost cap '
                          'P 175,000 for capital allowances + input VAT denial.',
            ),
        ),
        migrations.AddField(
            model_name='assetcategory',
            name='default_capital_allowance_method',
            field=models.CharField(
                choices=[
                    ('straight_line', 'Straight-line'),
                    ('reducing_balance', 'Reducing balance'),
                    ('full_year_one', '100% in year of acquisition'),
                ],
                default='reducing_balance',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='assetcategory',
            name='default_capital_allowance_rate',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('25.00'), max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name='assetcategory',
            name='default_tax_cost_cap',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=18, null=True,
            ),
        ),
        migrations.AlterField(
            model_name='assetcategory',
            name='default_method',
            field=models.CharField(
                choices=[
                    ('straight_line', 'Straight-line'),
                    ('reducing_balance', 'Reducing balance'),
                    ('full_year_one', '100% in year of acquisition'),
                ],
                default='straight_line',
                max_length=20,
            ),
        ),
        # ── Asset ─────────────────────────────────────────────────────────────
        migrations.AddField(
            model_name='asset',
            name='vat_treatment',
            field=models.CharField(
                choices=[
                    ('standard', 'Standard — input VAT recoverable, cost is net of VAT'),
                    ('denied',   'Denied — VAT capitalised into cost (luxury / passenger vehicle)'),
                    ('zero_exempt', 'Zero-rated or exempt — no VAT'),
                ],
                default='standard',
                max_length=15,
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='purchase_vat_amount',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=18,
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='capital_allowance_method',
            field=models.CharField(
                choices=[
                    ('straight_line', 'Straight-line'),
                    ('reducing_balance', 'Reducing balance'),
                    ('full_year_one', '100% in year of acquisition'),
                ],
                default='reducing_balance',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='capital_allowance_rate',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('25.00'), max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name='asset',
            name='tax_cost_cap',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=18, null=True,
            ),
        ),
        ]),
    ]
