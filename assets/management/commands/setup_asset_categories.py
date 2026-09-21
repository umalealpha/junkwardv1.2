"""
assets/management/commands/setup_asset_categories.py

Idempotent — creates the four default asset categories that match the chart of
accounts already shipped with alpha-finance:

    OE  Office equipment        cost 1410   accum 1451   60 months  SL
    IT  IT equipment            cost 1420   accum 1452   36 months  SL
    MV  Motor vehicles          cost 1430   accum 1453   48 months  RB
    FF  Furniture and fittings  cost 1440   accum 1454  120 months  SL

Run:
    python manage.py setup_asset_categories
"""

from django.core.management.base import BaseCommand

from assets.models import AssetCategory
from ledger.models import Account


from decimal import Decimal

# (code, name, cost_acct, accum_acct, expense_acct,
#  book_method, months,
#  is_passenger_vehicle, ca_method, ca_rate, tax_cost_cap)
#
# ca_method / ca_rate / tax_cost_cap are BURS Income Tax Act capital-allowance
# defaults — they're independent from the accounting depreciation choices.
CATEGORIES = [
    ('OE', 'Office equipment',       '1410', '1451', '6600', 'straight_line',     60,
     False, 'reducing_balance', Decimal('25.00'), None),
    ('IT', 'IT equipment',           '1420', '1452', '6600', 'straight_line',     36,
     False, 'reducing_balance', Decimal('25.00'), None),
    ('MV', 'Motor vehicles',         '1430', '1453', '6600', 'reducing_balance',  48,
     True,  'reducing_balance', Decimal('25.00'), Decimal('175000.00')),
    ('FF', 'Furniture and fittings', '1440', '1454', '6600', 'straight_line',    120,
     False, 'reducing_balance', Decimal('25.00'), None),
]


class Command(BaseCommand):
    help = 'Create default asset categories aligned to the chart of accounts.'

    def handle(self, *args, **options):
        created = 0
        updated = 0
        skipped = 0
        for (code, name, cost_code, accum_code, expense_code,
             method, months,
             is_passenger, ca_method, ca_rate, tax_cap) in CATEGORIES:
            try:
                cost_acct    = Account.objects.get(code=cost_code)
                accum_acct   = Account.objects.get(code=accum_code)
                expense_acct = Account.objects.get(code=expense_code)
            except Account.DoesNotExist as exc:
                self.stderr.write(self.style.ERROR(
                    f'  ! {code}: missing GL account ({exc}). Run setup_chart_of_accounts first.'
                ))
                continue

            defaults = {
                'name': name,
                'cost_account': cost_acct,
                'accum_depr_account': accum_acct,
                'depreciation_expense_account': expense_acct,
                'default_method': method,
                'default_useful_life_months': months,
                'is_active': True,
                'is_passenger_vehicle': is_passenger,
                'default_capital_allowance_method': ca_method,
                'default_capital_allowance_rate': ca_rate,
                'default_tax_cost_cap': tax_cap,
            }

            cat, was_created = AssetCategory.objects.get_or_create(code=code, defaults=defaults)
            if was_created:
                created += 1
                tag = ' [passenger vehicle — VAT denied, P175k cap]' if is_passenger else ''
                self.stdout.write(self.style.SUCCESS(f'  + {code} {name}{tag}'))
            else:
                # Backfill new tax fields on existing rows from older deployments
                changed = False
                for field, value in {
                    'is_passenger_vehicle': is_passenger,
                    'default_capital_allowance_method': ca_method,
                    'default_capital_allowance_rate': ca_rate,
                    'default_tax_cost_cap': tax_cap,
                }.items():
                    if getattr(cat, field, None) != value and getattr(cat, field, None) in (None, '', 0):
                        setattr(cat, field, value)
                        changed = True
                if changed:
                    cat.save()
                    updated += 1
                    self.stdout.write(self.style.WARNING(f'  ~ {code} {name} (backfilled tax fields)'))
                else:
                    skipped += 1
                    self.stdout.write(f'  = {code} {name} (already exists)')

        self.stdout.write(self.style.SUCCESS(
            f'Done. {created} created, {updated} updated, {skipped} unchanged.'
        ))
