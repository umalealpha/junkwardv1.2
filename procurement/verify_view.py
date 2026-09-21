"""
procurement/verify_view.py — public PO verification page.

This is the page the QR code on the PO print opens. A supplier or panel beater
scans the code and lands here to confirm the document is a GENUINE Alpha Direct
purchase order. View-only, no login.

The link carries the PO's UUID primary key — unguessable, so this is a
capability URL: only someone holding the printed document has it, and the page
discloses nothing beyond what is already on that document (number, type,
supplier name, total, date, status). No enumeration is possible.
"""
import base64
import uuid
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import PurchaseOrder
from .pdf import _logo_round_path, _money


# Statuses at which the document is an approved Purchase Order (vs an RFQ).
_APPROVED = {
    PurchaseOrder.Status.APPROVED,
    PurchaseOrder.Status.PARTIALLY_RECEIVED,
    PurchaseOrder.Status.FULLY_RECEIVED,
    PurchaseOrder.Status.CLOSED,
}

# Supplier-facing status wording — the internal "Pending FM/CFO Approval"
# labels don't belong on an external document, so collapse them.
_STATUS_LABEL = {
    PurchaseOrder.Status.DRAFT:                'Awaiting approval',
    PurchaseOrder.Status.PENDING_FM_APPROVAL:  'Awaiting approval',
    PurchaseOrder.Status.PENDING_CFO_APPROVAL: 'Awaiting approval',
    PurchaseOrder.Status.APPROVED:             'Approved',
    PurchaseOrder.Status.PARTIALLY_RECEIVED:   'Partially received',
    PurchaseOrder.Status.FULLY_RECEIVED:       'Fully received',
    PurchaseOrder.Status.CLOSED:               'Closed',
    PurchaseOrder.Status.REJECTED:             'Rejected',
    PurchaseOrder.Status.CANCELLED:            'Cancelled',
    PurchaseOrder.Status.EXPIRED:              'Expired',
}


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    p = _logo_round_path()
    if not p:
        return ''
    data = Path(p).read_bytes()
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


@require_GET
def po_verify(request, code):
    base = {'logo': _logo_data_uri(), 'public_base': settings.PUBLIC_BASE_URL}
    try:
        pk = uuid.UUID(str(code))
        po = PurchaseOrder.objects.select_related('supplier').get(pk=pk)
    except (ValueError, PurchaseOrder.DoesNotExist):
        return render(request, 'procurement/po_verify.html',
                      {**base, 'ok': False}, status=404)

    is_po = po.status in _APPROVED
    ccy = po.currency_code_id or 'BWP'
    ctx = {
        **base,
        'ok': True,
        'doc_type': 'Purchase Order' if is_po else 'Request for Quotation',
        'number': po.po_number or '(draft)',
        'supplier': po.supplier.name if po.supplier_id else '—',
        'total': _money(po.total_amount, ccy),
        'date': po.issue_date.strftime('%d %b %Y') if po.issue_date else '—',
        'status': _STATUS_LABEL.get(po.status, po.get_status_display()),
    }
    return render(request, 'procurement/po_verify.html', ctx, status=200)
