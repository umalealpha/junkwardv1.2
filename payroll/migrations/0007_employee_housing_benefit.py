# PAY-008 housing benefit (BURS § 32 — CFO directive 2026-05-29).
# Additive: 5 Employee fields, all nullable / default-False. No data backfill
# needed — staff stay on cash HOUSING_ALLOWANCE until the CFO flips an
# individual employee to salary_sacrifice_housing=True + sets the values.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll', '0006_contracts_loans_formula'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='housing_benefit_type',
            field=models.CharField(
                choices=[
                    ('none',      'No housing benefit (cash allowance or nothing)'),
                    ('rated',     'Company-leased — rated area (10% rateable value)'),
                    ('non_rated', 'Company-leased — non-rated area (8% × P250 × m²)'),
                ],
                default='none', max_length=10),
        ),
        migrations.AddField(
            model_name='employee',
            name='housing_rateable_value',
            field=models.DecimalField(
                blank=True, null=True,
                max_digits=14, decimal_places=2,
                help_text='Annual rateable value of the property (BWP) — used when '
                          'housing_benefit_type=rated. From council valuation roll.'),
        ),
        migrations.AddField(
            model_name='employee',
            name='housing_floor_area_m2',
            field=models.DecimalField(
                blank=True, null=True,
                max_digits=8, decimal_places=2,
                help_text='Floor area (m²) — used when housing_benefit_type=non_rated. '
                          'Tribal land / unrated area only.'),
        ),
        migrations.AddField(
            model_name='employee',
            name='housing_furniture_cost',
            field=models.DecimalField(
                blank=True, null=True,
                max_digits=14, decimal_places=2,
                help_text='Total cost of company-provided furniture (BWP). Furniture '
                          'benefit fires only on the excess above P15,000.'),
        ),
        migrations.AddField(
            model_name='employee',
            name='salary_sacrifice_housing',
            field=models.BooleanField(
                default=False,
                help_text='When True, the payslip generator forces HOUSING_ALLOWANCE to '
                          'zero and inserts a HOUSING_BENEFIT (and FURNITURE_BENEFIT) '
                          'line computed from the fields above. Requires a signed '
                          'employment-contract amendment.'),
        ),
    ]
