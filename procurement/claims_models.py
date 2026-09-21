"""
procurement/claims_models.py

Claims PO — vehicle-assessment persistence (PHASE 2 of the claims-PO port).

CFO 2026-07-06 claims-PO port: this replaces the retired `po-invoice-ingest`
FastAPI app's SQLAlchemy `Invoice` model (assessment path only — the
single-invoice / Odoo fields were dropped along with Odoo itself).

A ClaimsAssessment is one uploaded vehicle-assessment PDF and everything the
Claims user layers on top of it before two draft POs are generated:

  1. UPLOADED          — the PDF is stored, nothing parsed yet
  2. PARSING           — Phase-3 parser is running
  3. READY_FOR_REVIEW  — report_json is populated; user allocates lines to
                         vendors, sets excess / markup / per-supplier VAT
  4. PARSE_FAILED      — parser could not read the assessment (parse_error set)
  5. POS_CREATED       — the Repairer PO + Parts PO drafts exist (po_results)
  6. FAILED            — PO creation itself failed

The money math lives in procurement/claims_engine.py (Phase 1, pure):
  - report_json feeds claims_split / build_allocation_plan
  - line_allocations / supplier_settings / excess_* / markup_pct are the
    user-entered inputs those functions consume
  - po_results records what came out the other end

Conventions (match PurchaseOrder in procurement/models.py):
  - UUID PK from BaseModel; AuditableMixin tracks create/update in AuditLog
  - Entity-scoped via a `company` FK to core.Company
  - Monetary DecimalFields max_digits=18, decimal_places=2
"""

from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


class ClaimsAssessment(AuditableMixin, BaseModel):
    """One uploaded vehicle assessment on its way to two draft POs
    (Repairer PO + Parts PO). CFO 2026-07-06 claims-PO port."""

    class Status(models.TextChoices):
        UPLOADED         = 'uploaded',         'Uploaded'
        PARSING          = 'parsing',          'Parsing'
        READY_FOR_REVIEW = 'ready_for_review', 'Ready for Review'
        PARSE_FAILED     = 'parse_failed',     'Parse Failed'
        POS_CREATED      = 'pos_created',      'POs Created'
        FAILED           = 'failed',           'Failed'

    # --- the uploaded assessment + parse pipeline state ------------------
    assessment_file = models.FileField(
                          upload_to='claims_assessments/%Y/%m/',
                          null=True, blank=True,
                          help_text='The uploaded vehicle-assessment PDF.',
                      )
    status          = models.CharField(
                          max_length=20, choices=Status.choices,
                          default=Status.UPLOADED,
                      )
    report_json     = models.JSONField(
                          default=dict, blank=True,
                          help_text='Parsed assessment (Phase 3 fills it). '
                                    'Consumed by claims_engine.claims_split / '
                                    'build_allocation_plan.',
                      )
    deepseek_notes  = models.JSONField(default=list, blank=True)
    parse_error     = models.TextField(blank=True, default='')

    # --- entity scope (matches PurchaseOrder.company) ---------------------
    company         = models.ForeignKey(
                          'core.Company', on_delete=models.PROTECT,
                          related_name='claims_assessments', null=True, blank=True,
                      )

    # --- claim metadata (from the assessment / typed by the Claims user) --
    claims_type       = models.CharField(max_length=64, null=True, blank=True)
    policy_number     = models.CharField(max_length=64, null=True, blank=True)
    claim_number      = models.CharField(max_length=64, null=True, blank=True)
    assessment_number = models.CharField(max_length=64, null=True, blank=True)
    claim_description = models.TextField(null=True, blank=True)
    ad_note           = models.TextField(null=True, blank=True)
    client_name       = models.CharField(max_length=255, null=True, blank=True)
    registration      = models.CharField(max_length=32, null=True, blank=True)
    vehicle           = models.CharField(max_length=255, null=True, blank=True)
    contact_details   = models.CharField(max_length=255, null=True, blank=True)

    # --- excess / markup controls (inputs to claims_engine) ---------------
    # Excess priority (Kao 2026-06-29): typed excess_amount wins over the
    # assessment's own figure, which wins over max(pct x base, min).
    excess_pct    = models.DecimalField(
                        max_digits=5, decimal_places=2, null=True, blank=True,
                        help_text='Percent, e.g. 5.00 = 5%.',
                    )
    excess_min    = models.DecimalField(
                        max_digits=18, decimal_places=2, null=True, blank=True,
                        help_text='Minimum-excess floor (matrix minimum).',
                    )
    excess_amount = models.DecimalField(
                        max_digits=18, decimal_places=2, null=True, blank=True,
                        help_text='Directly-typed excess — overrides pct/min '
                                  'and the assessment figure.',
                    )
    markup_pct    = models.DecimalField(
                        max_digits=5, decimal_places=2, null=True, blank=True,
                        help_text='Percent markup on supplier-sourced parts.',
                    )

    # --- user allocation choices + PO outcome -----------------------------
    line_allocations  = models.JSONField(
                            default=list, blank=True,
                            help_text='List of {"id", "vendor"} overrides — '
                                      'merged by claims_engine.apply_allocations.',
                        )
    supplier_settings = models.JSONField(
                            default=dict, blank=True,
                            help_text='{"<vendor label>": {"vat": bool, '
                                      '"currency": "BWP"|"ZAR"}}.',
                        )
    po_results        = models.JSONField(
                            default=list, blank=True,
                            help_text='Outcome of draft-PO creation.',
                        )

    created_by = models.ForeignKey(
                     User, null=True, blank=True,
                     on_delete=models.SET_NULL,
                     related_name='claims_assessments_created',
                 )

    # ── Progress + timing (CFO 2026-07-08: upload returns instantly, the
    #    parse+PO-generation runs in the background, and the screen shows a
    #    live progress bar + ETA). progress_stage drives the bar; the *_ms
    #    timings feed the self-learning ETA (learned_eta_ms below).
    class Stage(models.TextChoices):
        UPLOADED   = 'uploaded',   'Uploaded'
        PARSING    = 'parsing',    'Reading the PDF'
        GENERATING = 'generating', 'Creating purchase orders'
        DONE       = 'done',       'Done'
        FAILED     = 'failed',     'Failed'

    progress_stage = models.CharField(
                         max_length=12, choices=Stage.choices,
                         default=Stage.UPLOADED, db_index=True,
                     )
    progress_pct   = models.PositiveSmallIntegerField(default=0)
    parse_ms       = models.PositiveIntegerField(null=True, blank=True)
    generate_ms    = models.PositiveIntegerField(null=True, blank=True)
    total_ms       = models.PositiveIntegerField(null=True, blank=True)
    processing_started_at  = models.DateTimeField(null=True, blank=True)
    processing_finished_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Claims Assessment'
        verbose_name_plural = 'Claims Assessments'

    def __str__(self):
        label = self.claim_number or self.assessment_number or (
            self.assessment_file.name if self.assessment_file else str(self.pk)
        )
        return f"Claims assessment {label} ({self.get_status_display()})"

    @property
    def is_ready(self) -> bool:
        """True when parsing succeeded and the user can allocate / generate POs."""
        return self.status == self.Status.READY_FOR_REVIEW

    # ── Self-learning ETA ────────────────────────────────────────────────
    # The ETA shown to the next user is the average real duration of the last
    # N completed runs. It has no history on day one (falls back to a sane
    # default) and sharpens itself every time a run finishes — that is the
    # "learns to do this quickly" loop: it learns the true timing from its own
    # past runs and predicts the wait accurately instead of a frozen spinner.
    ETA_SAMPLE = 20
    ETA_DEFAULT_MS = 22000

    @classmethod
    def learned_eta_ms(cls, company_id=None) -> int:
        qs = cls.objects.filter(total_ms__isnull=False)
        if company_id:
            qs = qs.filter(company_id=company_id)
        vals = list(qs.order_by('-created_at')
                      .values_list('total_ms', flat=True)[:cls.ETA_SAMPLE])
        if not vals:
            return cls.ETA_DEFAULT_MS
        return int(sum(vals) / len(vals))

    @property
    def is_processing(self) -> bool:
        return self.progress_stage in (self.Stage.UPLOADED,
                                       self.Stage.PARSING, self.Stage.GENERATING)
