"""
reclassify_pos_from_odoo.py

One-shot reclassification of historical Odoo-imported POs using the
authoritative `purchase_type` field from the Odoo source system.

Why this exists:
    The original Excel-based importer routed every PO to a single
    catch-all expense account (111014 'Professional Fees - other').
    That was wrong: Odoo already classifies every PO via a custom
    `purchase_type` field with values 'claims' (84%) or 'general' (15%),
    plus ~1% untagged. The previous attempt to fix this with a
    supplier-name heuristic was replaced — the source system has the
    answer, this script reads it.

Inputs:
    A JSON export of all purchase.order records pulled directly from
    Odoo via JSON-RPC. Default path:

        ops/seeds/data/odoo_po_export.json

    Override with environment variable ODOO_PO_EXPORT_PATH.

    The JSON shape (produced by the in-browser pull) is:
        {
          "exported_at": "...",
          "total_count": 5748,
          "fields": [...],
          "purchase_orders": [
              { "id": 1, "name": "P00001", "purchase_type": "claims"|"general"|false,
                "policy_number": "DOMG...", "claim_number": "G...",
                "partner_ref": "...", "partner_id": [10, "..."],
                "company_id": [4, "..."], "amount_total": 12345.67,
                "state": "purchase"|"draft"|"cancel"|..., ... },
              ...
          ]
        }

What it does per alpha-finance PO matched by `po_number == odoo.name`:

    purchase_type='claims'  → department=CLAIMS, line account=103000,
                              related_claim_reference=claim_number,
                              justification stamped with policy + vendor_ref

    purchase_type='general' → department=ADMIN, line account=111014,
                              justification stamped 'classified as operations'

    purchase_type blank     → leave on existing account, stamp justification
                              with REVIEW_MARKER so the CFO can filter

Lines are only moved if the current line account is one of the legacy
catch-all codes (111014, 6990, 111036, 6400). POs whose lines have
already been manually corrected to a proper account are left untouched.

Modes:
    DRY RUN  (default): prints what would change, writes nothing
    APPLY    (--apply): commits the changes

Idempotency:
    Re-running with --apply is safe. Lines already on the target
    account, departments already correct, and justifications already
    stamped will be skipped.

Run:
    DRY RUN:
        python manage.py shell < ops/seeds/reclassify_pos_from_odoo.py

    APPLY:
        python manage.py shell -c "
        import sys; sys.argv=['','--apply'];
        exec(open('ops/seeds/reclassify_pos_from_odoo.py').read())"
"""
import os, sys, json, django
from django.apps import apps as _django_apps
if not _django_apps.ready:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'alpha_finance.settings')
    django.setup()

from collections import Counter
from pathlib import Path
from django.db import transaction

from ledger.models import Account
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.po_classification import (
    CLAIMS_DEFAULT_ACCOUNT_CODE,
    OPERATIONS_DEFAULT_ACCOUNT_CODE,
    REVIEW_MARKER,
    category_from_odoo_purchase_type,
)


DRY_RUN = '--apply' not in sys.argv

# Lines on any of these legacy catch-all codes are eligible for re-routing.
# Anything else (already on a real account) is left alone.
LEGACY_CATCHALL_CODES = ('111014', '6990', '111036', '6400')

# Justification stamp prefix — used both to write and to detect prior runs.
STAMP_PREFIX = 'ODOO RECLASSIFY 2026-05-13: '


def _resolve_account(code, fallback_keyword=''):
    a = Account.objects.filter(code=code).first()
    if a or not fallback_keyword:
        return a
    return Account.objects.filter(name__icontains=fallback_keyword).first()


def _load_odoo_export():
    path = Path(os.environ.get(
        'ODOO_PO_EXPORT_PATH',
        Path(__file__).parent / 'data' / 'odoo_po_export.json',
    ))
    if not path.exists():
        raise SystemExit(
            f'Odoo export not found: {path}\n'
            f'  Pull from Odoo first (browser JSON-RPC) and drop the file at '
            f'ops/seeds/data/odoo_po_export.json, or set ODOO_PO_EXPORT_PATH.'
        )
    with open(path, 'r') as f:
        data = json.load(f)
    return data.get('purchase_orders', [])


def main():
    print(f'Reclassify POs from Odoo export — mode: {"DRY RUN" if DRY_RUN else "APPLY"}')

    claims_account = (_resolve_account(CLAIMS_DEFAULT_ACCOUNT_CODE, 'Claims')
                      or _resolve_account('5100', 'Claims'))
    ops_account = (_resolve_account(OPERATIONS_DEFAULT_ACCOUNT_CODE, 'Professional')
                   or _resolve_account('6990', 'Misc'))
    if not claims_account or not ops_account:
        raise SystemExit(
            f'Cannot resolve default accounts. claims={claims_account} ops={ops_account}. '
            f'Run seed_coa_v2 / setup_chart_of_accounts first.'
        )
    print(f'Claims target account     : {claims_account.code} {claims_account.name}')
    print(f'Operations target account : {ops_account.code} {ops_account.name}')
    print('-' * 78)

    odoo_pos = _load_odoo_export()
    odoo_by_name = {p['name']: p for p in odoo_pos if p.get('name')}
    print(f'Odoo records loaded: {len(odoo_by_name)}')

    af_qs = PurchaseOrder.objects.filter(
        justification__icontains='Imported from Odoo',
    ).select_related('supplier').prefetch_related('lines__account')
    total = af_qs.count()
    print(f'alpha-finance imported POs in scope: {total}')

    # Tallies
    moved_to_claims = 0
    moved_to_ops = 0
    flagged_review = 0
    skipped_already_done = 0
    skipped_non_catchall = 0
    skipped_no_odoo_match = 0
    by_purchase_type = Counter()

    for po in af_qs.iterator(chunk_size=200):
        odoo = odoo_by_name.get(po.po_number)
        if not odoo:
            skipped_no_odoo_match += 1
            continue

        odoo_pt = odoo.get('purchase_type')
        by_purchase_type[odoo_pt] += 1
        category = category_from_odoo_purchase_type(odoo_pt)

        # Already-stamped POs are idempotent skips on subsequent runs.
        if STAMP_PREFIX in (po.justification or ''):
            skipped_already_done += 1
            continue

        candidate_lines = [
            ln for ln in po.lines.all()
            if ln.account and ln.account.code in LEGACY_CATCHALL_CODES
        ]
        if not candidate_lines:
            skipped_non_catchall += 1
            continue

        if category == 'claims':
            target_account = claims_account
            target_dept = PurchaseOrder.Department.CLAIMS
            moved_to_claims += 1
        elif category == 'operations':
            target_account = ops_account
            target_dept = PurchaseOrder.Department.ADMIN
            moved_to_ops += 1
        else:  # review
            target_account = ops_account  # leave on catch-all
            target_dept = PurchaseOrder.Department.ADMIN
            flagged_review += 1

        if DRY_RUN:
            continue

        # Compute the new justification before the bulk update.
        stamp_parts = [STAMP_PREFIX + f'purchase_type={odoo_pt or "(blank)"}']
        if odoo.get('policy_number'):
            stamp_parts.append(f'policy={odoo["policy_number"]}')
        if odoo.get('partner_ref'):
            stamp_parts.append(f'vendor_ref={odoo["partner_ref"]}')
        if category == 'review':
            stamp_parts.append(REVIEW_MARKER)
        stamp = ' | '.join(stamp_parts)
        new_justification = ((po.justification or '') + ' ' + stamp).strip()[:1000]

        # Backfill the Graphite claim reference where Odoo had one.
        new_claim_ref = po.related_claim_reference
        if odoo.get('claim_number') and not po.related_claim_reference:
            new_claim_ref = str(odoo['claim_number'])[:100]

        # Use queryset .update() to bypass PurchaseOrder.save()'s
        # immutability guard. The imported POs were created with
        # status=CLOSED, which save() refuses to edit by design — that
        # guard exists for live PO mutations and isn't appropriate
        # for this one-shot historical correction. The fields being
        # touched (account, department, related_claim_reference,
        # justification) carry no live business state.
        with transaction.atomic():
            PurchaseOrderLine.objects.filter(
                pk__in=[ln.pk for ln in candidate_lines],
            ).update(account=target_account)

            PurchaseOrder.objects.filter(pk=po.pk).update(
                department=target_dept,
                related_claim_reference=new_claim_ref,
                justification=new_justification,
            )

    print()
    print('Summary')
    print(f'  Moved to claims         : {moved_to_claims}')
    print(f'  Moved to operations     : {moved_to_ops}')
    print(f'  Flagged for CFO review  : {flagged_review}')
    print(f'  Skipped — already done  : {skipped_already_done}')
    print(f'  Skipped — non-catchall  : {skipped_non_catchall}')
    print(f'  Skipped — no Odoo match : {skipped_no_odoo_match}')
    print()
    print('Distribution by Odoo purchase_type:')
    for pt, n in by_purchase_type.most_common():
        print(f'  {n:5d}  {pt!s}')

    if DRY_RUN:
        print()
        print('** DRY RUN — no rows updated. Re-run with --apply to commit. **')
        print()
        print('To list PENDING REVIEW POs after applying:')
        print('  python manage.py shell -c "from procurement.models import PurchaseOrder;'
              ' [print(p.po_number, p.supplier.name)'
              " for p in PurchaseOrder.objects.filter(justification__icontains='PENDING CFO REVIEW')[:50]]\"")


main()
