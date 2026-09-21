"""
procurement/reports.py — PO reporting layer.

CFO directive 2026-05-20 (Manus PO audit gaps #2 + #5):

  GET /api/v1/reports/po-outstanding/?company=<id>&as_of=YYYY-MM-DD
      Outstanding PO aging — POs approved but not yet closed, bucketed
      by age (issue_date → today) into 0-30 / 31-60 / 61-90 / 91+ days.

  GET /api/v1/reports/po-commitment/?company=<id>&as_of=YYYY-MM-DD
      Total open commitment exposure (DR 1990 less GRN-reversed portion)
      grouped by company + department.

  GET /api/v1/reports/gr-ir-reconciliation/?company=<id>&as_of=YYYY-MM-DD
      Account 2145 (GR-IR clearing) balance breakdown:
        - GRNs posted but not yet billed (Dr side of 2145)
        - Bills posted but not yet GRN-matched (Cr side of 2145)
        - Net imbalance per supplier
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from procurement.models import PurchaseOrder, GoodsReceiptNote, POBillMatch


ZERO = Decimal('0.00')


def _d(v) -> str:
    return f'{Decimal(str(v or 0)):.2f}'


def _bucket(days: int) -> str:
    if days <= 30:  return '0-30'
    if days <= 60:  return '31-60'
    if days <= 90:  return '61-90'
    return '91+'


# ---------------------------------------------------------------------------
# 1. Outstanding PO aging
# ---------------------------------------------------------------------------

def build_po_outstanding(as_of: date, company_id: str | None = None) -> dict:
    OPEN_STATUSES = (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
    )
    qs = (PurchaseOrder.objects
          .filter(status__in=OPEN_STATUSES, issue_date__lte=as_of)
          .select_related('supplier', 'company'))
    if company_id:
        qs = qs.filter(company_id=company_id)

    rows = []
    buckets = defaultdict(lambda: {'count': 0, 'total_bwp': ZERO})
    total_bwp = ZERO
    total_count = 0

    for po in qs.iterator():
        age = (as_of - po.issue_date).days
        b = _bucket(age)
        rows.append({
            'po_number':     po.po_number,
            'supplier':      po.supplier.name if po.supplier_id else '',
            'company':       po.company.code if po.company_id else '',
            'department':    po.department,
            'issue_date':    str(po.issue_date),
            'age_days':      age,
            'bucket':        b,
            'status':        po.status,
            'total_amount':  _d(po.total_amount),
            'currency':      po.currency_code_id,
            'total_bwp':     _d(po.total_bwp),
        })
        buckets[b]['count']     += 1
        buckets[b]['total_bwp'] += Decimal(str(po.total_bwp))
        total_bwp += Decimal(str(po.total_bwp))
        total_count += 1

    return {
        'as_of':        str(as_of),
        'company_id':   company_id,
        'rows':         rows,
        'buckets': {
            b: {'count': v['count'], 'total_bwp': _d(v['total_bwp'])}
            for b, v in buckets.items()
        },
        'totals': {
            'count':     total_count,
            'total_bwp': _d(total_bwp),
        },
    }


# ---------------------------------------------------------------------------
# 2. Commitment exposure
# ---------------------------------------------------------------------------

def build_po_commitment(as_of: date, company_id: str | None = None) -> dict:
    """
    Open commitment = sum of (total_bwp − BWP-value of GRN-received lines)
    for APPROVED / PARTIALLY_RECEIVED POs. Aggregated by company + dept.
    """
    OPEN_STATUSES = (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
    )
    qs = (PurchaseOrder.objects
          .filter(status__in=OPEN_STATUSES, issue_date__lte=as_of)
          .select_related('company')
          .prefetch_related('lines'))
    if company_id:
        qs = qs.filter(company_id=company_id)

    groups = defaultdict(lambda: {'count': 0, 'gross_bwp': ZERO, 'remaining_bwp': ZERO})

    # iterator() with prefetch_related requires explicit chunk_size on Django 4+.
    for po in qs.iterator(chunk_size=200):
        total_qty = sum((ln.quantity for ln in po.lines.all()), Decimal('0'))
        recv_qty  = sum((ln.quantity_received for ln in po.lines.all()), Decimal('0'))
        gross_bwp = Decimal(str(po.total_bwp))
        # Pro-rata remaining commitment
        if total_qty > 0:
            received_frac = recv_qty / total_qty if total_qty else Decimal('0')
        else:
            received_frac = Decimal('0')
        remaining_bwp = gross_bwp * (Decimal('1') - received_frac)

        key = (po.company.code if po.company_id else '(no company)', po.department)
        groups[key]['count']         += 1
        groups[key]['gross_bwp']     += gross_bwp
        groups[key]['remaining_bwp'] += remaining_bwp

    rows = [
        {
            'company':       co,
            'department':    dept,
            'po_count':      v['count'],
            'gross_bwp':     _d(v['gross_bwp']),
            'remaining_bwp': _d(v['remaining_bwp']),
        }
        for (co, dept), v in sorted(groups.items())
    ]
    return {
        'as_of':       str(as_of),
        'company_id':  company_id,
        'rows':        rows,
        'totals': {
            'count':         sum(v['count']         for v in groups.values()),
            'gross_bwp':     _d(sum((v['gross_bwp']     for v in groups.values()), ZERO)),
            'remaining_bwp': _d(sum((v['remaining_bwp'] for v in groups.values()), ZERO)),
        },
    }


# ---------------------------------------------------------------------------
# 3. GR-IR clearing reconciliation
# ---------------------------------------------------------------------------

def build_gr_ir_reconciliation(as_of: date, company_id: str | None = None) -> dict:
    """
    Walks every GRN posted on or before as_of, sums the GR-IR debits
    (clearing account 2145), then matches against bills via POBillMatch.
    Imbalance per supplier = GRN debits − billed credits.
    """
    grn_qs = (GoodsReceiptNote.objects
              .filter(receipt_date__lte=as_of)
              .select_related('purchase_order__supplier',
                              'purchase_order__company',
                              'journal_entry'))
    if company_id:
        grn_qs = grn_qs.filter(purchase_order__company_id=company_id)

    by_supplier = defaultdict(lambda: {
        'grn_value_bwp': ZERO,
        'billed_bwp':    ZERO,
        'grn_count':     0,
        'bill_count':    0,
    })

    for grn in grn_qs.iterator():
        po = grn.purchase_order
        supplier = po.supplier.name if po.supplier_id else '(no supplier)'
        company  = po.company.code if po.company_id else '(no company)'
        # GRN BWP value = sum(received_qty × unit_price_bwp) per line
        grn_bwp = ZERO
        for line in grn.lines.select_related('po_line').all():
            unit_bwp = Decimal(str(line.po_line.unit_price)) * Decimal(str(po.exchange_rate or 1))
            grn_bwp += Decimal(str(line.quantity_received)) * unit_bwp
        key = (company, supplier)
        by_supplier[key]['grn_value_bwp'] += grn_bwp
        by_supplier[key]['grn_count']     += 1

    # Billed portion via POBillMatch (matched/override = billed)
    # billing.Invoice field is `issue_date`, not `invoice_date`.
    match_qs = (POBillMatch.objects
                .filter(bill__issue_date__lte=as_of,
                        match_status__in=['matched', 'override'])
                .select_related('purchase_order__supplier',
                                'purchase_order__company',
                                'bill'))
    if company_id:
        match_qs = match_qs.filter(purchase_order__company_id=company_id)

    for m in match_qs.iterator():
        po = m.purchase_order
        supplier = po.supplier.name if po.supplier_id else '(no supplier)'
        company  = po.company.code if po.company_id else '(no company)'
        key = (company, supplier)
        by_supplier[key]['billed_bwp']  += Decimal(str(m.bill.total_amount or 0))
        by_supplier[key]['bill_count']  += 1

    rows = []
    for (co, sup), v in sorted(by_supplier.items()):
        gap = v['grn_value_bwp'] - v['billed_bwp']
        rows.append({
            'company':         co,
            'supplier':        sup,
            'grn_count':       v['grn_count'],
            'grn_value_bwp':   _d(v['grn_value_bwp']),
            'bill_count':      v['bill_count'],
            'billed_bwp':      _d(v['billed_bwp']),
            'imbalance_bwp':   _d(gap),
        })

    return {
        'as_of':       str(as_of),
        'company_id':  company_id,
        'rows':        rows,
        'totals': {
            'grn_count':     sum(v['grn_count']     for v in by_supplier.values()),
            'bill_count':    sum(v['bill_count']    for v in by_supplier.values()),
            'grn_value_bwp': _d(sum((v['grn_value_bwp'] for v in by_supplier.values()), ZERO)),
            'billed_bwp':    _d(sum((v['billed_bwp']    for v in by_supplier.values()), ZERO)),
            'imbalance_bwp': _d(sum((v['grn_value_bwp'] - v['billed_bwp']
                                     for v in by_supplier.values()), ZERO)),
        },
    }
