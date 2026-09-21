"""Supplier Payables Reconciliation — supplier-statement ingestion.

The "front half" added after the 2026-08-24 usability test: the existing module
reconciles Omni's own invoices, payments, POs and claims against each other; it
had no way to take a *supplier's own statement of account* and reconcile it
against what Omni holds. These models store an uploaded statement, its parsed
lines, and the line-by-line match result against the run's vendor bills.

Same discipline as the rest of the app:
* UUID pk + audit via BaseModel / AuditableMixin.
* Decimal money, never float.
* Moves NO money and posts NO GL — it produces a payment *recommendation* the
  CFO authorises in FNB.
* Scoped through the SupplierReconLine (run -> company), so a statement cannot
  be read across legal entities.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models import AuditableMixin, BaseModel

from .constants import (
    PaymentProposal,
    StatementLineType,
    StatementStatus,
    StmtMatchType,
    ZERO,
)
from .models import InvoiceReconItem, SupplierReconLine


def _statement_upload_path(instance, filename):
    # Grouped by year/month; the SupplierStatement row carries the real name.
    return f'supplier_statements/{filename}'


class SupplierStatement(AuditableMixin, BaseModel):
    """One supplier statement of account, uploaded against a supplier line.

    The line fixes company + supplier + period, so a statement is always tied to
    an existing built month (Finance builds the board first, then uploads the
    statement the supplier sent for that month)."""

    line = models.ForeignKey(SupplierReconLine, on_delete=models.CASCADE,
                             related_name='statements')

    statement_date  = models.DateField(null=True, blank=True,
                                        help_text='Date printed on the statement.')
    currency        = models.CharField(max_length=3, default='BWP')
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2,
                                          null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=18, decimal_places=2,
                                          null=True, blank=True)

    # Evidence — the original file kept verbatim, with a checksum so the same
    # file cannot be uploaded twice against the same line (duplicate control).
    original_file = models.FileField(upload_to=_statement_upload_path,
                                     max_length=400, null=True, blank=True)
    file_name     = models.CharField(max_length=400, blank=True, default='')
    content_type  = models.CharField(max_length=120, blank=True, default='')
    file_size     = models.PositiveIntegerField(default=0)
    checksum      = models.CharField(max_length=64, blank=True, default='',
                                     help_text='SHA-256 of the uploaded bytes.')

    status      = models.CharField(max_length=12, choices=StatementStatus.choices,
                                   default=StatementStatus.UPLOADED)
    parse_error = models.TextField(blank=True, default='')

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='uploaded_supplier_statements')
    matched_at  = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            # Same bytes uploaded twice against one supplier month = a duplicate
            # upload, rejected. A genuinely revised statement has different bytes
            # (and therefore a different checksum) so it is allowed through.
            models.UniqueConstraint(
                fields=['line', 'checksum'],
                condition=models.Q(checksum__gt=''),
                name='uniq_statement_line_checksum'),
        ]
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['line', 'status'], name='stmt_line_status_idx'),
        ]

    def __str__(self):
        return f'Statement {self.file_name or self.pk} @ {self.line.run.period_label}'

    @property
    def company_id(self):
        return self.line.run.company_id


class SupplierStatementLine(BaseModel):
    """One parsed row of a supplier statement."""

    statement = models.ForeignKey(SupplierStatement, on_delete=models.CASCADE,
                                  related_name='lines')
    row_number = models.PositiveIntegerField(default=0)

    line_type = models.CharField(max_length=12, choices=StatementLineType.choices,
                                 default=StatementLineType.INVOICE)
    reference = models.CharField(max_length=100, blank=True, default='',
                                 help_text='Supplier invoice / document number.')
    doc_date  = models.DateField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default='')

    # Positive = a charge (invoice) the supplier says we owe. Payments and credit
    # notes are stored positive too, with the sign carried by line_type, so a
    # human reading a row is never surprised by a negative on an invoice.
    amount          = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    running_balance = models.DecimalField(max_digits=18, decimal_places=2,
                                          null=True, blank=True)

    raw = models.JSONField(default=dict, blank=True,
                           help_text='Original row as parsed, for audit.')

    class Meta(BaseModel.Meta):
        ordering = ['statement', 'row_number']
        indexes = [
            models.Index(fields=['statement', 'line_type'], name='stmtline_type_idx'),
            models.Index(fields=['reference'], name='stmtline_ref_idx'),
        ]

    def __str__(self):
        return f'{self.reference or self.description} ({self.amount})'


class StatementMatch(BaseModel):
    """The reconciliation result for one statement line and/or one Omni bill.

    * statement_line set, recon_item set  -> the two matched (possibly variance)
    * statement_line set, recon_item null -> statement-only / duplicate / payment
    * statement_line null, recon_item set -> Omni-only (bill the supplier omitted)
    """

    statement = models.ForeignKey(SupplierStatement, on_delete=models.CASCADE,
                                  related_name='matches')
    statement_line = models.ForeignKey(SupplierStatementLine, on_delete=models.CASCADE,
                                       null=True, blank=True, related_name='matches')
    recon_item = models.ForeignKey(InvoiceReconItem, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='statement_matches')

    match_type = models.CharField(max_length=18, choices=StmtMatchType.choices)
    # statement amount - Omni bill amount (BWP). Positive = supplier billed more.
    variance   = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    proposal        = models.CharField(max_length=12, choices=PaymentProposal.choices,
                                       default=PaymentProposal.INVESTIGATE)
    proposed_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    reason          = models.CharField(max_length=255, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['match_type', 'statement_line__row_number']
        indexes = [
            models.Index(fields=['statement', 'match_type'], name='stmtmatch_type_idx'),
            models.Index(fields=['statement', 'proposal'], name='stmtmatch_prop_idx'),
        ]

    def __str__(self):
        return f'{self.get_match_type_display()} → {self.get_proposal_display()}'
