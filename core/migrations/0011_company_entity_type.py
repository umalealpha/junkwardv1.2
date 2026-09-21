"""
0011_company_entity_type — add Company.entity_type + backfill.

CFO directive 2026-05-19 (Manus master guide § 2): the dashboard must
branch between insurance metrics (GWP / Claims / Combined Ratio) and
trading metrics (Revenue / GP / EBITDA / PAT). Add a field on Company,
default everything to trading, then mark ADIC + ADSA as insurance.
"""

from django.db import migrations, models


INSURANCE_CODES = {'ADIC', 'ADSA'}


def backfill_entity_types(apps, schema_editor):
    Company = apps.get_model('core', 'Company')
    for c in Company.objects.all():
        code = (c.code or '').upper()
        if code in INSURANCE_CODES:
            c.entity_type = 'insurance'
        else:
            # Default fall-through is already 'trading'; this is explicit
            # so future re-runs are idempotent.
            c.entity_type = 'trading'
        c.save(update_fields=['entity_type'])


def reverse(apps, schema_editor):
    # Schema change can be reversed; data backfill cannot.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_monthly_commandments'),
    ]

    operations = [
        migrations.AddField(
            model_name='company',
            name='entity_type',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('insurance',    'Insurance'),
                    ('trading',      'Trading'),
                    ('consolidated', 'Consolidated Group'),
                ],
                default='trading',
                help_text=(
                    'Picks the dashboard layout: insurance metrics '
                    '(GWP / Claims) vs trading metrics (Revenue / PAT).'
                ),
            ),
        ),
        migrations.RunPython(backfill_entity_types, reverse),
    ]
