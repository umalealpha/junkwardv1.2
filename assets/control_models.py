"""
assets/control_models.py

Asset Control & Handover — the internal-control lifecycle layered on top of the
fixed-asset register (assets.Asset).

Build spec: "Alpha Direct - Asset Control Module - Build Specification" (CFO,
2 Sep 2026). Closes the segregation-of-duties gap where IT requested, approved
and handed out assets in one hand, with no Finance sight and no signed trail.

Models
------
  - AssetControlPolicy   One config row: the value threshold above which a
                         requisition needs the FULL CFO + Finance-Manager gate
                         (§11 tiering; recommended P 1,500). Below it, a single
                         approver signs off — everything is still logged.

  - AssetRequisition     A request to issue an asset to a named staff member.
                         New purchase OR reissue of a returned spare. Carries the
                         sequential FM -> CFO approval gate (mirrors the
                         procurement PurchaseOrder flow) with segregation of
                         duties. The recipient is a payroll.Employee FK — it can
                         only be PICKED from the directory, never typed. [Rule 2]

  - AssetHandover        The signed hand-over note tied to an APPROVED
                         requisition. Three signatures: IT releases, Finance
                         records, employee accepts (e-sign). Only when all three
                         are present does the asset become 'In use'. No note, the
                         asset does not leave. Generates a storable PDF.

Reuse notes (do NOT rebuild)
  - Approver eligibility resolves through core.models.get_user_profile ->
    UserProfile.title, exactly like the procurement + journal-entry gates.
  - Custody history is the existing append-only assets.AssetAssignment.
  - The approval path has NO AI dependency and must keep working if the local
    AI box is down (spec §10).

Conventions follow the rest of assets/:
  - UUID PKs + AuditableMixin from core.models
  - All money: DecimalField(max_digits=18, decimal_places=2)
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel, Company


ZERO = Decimal('0.00')

# §11 threshold. Requisitions for assets valued at or above this need the full
# CFO + Finance-Manager gate; below it a single approver signs off. CFO
# 2026-09-02: the target is laptop + vehicle movements — assets above P5,000 —
# so the line sits at 5,000 (a laptop/vehicle is material; a mouse is not).
# Overridable per the singleton AssetControlPolicy row.
DEFAULT_MATERIAL_THRESHOLD_BWP = Decimal('5000.00')

# §11 — the named IT Asset Officers who may raise a requisition and sign the
# IT-release (CFO 2026-09-02). A narrow allow-list, editable on the policy row.
# Mirrors the vehicle-register fleet-admin pattern (nexus.VEHICLE_FLEET_ADMIN).
def default_it_officer_emails():
    return ['kmolefe@alphadirect.co.bw', 'isechele@alphadirect.co.bw']


class AssetControlPolicy(BaseModel):
    """Singleton-style config for the asset-control gate. Use current()."""

    material_threshold_bwp = models.DecimalField(
        max_digits=18, decimal_places=2, default=Decimal('5000.00'),
        help_text='Estimated value at or above which a requisition needs the '
                  'full CFO + Finance-Manager approval. Below it, a single '
                  'approver signs off. Everything is still registered and logged.',
    )
    it_officer_emails = models.JSONField(
        default=default_it_officer_emails,
        help_text='Named IT Asset Officers (by email) who may raise a '
                  'requisition and sign the IT-release. Superusers / omni '
                  'administrators are always included.',
    )
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name = 'Asset Control Policy'
        verbose_name_plural = 'Asset Control Policy'

    def __str__(self):
        return f'Asset control policy — material ≥ P {self.material_threshold_bwp}'

    @classmethod
    def current(cls):
        """Return the live policy row, creating the default on first use."""
        policy = cls.objects.filter(is_active=True).order_by('created_at').first()
        if policy is None:
            policy = cls.objects.create()
        return policy


class AssetRequisition(AuditableMixin, BaseModel):
    """A controlled request to issue an asset to a named staff member.

    Lifecycle (spec §4, steps 1-3 and 7):
        DRAFT
          -> submit ->            PENDING_FM_APPROVAL
          -> fm_approve ->        PENDING_CFO_APPROVAL  (material)
                              OR  APPROVED               (below threshold)
          -> cfo_approve ->       APPROVED
          -> (handover signed) -> FULFILLED
          -> reject ->            REJECTED   (either leg, with a reason)
          -> cancel ->            CANCELLED  (requester/approver, before handover)
    """

    class Type(models.TextChoices):
        NEW_PURCHASE = 'new_purchase', 'New purchase'
        REISSUE      = 'reissue',      'Reissue of a spare'

    class Status(models.TextChoices):
        DRAFT                = 'draft',                'Draft'
        PENDING_FM_APPROVAL  = 'pending_fm_approval',  'Awaiting Finance Manager'
        PENDING_CFO_APPROVAL = 'pending_cfo_approval', 'Awaiting CFO'
        APPROVED             = 'approved',             'Approved — ready to hand over'
        FULFILLED            = 'fulfilled',            'Fulfilled (handed over)'
        REJECTED             = 'rejected',             'Rejected'
        CANCELLED            = 'cancelled',            'Cancelled'

    requisition_number = models.CharField(max_length=30, unique=True)

    req_type   = models.CharField(max_length=20, choices=Type.choices)
    company    = models.ForeignKey(
                     Company, on_delete=models.PROTECT,
                     related_name='asset_requisitions',
                 )

    # What is being requested.
    category   = models.ForeignKey(
                     'assets.AssetCategory', on_delete=models.PROTECT,
                     related_name='requisitions',
                     help_text='Type of asset requested.',
                 )
    description = models.CharField(
                     max_length=300,
                     help_text='Make / model / spec of the asset requested.',
                 )
    estimated_value = models.DecimalField(
                     max_digits=18, decimal_places=2, default=ZERO,
                     help_text='Best estimate of value — drives the approval tier.',
                 )

    # For a REISSUE, the specific spare being requested out of the pool. It must
    # be a returned spare; issuing it still needs a fresh approved requisition.
    spare_asset = models.ForeignKey(
                     'assets.Asset', null=True, blank=True,
                     on_delete=models.PROTECT,
                     related_name='reissue_requisitions',
                     help_text='The returned/spare asset being reissued. '
                               'Set only when req_type = reissue. [Rule 1]',
                 )

    # Recipient — MUST be selected from the staff directory (a real, active
    # Employee). A free-typed name is impossible: this is a foreign key. [Rule 2]
    recipient        = models.ForeignKey(
                           'payroll.Employee', on_delete=models.PROTECT,
                           related_name='asset_requisitions_received',
                           help_text='Staff member the asset is for. Picked from '
                                     'the directory, never typed. [Rule 2]',
                       )
    # Read-only snapshots so the record stays readable even if the staff record
    # later changes or the person leaves.
    recipient_name   = models.CharField(max_length=200, blank=True, default='')
    recipient_email  = models.EmailField(blank=True, default='')

    reason           = models.CharField(
                           max_length=500,
                           help_text='Why the asset is needed.',
                       )

    status           = models.CharField(
                           max_length=25, choices=Status.choices,
                           default=Status.DRAFT,
                       )
    requires_full_gate = models.BooleanField(
                           default=True,
                           help_text='True when value ≥ the material threshold: '
                                     'needs both FM and CFO. False: single sign-off.',
                       )

    # Origination.
    requested_by     = models.ForeignKey(
                           User, on_delete=models.PROTECT,
                           related_name='asset_requisitions_raised',
                       )
    submitted_by     = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='asset_requisitions_submitted',
                       )
    submitted_at     = models.DateTimeField(null=True, blank=True)

    # Sequential approval legs (segregation of duties enforced in services).
    fm_approved_by   = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='asset_requisitions_fm_approved',
                       )
    fm_approved_at   = models.DateTimeField(null=True, blank=True)
    fm_comment       = models.CharField(max_length=500, blank=True, default='')

    cfo_approved_by  = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='asset_requisitions_cfo_approved',
                       )
    cfo_approved_at  = models.DateTimeField(null=True, blank=True)
    cfo_comment      = models.CharField(max_length=500, blank=True, default='')

    rejected_by      = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='asset_requisitions_rejected',
                       )
    rejected_at      = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=500, blank=True, default='')

    # Outcome — the asset that ends up issued (new buy: registered later;
    # reissue: the spare) and the handover note that moved it.
    resulting_asset  = models.ForeignKey(
                           'assets.Asset', null=True, blank=True,
                           on_delete=models.SET_NULL,
                           related_name='issuing_requisitions',
                       )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Asset Requisition'
        verbose_name_plural = 'Asset Requisitions'
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['req_type', 'status']),
            models.Index(fields=['recipient']),
        ]

    def __str__(self):
        return f'{self.requisition_number} — {self.get_req_type_display()} for {self.recipient_name}'

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED

    @property
    def is_open(self):
        return self.status in (
            self.Status.DRAFT,
            self.Status.PENDING_FM_APPROVAL,
            self.Status.PENDING_CFO_APPROVAL,
        )


class AssetHandover(AuditableMixin, BaseModel):
    """The three-signature hand-over note (spec §4 step 4, §6.3).

    Created only from an APPROVED requisition. Not until all three signatures
    are present does the asset become 'In use'. Signatures, in order:
        1. IT releases       (it_released_by / at)
        2. Finance records   (finance_recorded_by / at)
        3. Employee accepts  (employee_accepted_by / at)  — the recipient e-signs
    """

    class Status(models.TextChoices):
        PENDING          = 'pending',          'Awaiting IT release'
        IT_RELEASED      = 'it_released',       'Released by IT — awaiting Finance'
        FINANCE_RECORDED = 'finance_recorded',  'Recorded by Finance — awaiting employee'
        ACCEPTED         = 'accepted',          'Accepted — asset in use'
        CANCELLED        = 'cancelled',         'Cancelled'

    handover_number = models.CharField(max_length=30, unique=True)

    requisition = models.OneToOneField(
                      AssetRequisition, on_delete=models.PROTECT,
                      related_name='handover',
                  )
    asset       = models.ForeignKey(
                      'assets.Asset', on_delete=models.PROTECT,
                      related_name='handovers',
                  )
    recipient   = models.ForeignKey(
                      'payroll.Employee', on_delete=models.PROTECT,
                      related_name='asset_handovers_received',
                  )
    recipient_name  = models.CharField(max_length=200, blank=True, default='')
    recipient_email = models.EmailField(blank=True, default='')

    status = models.CharField(
                 max_length=20, choices=Status.choices, default=Status.PENDING,
             )

    # Condition / accessories at issue (spec §6.3).
    condition_on_issue = models.CharField(max_length=300, blank=True, default='')
    accessories        = models.CharField(
                             max_length=300, blank=True, default='',
                             help_text='Charger, bag, mouse, etc. included.',
                         )

    # --- The three signatures. e-signature PNG data-URLs are optional; the
    #     authoritative signature is the signed-in user + timestamp. ---
    it_released_by   = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='asset_handovers_released',
                       )
    it_released_at   = models.DateTimeField(null=True, blank=True)
    it_signature     = models.TextField(blank=True, default='')

    finance_recorded_by = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='asset_handovers_recorded',
                       )
    finance_recorded_at = models.DateTimeField(null=True, blank=True)
    finance_signature   = models.TextField(blank=True, default='')

    employee_accepted_by = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='asset_handovers_accepted',
                       )
    employee_accepted_at = models.DateTimeField(null=True, blank=True)
    employee_signature   = models.TextField(blank=True, default='')

    notes = models.CharField(max_length=500, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Asset Handover Note'
        verbose_name_plural = 'Asset Handover Notes'
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.handover_number} — {self.asset_id} to {self.recipient_name} ({self.status})'

    @property
    def is_complete(self):
        return (
            self.it_released_by_id is not None
            and self.finance_recorded_by_id is not None
            and self.employee_accepted_by_id is not None
        )
