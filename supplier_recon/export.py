"""Excel export of a supplier reconciliation month.

Payables asked for the monthly board as a file — it goes to the auditors and
into the month-end pack. Reuses ``aware.reporting._wb`` so the workbook looks
like every other Alpha Direct export (navy header band, KPI block, striped
rows, notes footer) instead of introducing a second house style.

Read-only: builds a workbook from an existing run and streams it. It never
rebuilds, never recomputes, and never writes.
"""

from __future__ import annotations

from django.http import HttpResponse

from aware.reporting import _wb

from .constants import LedgerStage
from .models import Escalation, InvoiceReconItem

COLUMNS = [
    'Supplier', 'Type', 'Bill', 'Issue date', 'Due date', 'Currency',
    'Billed (BWP)', 'Paid (BWP)', 'Still owed (BWP)', 'Payment status',
    'In ledger?', '3-way match', 'PO', 'Goods receipt', 'Claim ref',
    'Days past due', 'Age bucket', 'Reason', 'Justification',
    'Actioned by', 'Escalated',
]

# Zero-based indices of the money columns, for '#,##0' formatting.
MONEY_COLS = (6, 7, 8)


def _fmt(value) -> str:
    return '' if value is None else str(value)


def build_workbook(run):
    items = (InvoiceReconItem.objects
             .filter(line__run=run)
             .select_related('invoice', 'invoice__currency_code', 'reason_code',
                             'line', 'line__supplier', 'purchase_order',
                             'goods_receipt', 'actioned_by')
             .prefetch_related('escalations')
             .order_by('line__supplier__name', 'due_date'))

    rows = []
    for it in items:
        live = [e for e in it.escalations.all()
                if e.status in ('open', 'acknowledged')]
        rows.append([
            it.line.supplier.name,
            it.line.get_category_display(),
            it.invoice.invoice_number,
            str(it.invoice.issue_date),
            str(it.due_date or ''),
            it.invoice.currency_code_id or '',
            float(it.amount),
            float(it.amount_paid),
            float(it.amount_outstanding),
            it.get_payment_status_display(),
            'Yes' if it.is_posted else 'NOT POSTED',
            it.get_match_status_display(),
            _fmt(it.purchase_order.po_number if it.purchase_order_id else ''),
            _fmt(it.goods_receipt.grn_number if it.goods_receipt_id else ''),
            it.claim_reference,
            it.days_past_due,
            it.ageing_bucket,
            it.reason_code.label if it.reason_code_id else '',
            (it.justification or '').strip(),
            (it.actioned_by.get_full_name() or it.actioned_by.username)
            if it.actioned_by_id else '',
            'Yes' if live else '',
        ])

    unexplained = sum(
        1 for it in items
        if it.needs_justification and (
            not it.actioned or it.reason_code_id is None
            or not (it.justification or '').strip())
    )
    open_escalations = Escalation.objects.filter(
        item__line__run=run, status__in=['open', 'acknowledged']).count()
    not_posted = items.filter(ledger_stage=LedgerStage.DRAFT).count()

    kpis = [
        ('Entity', f"{run.company.code} — {run.company.name}"),
        ('Status', run.get_status_display()),
        ('Suppliers', str(run.lines.count())),
        ('Bills', str(items.count())),
        ('Billed (BWP)', f"{run.total_invoiced:,.2f}"),
        ('Paid (BWP)', f"{run.total_paid:,.2f}"),
        ('Still owed (BWP)', f"{run.total_unpaid:,.2f}"),
        ('On hold (BWP)', f"{run.total_held:,.2f}"),
        ('Not yet in the ledger (BWP)', f"{run.total_not_posted:,.2f}"),
        ('Bills still needing a reason', str(unexplained)),
        ('Open escalations', str(open_escalations)),
        ('Prepared by', run.prepared_by_name if hasattr(run, 'prepared_by_name')
         else (run.prepared_by.get_full_name() if run.prepared_by_id else '—')),
        ('Signed off by', (run.reviewed_by.get_full_name()
                           if run.reviewed_by_id else 'not yet signed off')),
    ]

    notes = [
        'Paid amounts are as at the period end, derived from confirmed payment '
        'allocations — not the invoice\'s current paid figure.',
        'Amounts are BWP, converted at the exchange rate locked on each bill. '
        'The transaction currency and face amount are shown per row.',
        f'{not_posted} bill(s) are marked NOT POSTED: raised but never posted to '
        'the general ledger, so they are absent from the balance sheet and the '
        'AP Aging report. They are included here deliberately.',
        'The posted subset reconciles to the AP Aging report — same open '
        'statuses, same issue-date cut, same ageing bands.',
        'This board explains payments. It does not make them.',
    ]

    return _wb(
        title=f"Supplier Payables Reconciliation — {run.period_label}",
        subtitle=(f"{run.company.name} · {run.period_start} to {run.period_end} "
                  f"· {run.get_status_display()}"),
        kpis=kpis, columns=COLUMNS, rows=rows, notes=notes,
        money_cols=MONEY_COLS,
    )


def workbook_response(run) -> HttpResponse:
    buf = build_workbook(run)
    fname = f"supplier-recon-{run.company.code}-{run.period_label}.xlsx"
    resp = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.'
                     'spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{fname}"'
    return resp
