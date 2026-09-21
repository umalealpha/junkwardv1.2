"""
nbfira/models.py — NBFIRA quarterly + annual return models.

CFO directive 2026-05-22 (NBFIRA_MODULE_BLUEPRINT.md).

Phase 2 ships the Quarterly Return only — five schedules (IS, A, A.1,
B, C). Phase 3 will reuse NBFIRAReturn + NBFIRAReturnLine for the
Annual Return without schema change.

Models:
  NBFIRAReturn         header (type, period, status, locked_at, …)
  NBFIRAReturnLine     one cell from one schedule (code + label + value
                        + source_accounts JSON)
  NBFIRACapitalFactor  configurable IRC / MRC / g-factor / MCR
                        (blueprint §A.1 mandate — never hardcoded)
  NBFIRASubmission     post-submit metadata (file hash, filing ref,
                        acknowledgement ref)
  NBFIRAAuditLog       immutable trail of every action on a return

Workflow states (Phase 4 wires the transitions):
  draft → reviewed → approved → locked → submitted
  rejected, reopened as side states.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import AuditableMixin, BaseModel, Company


ZERO = Decimal('0.00')
User = settings.AUTH_USER_MODEL


# ─────────────────────────────────────────────────────────────────────────
# Return header
# ─────────────────────────────────────────────────────────────────────────
class NBFIRAReturn(AuditableMixin, BaseModel):

    class Type(models.TextChoices):
        QUARTERLY = 'quarterly', 'Quarterly (Short-term Insurer)'
        ANNUAL    = 'annual',    'Annual (IMF General Insurance)'

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        REVIEWED  = 'reviewed',  'Reviewed'
        APPROVED  = 'approved',  'Approved'
        LOCKED    = 'locked',    'Locked (immutable)'
        SUBMITTED = 'submitted', 'Submitted to NBFIRA'
        REJECTED  = 'rejected',  'Rejected'
        REOPENED  = 'reopened',  'Reopened'

    type    = models.CharField(max_length=12, choices=Type.choices,
                               default=Type.QUARTERLY)
    company = models.ForeignKey(
        Company, on_delete=models.PROTECT,
        related_name='nbfira_returns', null=True, blank=True,
    )
    # Period labels — for quarterly: '2026Q3' (calendar Q3 = Jan-Mar of FY ending Jun);
    # for annual: 'FY2026'.
    period_label = models.CharField(
        max_length=20,
        help_text='e.g. 2026Q3, FY2026.',
    )
    period_start = models.DateField()
    period_end   = models.DateField()

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.DRAFT)

    initiated_by   = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_returns_initiated',
    )
    initiated_at   = models.DateTimeField(auto_now_add=True)
    reviewed_by    = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_returns_reviewed',
    )
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    approved_by    = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_returns_approved',
    )
    approved_at    = models.DateTimeField(null=True, blank=True)
    locked_by      = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_returns_locked',
    )
    locked_at      = models.DateTimeField(null=True, blank=True)
    submitted_by   = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_returns_submitted',
    )
    submitted_at   = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-period_end']
        unique_together = [('type', 'period_label', 'company')]
        verbose_name = 'NBFIRA Return'
        verbose_name_plural = 'NBFIRA Returns'

    def __str__(self):
        return f'{self.type} {self.period_label} ({self.status})'


# ─────────────────────────────────────────────────────────────────────────
# Return line — one row in one schedule
# ─────────────────────────────────────────────────────────────────────────
class NBFIRAReturnLine(BaseModel):

    return_obj = models.ForeignKey(
        NBFIRAReturn, on_delete=models.CASCADE, related_name='lines',
    )
    schedule  = models.CharField(
        max_length=8,
        help_text='IS, A, A.1, B, C, AFS, IMF_ASSETS, IMF_LIAB, …',
    )
    section   = models.CharField(
        max_length=40, blank=True, default='',
        help_text='Sub-section inside a schedule for grouping the UI.',
    )
    line_code = models.CharField(max_length=40)
    label     = models.CharField(max_length=200)
    value     = models.DecimalField(max_digits=20, decimal_places=4, default=ZERO)
    source_accounts = models.JSONField(default=list, blank=True,
        help_text='List of GL account codes that contributed to this value.')
    formula   = models.CharField(max_length=200, blank=True, default='',
        help_text='Human formula description for source-trace tooltip.')
    sort_order = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['return_obj', 'schedule', 'sort_order', 'line_code']
        indexes = [models.Index(fields=['return_obj', 'schedule'])]
        unique_together = [('return_obj', 'schedule', 'line_code')]
        verbose_name = 'NBFIRA Return Line'
        verbose_name_plural = 'NBFIRA Return Lines'

    def __str__(self):
        return f'{self.return_obj_id} {self.schedule}:{self.line_code} = {self.value}'


# ─────────────────────────────────────────────────────────────────────────
# Capital factors — blueprint §A.1 mandate: never hardcoded
# ─────────────────────────────────────────────────────────────────────────
class NBFIRACapitalFactor(AuditableMixin, BaseModel):

    class Kind(models.TextChoices):
        IRC = 'irc', 'Insurance Risk Capital factor'
        MRC = 'mrc', 'Market Risk Capital factor'
        G   = 'g',   'g-factor (Insurance / Market)'
        MCR = 'mcr', 'Minimum Capital Requirement (BWP)'

    kind          = models.CharField(max_length=4, choices=Kind.choices)
    key           = models.CharField(
        max_length=60,
        help_text='IRC: class name (motor, accident, …). '
                  'MRC: asset bucket (cash, fixed_interest_1yr, …). '
                  'G:   "insurance" or "market". '
                  'MCR: "default".',
    )
    factor_value  = models.DecimalField(max_digits=14, decimal_places=4)
    effective_from = models.DateField()
    effective_to   = models.DateField(null=True, blank=True)
    notes          = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['kind', 'key', '-effective_from']
        unique_together = [('kind', 'key', 'effective_from')]
        verbose_name = 'NBFIRA Capital Factor'
        verbose_name_plural = 'NBFIRA Capital Factors'

    def __str__(self):
        return f'{self.kind}/{self.key}={self.factor_value} (from {self.effective_from})'


# ─────────────────────────────────────────────────────────────────────────
# Submission (after locked + sent to NBFIRA)
# ─────────────────────────────────────────────────────────────────────────
class NBFIRASubmission(BaseModel):

    return_obj         = models.OneToOneField(
        NBFIRAReturn, on_delete=models.PROTECT, related_name='submission',
    )
    submitted_by       = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_submissions',
    )
    submitted_at       = models.DateTimeField(auto_now_add=True)
    file_name          = models.CharField(max_length=200, blank=True, default='')
    file_hash_sha256   = models.CharField(max_length=64, blank=True, default='')
    filing_reference   = models.CharField(max_length=80, blank=True, default='',
        help_text='Reference returned by the NBFIRA RBSS portal on accept.')
    acknowledgement_ref = models.CharField(max_length=80, blank=True, default='')
    proof_of_submission = models.FileField(
        upload_to='nbfira/proofs/', null=True, blank=True,
    )

    class Meta(BaseModel.Meta):
        ordering = ['-submitted_at']
        verbose_name = 'NBFIRA Submission'
        verbose_name_plural = 'NBFIRA Submissions'

    def __str__(self):
        return f'{self.return_obj.period_label} → {self.filing_reference or "-"}'


# ─────────────────────────────────────────────────────────────────────────
# Audit log — immutable
# ─────────────────────────────────────────────────────────────────────────
class NBFIRAAuditLog(BaseModel):

    class Action(models.TextChoices):
        CREATE     = 'create',    'Create draft'
        GENERATE   = 'generate',  'Generate schedules from GL'
        EDIT       = 'edit',      'Edit a line value'
        REVIEW     = 'review',    'Mark as reviewed'
        APPROVE    = 'approve',   'Approve'
        REJECT     = 'reject',    'Reject'
        REOPEN     = 'reopen',    'Reopen'
        LOCK       = 'lock',      'Lock'
        UNLOCK     = 'unlock',    'Unlock (privileged)'
        EXPORT     = 'export',    'Export XLSX/PDF'
        SUBMIT     = 'submit',    'Mark as submitted'
        UPLOAD     = 'upload',    'Attach filed return'
        UNATTACH   = 'unattach',  'Remove filed return'

    return_obj = models.ForeignKey(
        NBFIRAReturn, on_delete=models.PROTECT, related_name='audit_logs',
    )
    user       = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_audit_logs',
    )
    action     = models.CharField(max_length=12, choices=Action.choices)
    comment    = models.TextField(blank=True, default='')
    before_json = models.JSONField(default=dict, blank=True)
    after_json  = models.JSONField(default=dict, blank=True)
    ip_address = models.CharField(max_length=45, blank=True, default='')
    timestamp  = models.DateTimeField(auto_now_add=True)
    file_hash  = models.CharField(max_length=64, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-timestamp']
        indexes  = [models.Index(fields=['return_obj', 'timestamp'])]
        verbose_name = 'NBFIRA Audit Log'
        verbose_name_plural = 'NBFIRA Audit Log'

    def save(self, *args, **kwargs):
        # Audit log is append-only — never updated. UUID default fills
        # self.pk before save(), so check _state.adding instead.
        if not self._state.adding:
            raise ValueError('NBFIRAAuditLog rows are immutable.')
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.action} @ {self.timestamp:%Y-%m-%d %H:%M:%S}'


# ─────────────────────────────────────────────────────────────────────────
# Filed return documents — the ACTUAL workbook submitted to NBFIRA
# ─────────────────────────────────────────────────────────────────────────
class NBFIRAFiledDocument(AuditableMixin, BaseModel):
    """A real NBFIRA-format file attached to a return period (CFO 2026-08-17).

    Omni GENERATES its schedules from the GL. This holds the return that was
    actually FILED, so the two can be reconciled: Omni's own figure against the
    figure on the submitted document, per period.

    Deliberately evidence-only for now — the file is stored, hashed and audited,
    but NOT parsed. Parsing is a separate step that needs the real workbook
    layout in hand; guessing at a regulator's cell layout would silently produce
    wrong comparisons, which is worse than no comparison. `parsed` /
    `parse_note` are the hooks for when that lands.

    Distinct from NBFIRASubmission, which records proof AFTER filing (filing
    reference, acknowledgement). This is the return content itself.
    """

    return_obj = models.ForeignKey(
        NBFIRAReturn, on_delete=models.CASCADE, related_name='filed_documents',
    )
    file          = models.FileField(upload_to='nbfira/filed/%Y/%m/')
    original_name = models.CharField(
        max_length=255,
        help_text='Filename as uploaded — the stored name is slugified.')
    statement     = models.CharField(
        max_length=8, blank=True, default='',
        help_text="Which statement this file is, if it is a single one: "
                  "IS, A, A.1, B, C. Blank = a full workbook covering several.")
    size_bytes    = models.PositiveBigIntegerField(default=0)
    content_type  = models.CharField(max_length=100, blank=True, default='')
    file_hash_sha256 = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='Integrity + duplicate detection.')
    notes         = models.TextField(blank=True, default='')

    uploaded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_filed_documents',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # Hooks for the reconciliation step (not populated yet — see docstring).
    parsed     = models.BooleanField(default=False)
    parse_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-uploaded_at']
        indexes  = [models.Index(fields=['return_obj', 'statement'])]
        verbose_name = 'NBFIRA Filed Document'
        verbose_name_plural = 'NBFIRA Filed Documents'

    def __str__(self):
        tag = f' [{self.statement}]' if self.statement else ''
        return f'{self.return_obj.period_label}{tag} — {self.original_name}'


# ─────────────────────────────────────────────────────────────────────────
# Statement A.1 inputs — the figures the general ledger cannot supply
# ─────────────────────────────────────────────────────────────────────────
class NBFIRAA1Input(AuditableMixin, BaseModel):
    """The three A.1 inputs that are ENTERED, not derived (CFO 2026-08-18).

    Omni computes A.1 exactly as the filed NBFIRA workbook does (see
    nbfira/a1_math.py). Three of its inputs cannot come from the ledger:

      * `anwp` — ASSUMED annual net written premium per class for the NEXT
        twelve months. Forward-looking. The old code reached for historical GWP
        by class, which is the wrong figure however well it is mapped.
      * `mer_total` — maximum event retention, off the reinsurance treaty.
      * `net_assets` / `alloc_mrctr` — the investment classification per asset
        bucket, and how much of each is allocated to market-risk cover.

    Because these drive a regulatory figure they carry provenance: who entered
    them, when, the source they came from and the date they take effect —
    exactly what Regulation 4's forward-looking basis needs to be auditable.
    A figure with no stated source is not evidence.

    One row per return. Absent, A.1 falls back to the statutory MCR floor.
    """

    return_obj = models.OneToOneField(
        NBFIRAReturn, on_delete=models.CASCADE, related_name='a1_input',
    )
    # Per-class and per-bucket maps. JSON so a change to the class or bucket
    # list is not a migration; the builder only reads keys it knows.
    anwp        = models.JSONField(default=dict, blank=True,
        help_text="Assumed annual NWP for the next 12 months, per class, P'000.")
    mer_total   = models.DecimalField(max_digits=20, decimal_places=4, default=ZERO,
        help_text="Maximum event retention, P'000.")
    net_assets  = models.JSONField(default=dict, blank=True,
        help_text="Net total assets per asset bucket, P'000.")
    alloc_mrctr = models.JSONField(default=dict, blank=True,
        help_text="Assets allocated to market-risk cover per bucket, P'000.")

    source_note    = models.TextField(
        blank=True, default='',
        help_text='Where these came from — e.g. "as per filed Q4 workbook, '
                  'treaty schedule B". Required by the UI.')
    effective_from = models.DateField(
        null=True, blank=True,
        help_text='Date the assumption takes effect.')
    entered_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='nbfira_a1_inputs',
    )
    entered_at = models.DateTimeField(auto_now=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'NBFIRA A.1 Input'
        verbose_name_plural = 'NBFIRA A.1 Inputs'

    def __str__(self):
        return f'A.1 inputs for {self.return_obj.period_label}'

    def as_builder_inputs(self) -> dict:
        """Shape a1_math/build_schedule_a1 expects. Decimal, never float —
        these figures end up in a regulatory return."""
        def dec_map(raw):
            out = {}
            for k, v in (raw or {}).items():
                try:
                    out[k] = Decimal(str(v))
                except Exception:      # noqa: BLE001 — a bad key is not fatal
                    continue
            return out
        return {
            'anwp':        dec_map(self.anwp),
            'mer_total':   Decimal(self.mer_total or 0),
            'net_assets':  dec_map(self.net_assets),
            'alloc_mrctr': dec_map(self.alloc_mrctr),
        }
