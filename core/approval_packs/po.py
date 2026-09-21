"""The detail pack behind ONE purchase order.

A signer sees the supplier, every line item with its quantity and price, the
subtotal/VAT/total, and why the order was raised. The phone app keeps its own
richer PO pack (it also opens the attached quotes); this one is what the email
and the no-login confirm page show.
"""
from __future__ import annotations

from decimal import Decimal

from core.approval_pack import make_pack, money


def _row(pk):
    from procurement.models import PurchaseOrder
    return (PurchaseOrder.objects
            .select_related("supplier", "company", "currency_code")
            .prefetch_related("lines")
            .filter(pk=pk).first())


def build(pk):
    po = _row(pk)
    if po is None:
        return None

    # Currency.__str__ is "BWP - Pula"; the id IS the code.
    cur = po.currency_code_id or "BWP"
    lines = sorted(po.lines.all(), key=lambda l: (l.sequence or 0))
    rows = [[l.description, str(l.quantity or ""), money(l.unit_price, ""),
             money(l.line_total, "")] for l in lines]
    line_sum = sum((Decimal(l.line_total) for l in lines if l.line_total), Decimal("0.00"))

    in_bwp = ""
    if po.total_bwp is not None and po.total_amount is not None \
            and Decimal(po.total_bwp) != Decimal(po.total_amount):
        in_bwp = money(po.total_bwp)

    summary = [
        {"label": "PO number", "value": po.po_number or ""},
        {"label": "Supplier", "value": getattr(po.supplier, "name", "")},
        {"label": "Department", "value": po.get_department_display()},
        {"label": "Company", "value": getattr(po.company, "name", "")},
        {"label": "Needed by", "value": str(po.expected_delivery_date)
         if po.expected_delivery_date else ""},
        {"label": "Subtotal", "value": money(po.subtotal, cur)},
        {"label": "Tax", "value": money(po.tax_total, cur)},
        {"label": "Total", "value": money(po.total_amount, cur)},
        {"label": "Total in BWP", "value": in_bwp},
        {"label": "Why", "value": (po.justification or "")[:200]},
        {"label": "Status", "value": po.get_status_display()},
    ]

    items = []
    if po.subtotal is not None and abs(line_sum - Decimal(po.subtotal)) > Decimal("0.01"):
        items.append("The lines do not add up to the order subtotal")
    if not po.justification:
        items.append("No reason given for this order")

    return make_pack(
        "po",
        title=f"{getattr(po.supplier, 'name', '')} · {money(po.total_amount, cur)}".strip(" ·"),
        subtitle=f"Purchase order {po.po_number or ''}".strip(),
        summary=summary,
        columns=["Item", "Qty", "Unit price", "Line total"],
        rows=rows,
        row_total=money(po.total_amount, cur),
        checks={"level": "check" if items else "clean", "items": items},
    )
