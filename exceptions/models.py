"""
exceptions/models.py

Exception engine — a queue of operational anomalies that need a human eye.

The system writes to this queue from anywhere it spots a problem. Examples:
  - PO ↔ Bill match has a variance over the auto-pass threshold
  - Vendor bank account has been added or changed (must be FM-cleared)
  - DeepSeek AI flagged a bill as anomaly / fraud_cue
  - A payment was attempted against an unmatched bill
  - A user manually flags a transaction for review

Resolution is gated by `requires_role` — for example, banking-detail
exceptions can only be cleared by the CFO or Finance Manager, while a
PO/bill variance can be resolved by the dept manager.

Signals from `exceptions.signals` auto-create exceptions when the relevant
models in procurement / billing / payments save. Manual creation is also
supported via /api/v1/exceptions/.
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


class Exception(BaseModel):
    """One row per operational anomaly. The queue is the entire table.

    `Exception` is a Python builtin, but Django doesn't care — only our
    application code references it. We resolve the conflict in code by
    importing as `Exception as ExceptionModel` where needed.
    """

    class Type(models.TextChoices):
        PO_BILL_MISMATCH    = 'po_bill_mismatch',    'PO ↔ Bill mismatch (variance)'
        BANKING_CHANGE      = 'banking_change',      'Vendor banking detail change'
        AI_FRAUD_CUE        = 'ai_fraud_cue',        'AI fraud cue'
        AI_ANOMALY          = 'ai_anomaly',          'AI anomaly'
        UNMATCHED_PAYMENT   = 'unmatched_payment',   'Payment against unmatched bill'
        BACK_DATED_BILL     = 'back_dated_bill',     'Bill dated before PO approval'
        VENDOR_MISMATCH     = 'vendor_mismatch',     'Bill supplier ≠ PO supplier'
        BILL_NO_PO          = 'bill_no_po',          'Vendor bill without PO reference'
        OPEN_PO_AT_CLOSE    = 'open_po_at_close',    'Open PO blocking period close'
        FX_RATE_STALE       = 'fx_rate_stale',       'FX rate older than threshold'
        MANUAL              = 'manual',              'Manual flag'
        OTHER               = 'other',               'Other'

    class Severity(models.TextChoices):
        LOW      = 'low',      'Low'
        MEDIUM   = 'medium',   'Medium'
        HIGH     = 'high',     'High'
        CRITICAL = 'critical', 'Critical'

    class Status(models.TextChoices):
        OPEN         = 'open',         'Open'
        ACKNOWLEDGED = 'acknowledged', 'Acknowledged'
        IN_PROGRESS  = 'in_progress',  'In progress'
        RESOLVED     = 'resolved',     'Resolved'
        DISMISSED    = 'dismissed',    'Dismissed'

    class RequiresRole(models.TextChoices):
        ANY              = 'any',              'Anyone in finance'
        DEPT_MANAGER     = 'dept_manager',     'Department Manager'
        FINANCE_MANAGER  = 'finance_manager',  'Finance Manager (or CFO)'
        CFO              = 'cfo',              'CFO only'

    exception_type   = models.CharField(max_length=24, choices=Type.choices,
                                        default=Type.OTHER)
    severity         = models.CharField(max_length=8, choices=Severity.choices,
                                        default=Severity.MEDIUM)
    title            = models.CharField(max_length=200)
    description      = models.TextField(blank=True, default='')

    # What triggered this exception (free-form pointer to any model)
    source_app       = models.CharField(max_length=50, blank=True, default='')
    source_model     = models.CharField(max_length=50, blank=True, default='')
    source_id        = models.CharField(max_length=100, blank=True, default='')
    source_label     = models.CharField(max_length=200, blank=True, default='',
                                        help_text='e.g. "BILL-2026-000123" or '
                                                  '"VendorBankAccount #abc-123".')

    # Authorisation
    requires_role    = models.CharField(max_length=20, choices=RequiresRole.choices,
                                        default=RequiresRole.ANY)

    # Workflow
    status           = models.CharField(max_length=15, choices=Status.choices,
                                        default=Status.OPEN)
    acknowledged_by  = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='exceptions_acknowledged',
                      )
    acknowledged_at  = models.DateTimeField(null=True, blank=True)
    resolved_by      = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='exceptions_resolved',
                      )
    resolved_at      = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(blank=True, default='')
    dismissed_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='exceptions_dismissed',
                      )
    dismissed_at     = models.DateTimeField(null=True, blank=True)
    dismissal_reason = models.TextField(blank=True, default='')

    # Linker — third-party notification webhook (Slack / Telegram / WhatsApp /
    # whatever the CFO points it at). Records the dispatch attempt for replay.
    linker_notified_at = models.DateTimeField(null=True, blank=True)
    linker_response    = models.CharField(max_length=200, blank=True, default='')

    created_by       = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='exceptions_created',
                       )

    # Free-form metadata bag for hooks to stash extra fields (e.g. variance %,
    # AI flags list, BWP amount). Surfaced in the UI as a properties grid.
    metadata         = models.JSONField(default=dict, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Exception'
        verbose_name_plural = 'Exceptions'
        ordering            = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['exception_type']),
            models.Index(fields=['source_app', 'source_model', 'source_id']),
        ]

    def __str__(self):
        return f"[{self.severity.upper()}] {self.title}"

    @property
    def is_open(self) -> bool:
        return self.status in (
            self.Status.OPEN, self.Status.ACKNOWLEDGED, self.Status.IN_PROGRESS,
        )
