"""
load_purchase_orders_from_odoo.py

Loads ADIC purchase orders from the Odoo extract dumped to ~/Desktop/Odoo-Extract/
into alpha-finance. Idempotent on po_number.

Source files (must be present on the machine running this script):
  Alpha_Direct_Insurance_Pty_Ltd/PurchaseOrders/orders.json
  Alpha_Direct_Insurance_Pty_Ltd/PurchaseOrders/order_lines.json
  _global/partners.json  (for supplier names)

Run:
    docker compose exec backend python manage.py shell < ops/seeds/load_purchase_orders_from_odoo.py

Notes:
- Status = CLOSED — historical POs, no workflow needed.
- Department = ADMIN (Odoo doesn't carry this field; CFO can rebucket via SQL).
- Each PO gets ONE lump-sum line on a generic expense account (6990 or fallback)
  because the line-level account mapping doesn't exist in this import. This
  matches the prior xlsx importer's behavior.
- Vendors created via billing.Contact.contact_type='vendor' if missing.
"""
import os, sys, django
from django.apps import apps as _django_apps
from django.utils import timezone
if not _django_apps.ready:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'alpha_finance.settings_bot')
    django.setup()

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction

from billing.models import Contact
from core.models import Company
from ledger.models import Account
from procurement.models import PurchaseOrder, PurchaseOrderLine

ZERO = Decimal('0.00')
User = get_user_model()

# Source dirs — search a few likely paths so the script runs on Mac (Desktop)
# or on prod (file shipped to /tmp via scp).
_CANDIDATE_PATHS = [
    Path('/data/odoo-extract/Alpha_Direct_Insurance_Pty_Ltd/PurchaseOrders'),
    Path('/tmp/odoo-extract/Alpha_Direct_Insurance_Pty_Ltd/PurchaseOrders'),
    Path.home() / 'Desktop' / 'Odoo-Extract' / 'Alpha_Direct_Insurance_Pty_Ltd' / 'PurchaseOrders',
]
SRC = next((p for p in _CANDIDATE_PATHS if p.exists()), None)
if SRC is None:
    raise SystemExit(f'Source PO folder not found. Tried: {_CANDIDATE_PATHS}')

ORDERS_PATH = SRC / 'orders.json'
PARTNERS_PATH = SRC.parent.parent / '_global' / 'partners.json'


def _admin_user():
    u = User.objects.filter(is_superuser=True).first() or User.objects.first()
    if not u:
        raise SystemExit('No User in DB; create a superuser before running.')
    return u


def _company():
    co = (Company.objects.filter(code__iexact='ADIC').first()
          or Company.objects.filter(code__iexact='ADI').first()
          or Company.objects.filter(name__icontains='Alpha Direct Insurance').first())
    if not co:
        raise SystemExit('No Company record matching Alpha Direct Insurance; aborting.')
    return co


def _default_expense_account():
    for code in ('6990', '6995', '6999', '111014', '111046'):
        a = Account.objects.filter(code=code).first()
        if a:
            return a
    a = (Account.objects.filter(name__icontains='misc').first()
         or Account.objects.filter(name__icontains='other').first()
         or Account.objects.filter(code__startswith='6').first()
         or Account.objects.filter(code__startswith='111').first())
    if not a:
        raise SystemExit('No suitable expense account found; aborting.')
    return a


_vendor_cache = {}

def _vendor(name):
    if not name:
        name = 'Unknown Vendor (imported from Odoo)'
    key = (name or '').strip()
    if key in _vendor_cache:
        return _vendor_cache[key]
    v = Contact.objects.filter(name__iexact=key).first()
    if v is None:
        v = Contact.objects.create(name=key, contact_type='vendor')
    _vendor_cache[key] = v
    return v


def _m2o_name(v):
    """Odoo m2o field is [id, display_name] or False."""
    if isinstance(v, list) and len(v) >= 2:
        return v[1]
    return None


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.split(' ')[0]).date()
    except Exception:
        return None


def main():
    print(f'Source: {ORDERS_PATH}')
    orders = json.loads(ORDERS_PATH.read_text())
    print(f'Loaded {len(orders)} orders from JSON')

    admin = _admin_user()
    company = _company()
    expense_a = _default_expense_account()
    print(f'  importer: {admin.username}')
    print(f'  company:  {company.name} ({company.code})')
    print(f'  account:  {expense_a.code} {expense_a.name}')

    created = 0; skipped = 0; failed = 0
    failures = []

    for i, o in enumerate(orders, 1):
        po_number = (o.get('name') or '').strip()
        if not po_number:
            continue
        if PurchaseOrder.objects.filter(po_number=po_number).exists():
            skipped += 1
            continue

        vendor_name = _m2o_name(o.get('partner_id'))
        vendor = _vendor(vendor_name)
        issue_d = _parse_date(o.get('date_order')) or timezone.localdate()
        total = Decimal(str(o.get('amount_total') or 0))
        untaxed = Decimal(str(o.get('amount_untaxed') or 0)) or total
        tax = total - untaxed if total > untaxed else ZERO
        ccy = _m2o_name(o.get('currency_id')) or 'BWP'
        state = o.get('state') or 'closed'
        odoo_user = _m2o_name(o.get('user_id')) or '—'

        try:
            with transaction.atomic():
                po = PurchaseOrder(
                    po_number=po_number,
                    department=PurchaseOrder.Department.ADMIN,
                    supplier=vendor,
                    company=company,
                    issue_date=issue_d,
                    currency_code_id='BWP' if ccy not in ('USD','ZAR','EUR','GBP','INR') else ccy,
                    exchange_rate=Decimal('1'),
                    subtotal=untaxed,
                    tax_total=tax,
                    total_amount=total,
                    total_bwp=total,
                    status=PurchaseOrder.Status.CLOSED,
                    justification=(f'Imported from Odoo extract 2026-05-20. '
                                   f'Original buyer: {odoo_user}. '
                                   f'Odoo state at export: {state}.')[:1000],
                    created_by=admin,
                )
                po.save()
                # bypass strict line-level clean() by using save_base + recompute later
                PurchaseOrderLine.objects.create(
                    purchase_order=po,
                    description=(f'Imported lump sum — {vendor_name or "unknown"}')[:500],
                    account=expense_a,
                    quantity=Decimal('1'),
                    unit_price=total,
                    line_total=total,
                    tax_amount=tax,
                )
                created += 1
        except Exception as e:
            failed += 1
            failures.append((po_number, type(e).__name__, str(e)[:120]))

        if i % 500 == 0:
            print(f'  ... {i}/{len(orders)}  created={created} skipped={skipped} failed={failed}')

    print()
    print('Done.')
    print(f'  Created : {created}')
    print(f'  Skipped : {skipped} (already in DB)')
    print(f'  Failed  : {failed}')
    if failures[:10]:
        print('  First 10 failures:')
        for r, etype, msg in failures[:10]:
            print(f'    {r}  {etype}: {msg}')
    print(f'  Total PurchaseOrders in DB now: {PurchaseOrder.objects.count()}')


main()
