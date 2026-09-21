"""
procurement/match_models.py

Line-level 3-way match.

The existing POBillMatch records the HEADER-level match (PO total vs bill
total). For tight control we also need to record which PO line + GRN line
each BILL line was matched against, so that:
  - A bill cannot exceed the qty received on any single PO line.
  - Per-line variance is visible in the audit trail, not just the header.

Models:
  - POBillMatchLine    one row per (match, bill_line) linking back to
                       po_line and (optionally) grn_line.

Also adds PurchaseOrderLine.qty_billed_to_date — a running counter bumped
inside match_bill_lines() that the over-bill guard reads.

Imported into procurement/models.py at the bottom so the models register
on the procurement app_label and migrations land in procurement/migrations/.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models

from core.models import BaseModel


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# POBillMatchLine
# ---------------------------------------------------------------------------

class POBillMatchLine(BaseModel):
    """
    Line-level match record. One row per matched BILL line.

    qty            — qty taken from the bill line.
    unit_price     — bill line unit price (for variance calc).
    line_variance  — (bill_qty * bill_unit_price) - (po_qty * po_unit_price)
                     positive = bill is over PO. Negative = under.
    """

    match     = models.ForeignKey(
                    'procurement.POBillMatch',
                    on_delete=models.CASCADE,
                    related_name='match_lines',
                )
    po_line   = models.ForeignKey(
                    'procurement.PurchaseOrderLine',
                    on_delete=models.PROTECT,
                    related_name='bill_match_lines',
                )
    grn_line  = models.ForeignKey(
                    'procurement.GoodsReceiptNoteLine',
                    null=True, blank=True,
                    on_delete=models.PROTECT,
                    related_name='bill_match_lines',
                    help_text='Most-recent GRN line for the same PO line at '
                              'the time of the match. NULL for service '
                              'bills with no goods receipt.',
                )
    bill_line = models.ForeignKey(
                    'billing.InvoiceLine',
                    on_delete=models.PROTECT,
                    related_name='match_records',
                )
    qty           = models.DecimalField(max_digits=12, decimal_places=4, default=ZERO)
    unit_price    = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    line_variance = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    class Meta(BaseModel.Meta):
        verbose_name        = 'PO ↔ Bill Match Line'
        verbose_name_plural = 'PO ↔ Bill Match Lines'
        ordering            = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['match', 'bill_line'],
                name='uq_po_bill_match_line',
            ),
        ]

    def __str__(self):
        return f'match-line {self.po_line_id} → {self.bill_line_id} ({self.qty})'
