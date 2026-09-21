"""
load_purchase_orders.py

Loads historical purchase orders from an Excel export into alpha-finance.

Source file (CFO supplied):
    C:/Users/PrathapAsus/Downloads/Purchase Order (purchase.order).xlsx

Sheet1 columns:
    Priority | Order Reference | Confirmation Date | Vendor | Company |
    Buyer | Activities | Source Document | Total | Billing Status | Expected Arrival

4421 rows, dated 2022-03-17 through 2026-05-12, P 84,955,206.67 total value.
All "Nothing to Bill" status (loaded as CLOSED). All for one company.

Design choices:
- Vendors get-or-created as billing.Contact with contact_type='vendor'.
- Buyer name retained in justification text; created_by = admin user (the importer).
- Department defaults to ADMIN (Excel doesn't carry this; CFO can rebucket later via SQL).
- Status = CLOSED — "Nothing to Bill" semantics.
- Each PO gets one lump-sum line (Excel has no line detail) on a generic
  expense account — chosen at runtime from what exists in this DB so the
  same script works on local (6-digit CoA) and production (4-digit CoA).
- po_number = Excel's "Order Reference" (e.g. 'P05887') — preserves audit trail.
- Idempotent: skips rows where po_number already exists.

Run:
    python manage.py shell < ops/seeds/load_purchase_orders.py

Or from a Django shell:
    exec(open('ops/seeds/load_purchase_orders.py').read())
"""
import os, sys, django
from django.apps import apps as _django_apps
from django.utils import timezone
if not _django_apps.ready:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'alpha_finance.settings_bot')
    django.setup()

from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
from django.contrib.auth import get_user_model
from django.db import transaction

from billing.models import Contact
from core.models import Company
from ledger.models import Account
from procurement.models import PurchaseOrder, PurchaseOrderLine


ZERO = Decimal('0.00')
User = get_user_model()

XLSX_PATH = Path(r'C:/Users/PrathapAsus/Downloads/Purchase Order (purchase.order).xlsx')


# ---------------------------------------------------------------------------
# Resolve dependencies once
# ---------------------------------------------------------------------------

def _admin_user():
    u = User.objects.filter(is_superuser=True).first() or User.objects.first()
    if not u:
        raise SystemExit('No User in DB; create a superuser before running.')
    return u


def _company():
    """Resolve the Alpha Direct Insurance Company record."""
    co = (Company.objects.filter(code__iexact='ADI').first()
          or Company.objects.filter(code__iexact='ADIC').first()
          or Company.objects.filter(name__icontains='Alpha Direct').first())
    if not co:
        raise SystemExit('No Company record matching Alpha Direct; aborting.')
    return co


def _default_expense_account():
    """Pick a sensible generic-expense account that exists in this DB.

    Production CoA (4-digit) → '6990 Miscellaneous expenses'.
    Local CoA (6-digit)     → '111014 Professional Fees - other' as catch-all.
    Falls back to whichever expense-ish account is available.
    """
    for code in ('6990', '111014', '6990 ', '111046'):
        a = Account.objects.filter(code=code.strip()).first()
        if a:
            return a
    # Last resort: any account with 'misc' or 'other' in its name
    a = (Account.objects.filter(name__icontains='misc').first()
         or Account.objects.filter(name__icontains='other').first()
         or Account.objects.filter(code__startswith='6').first()
         or Account.objects.filter(code__startswith='111').first())
    if not a:
        raise SystemExit('No suitable expense account found; aborting.')
    return a


# ---------------------------------------------------------------------------
# Vendor get-or-create
# ---------------------------------------------------------------------------

_vendor_cache = {}

def _vendor(name: str, company):
    if not name:
        name = 'Unknown Vendor (imported)'
    key = name.strip()
    if key in _vendor_cache:
        return _vendor_cache[key]
    v = Contact.objects.filter(name__iexact=key).first()
    if v is None:
        v = Contact.objects.create(
            name=key,
            contact_type='vendor',
        )
    _vendor_cache[key] = v
    return v


# ---------------------------------------------------------------------------
# Main load
# ---------------------------------------------------------------------------

def main():
    if not XLSX_PATH.exists():
        raise SystemExit(f'Source file not found: {XLSX_PATH}')

    print(f'Loading {XLSX_PATH.name}...')
    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True, read_only=True)
    ws = wb.active

    admin     = _admin_user()
    company   = _company()
    expense_a = _default_expense_account()
    print(f'  importer user: {admin.username}')
    print(f'  company: {company.name} ({company.code})')
    print(f'  default expense account for lump-sum lines: {expense_a.code} {expense_a.name}')
    print()

    created = 0
    skipped = 0
    failed = 0
    failures = []

    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or not row[1]:  # need Order Reference
            continue
        priority, ref, conf_date, vendor_name, company_name, buyer, activities, source_doc, total, billing, expected = row

        po_number = str(ref).strip()
        if PurchaseOrder.objects.filter(po_number=po_number).exists():
            skipped += 1
            continue

        issue_d = (conf_date.date() if hasattr(conf_date, 'date') else conf_date) or timezone.localdate()
        expected_d = expected.date() if hasattr(expected, 'date') else expected

        total_dec = Decimal(str(total)) if total is not None else ZERO

        vendor = _vendor(vendor_name, company)

        try:
            with transaction.atomic():
                po = PurchaseOrder(
                    po_number=po_number,
                    department=PurchaseOrder.Department.ADMIN,
                    supplier=vendor,
                    company=company,
                    issue_date=issue_d,
                    expected_delivery_date=expected_d,
                    currency_code_id='BWP',
                    exchange_rate=Decimal('1'),
                    subtotal=total_dec,
                    tax_total=ZERO,
                    total_amount=total_dec,
                    total_bwp=total_dec,
                    status=PurchaseOrder.Status.CLOSED,
                    justification=(f'Imported from Odoo export 2026-05-12. '
                                   f'Original buyer: {buyer or "—"}. '
                                   f'Billing status at export: {billing or "—"}.')[:1000],
                    created_by=admin,
                )
                po.save()
                PurchaseOrderLine.objects.create(
                    purchase_order=po,
                    description=(f'Imported lump sum — {vendor_name or "unknown"}')[:500],
                    account=expense_a,
                    quantity=Decimal('1'),
                    unit_price=total_dec,
                    line_total=total_dec,
                    tax_amount=ZERO,
                )
                created += 1
        except Exception as e:
            failed += 1
            failures.append((po_number, type(e).__name__, str(e)[:120]))

        if (created + skipped + failed) % 500 == 0:
            print(f'  ... processed {created + skipped + failed} (created={created} skipped={skipped} failed={failed})')

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
    print(f'  Total Contacts (vendors): {Contact.objects.filter(contact_type="vendor").count()}')


main()
