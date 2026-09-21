"""Seed the three commission groups (idempotent). CFO 2026-07-15."""
from decimal import Decimal
from django.db import migrations

GROUPS = [
    ('independent', 'Independent agents',        Decimal('0.1000'), 'direct_bank', 'Bokani Makosha'),
    ('in_house',    'In-house / payroll agents', Decimal('0.0000'), 'payroll',     'Tlamelo Chimidza'),
    ('bdu',         'BDU domestic sales team',   Decimal('0.0000'), 'payroll',     'Tlamelo Chimidza'),
]

def seed(apps, se):
    G = apps.get_model('commissions', 'CommissionGroup')
    for key, name, rate, pv, owner in GROUPS:
        G.objects.update_or_create(key=key, defaults={'name': name, 'withholding_rate': rate,
            'pays_via': pv, 'owner_name': owner, 'is_active': True})

def unseed(apps, se):
    apps.get_model('commissions', 'CommissionGroup').objects.filter(key__in=[g[0] for g in GROUPS]).delete()

class Migration(migrations.Migration):
    dependencies = [('commissions', '0001_initial')]
    operations = [migrations.RunPython(seed, unseed)]
