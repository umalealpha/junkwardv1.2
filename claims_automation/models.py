"""claims_automation — the Omni half of the end-to-end claims pipeline
(CFO plan 19-Sep-2026, "Claims Automation — End to End").

Graphite owns the claim and fires events; Omni owns Finance's rules, the
purchase orders, the letters and the AI reading. Three tables:

  ClaimCase             one row per Graphite claim reference — the latest facts
                        Graphite sent, what the AI read, and where it stands.
  ClaimAutomationEvent  every event Graphite pushed, idempotent on the sender's
                        own key, with what Omni did about it.
  ClaimLetter           an Agreement of Loss or a repudiation letter, drafted by
                        the system and NEVER sent until a person approves it.

Guardrails that live in the code, not in a note:
  * Nothing here declines a claim. A repudiation is only ever a DRAFT waiting
    for the Claims Manager; declining it lets the claim carry on.
  * Omni never moves money. A letter states a figure; payment still goes
    through the normal payment request and the banks.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


class ClaimCase(BaseModel):
    class Stage(models.TextChoices):
        REGISTERED = "registered", "Registered"
        FORM_IN = "form_in", "Customer form received"
        ASSESSED = "assessed", "Assessment received"
        PO_DRAFTED = "po_drafted", "Purchase orders drafted"
        AOL_PENDING = "aol_pending", "Agreement of Loss awaiting authorisation"
        REPUDIATION_PENDING = "repudiation_pending", "Repudiation awaiting approval"
        DECIDED = "decided", "Decision recorded"

    class Triage(models.TextChoices):
        UNKNOWN = "", "Not yet read"
        STRAIGHT_THROUGH = "straight_through", "Straight through"
        EXCEPTION = "exception", "Needs a person"

    claim_ref = models.CharField(
        max_length=64, unique=True, help_text="Graphite claim number, upper-cased."
    )
    graphite_id = models.BigIntegerField(null=True, blank=True)
    claim_type = models.CharField(max_length=64, blank=True, default="")
    is_motor = models.BooleanField(default=False)
    facts = models.JSONField(
        default=dict, blank=True, help_text="Latest facts Graphite sent (merged)."
    )
    # B1 — every clock in the Claims Life Cycle Tracker counts from the date
    # the claim was NOTIFIED, not the date the file was opened and not the date
    # the police report arrived. Blank means not measurable: the tracker shows
    # "unknown" rather than falling back to another date.
    notification_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Date the claim was notified to us. Every clock counts from here.",
    )
    handler_email = models.CharField(max_length=254, blank=True, default="")
    premium_light = models.CharField(max_length=10, blank=True, default="")
    stage = models.CharField(
        max_length=24, choices=Stage.choices, default=Stage.REGISTERED
    )

    # AI reading — WORDS only, advisory. Every flag is computed by rule.
    ai_summary = models.TextField(blank=True, default="")
    ai_next_step = models.TextField(blank=True, default="")
    ai_engine = models.CharField(max_length=40, blank=True, default="")
    ai_at = models.DateTimeField(null=True, blank=True)
    triage = models.CharField(
        max_length=20, choices=Triage.choices, blank=True, default=""
    )
    triage_reasons = models.JSONField(default=list, blank=True)
    flags = models.JSONField(
        default=list, blank=True, help_text="Deterministic policy-breach flags."
    )

    po_assessment = models.ForeignKey(
        "procurement.ClaimsAssessment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta(BaseModel.Meta):
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.claim_ref} [{self.stage}]"


class ClaimAutomationEvent(BaseModel):
    class Type(models.TextChoices):
        CLAIM_REGISTERED = "claim_registered", "Claim registered"
        FORM_SUBMITTED = "claim_form_submitted", "Customer form submitted"
        PREMIUM_CHECKED = "premium_checked", "Premium checked"
        ASSESSMENT_RECEIVED = "assessment_received", "Assessment received"
        WRITE_OFF_FLAGGED = "write_off_flagged", "Write-off flagged"
        DECISION_RECORDED = "decision_recorded", "Decision recorded"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PROCESSED = "processed", "Processed"
        FAILED = "failed", "Failed"

    case = models.ForeignKey(ClaimCase, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=32, choices=Type.choices)
    idempotency_key = models.CharField(max_length=128, unique=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.RECEIVED
    )
    actions = models.JSONField(
        default=list, blank=True, help_text="What Omni did, in plain words."
    )
    error = models.TextField(blank=True, default="")
    received_via = models.CharField(max_length=120, blank=True, default="")

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]


class ClaimLetter(BaseModel):
    class Kind(models.TextChoices):
        AOL = "aol", "Agreement of Loss"
        REPUDIATION = "repudiation", "Repudiation"

    class Status(models.TextChoices):
        AWAITING = "awaiting", "Awaiting authorisation"
        APPROVED = "approved", "Approved"
        SENT = "sent", "Approved and sent to the client"
        DECLINED = "declined", "Declined"

    case = models.ForeignKey(
        ClaimCase, on_delete=models.CASCADE, related_name="letters"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.AWAITING
    )
    figures = models.JSONField(default=dict, blank=True)
    reasons = models.JSONField(default=list, blank=True)
    context = models.JSONField(
        default=dict, blank=True, help_text="Everything the letter renders from."
    )
    decided_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")
    sent_to = models.CharField(max_length=254, blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]


class SalvageHandover(BaseModel):
    """Veritas confirming they hold the wreck, before an Agreement of Loss may be
    authorised (CFO 19-Sep-2026).

    The yard (Moses Ncube / Tshephang Motswagae at Motor Liquidators, company VCM)
    ticks off what they physically have. Until every item is answered, Omni blocks
    the Agreement of Loss — a claims manager may still override, in writing, and
    the override is recorded here and reported.

    Each item is 'yes', 'no' or 'na' (not applicable — e.g. a burnt shell has no
    spare keys); an 'na' needs a note, so an exception is always explained.
    """

    ITEMS = (
        ('vehicle_in_yard', 'Vehicle received in the yard'),
        ('blue_book', 'Registration book (blue book)'),
        ('spare_keys', 'Spare keys'),
        ('number_plates', 'Number plates'),
    )
    ANSWERS = ('yes', 'no', 'na')

    case = models.OneToOneField(ClaimCase, on_delete=models.CASCADE, related_name='handover')
    checklist = models.JSONField(default=dict, blank=True,
                                 help_text="{'vehicle_in_yard': 'yes'|'no'|'na', ...}")
    yard_reference = models.CharField(max_length=64, blank=True, default='',
                                      help_text="The yard's own item number, if they have one.")
    note = models.TextField(blank=True, default='')
    declared_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='+')
    declared_at = models.DateTimeField(null=True, blank=True)

    # A claims manager settling WITHOUT possession. Never silent.
    override_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='+')
    override_at = models.DateTimeField(null=True, blank=True)
    override_reason = models.TextField(blank=True, default='')

    @property
    def complete(self) -> bool:
        """Every item answered yes or na, and any na explained."""
        answers = self.checklist or {}
        if any(answers.get(k) not in ('yes', 'na') for k, _ in self.ITEMS):
            return False
        if any(v == 'na' for v in answers.values()) and not (self.note or '').strip():
            return False
        return True

    @property
    def overridden(self) -> bool:
        return bool(self.override_at and (self.override_reason or '').strip())

    @property
    def missing(self) -> list:
        answers = self.checklist or {}
        return [label for key, label in self.ITEMS if answers.get(key) not in ('yes', 'na')]

    def __str__(self):
        return f'{self.case.claim_ref} handover'


class VeritasSalvageCharge(BaseModel):
    """What Veritas owes us for the wreck: 20% of what we actually paid the client
    (CFO 19-Sep-2026 — this REPLACES the 20%-of-sum-insured basis written in the
    earlier CR-006 spec; Kago to be told).

    Raised only when BOTH are true: the Agreement of Loss is authorised, and
    Veritas has confirmed possession (or a manager has overridden it in writing).
    The invoice is created as a DRAFT for Finance to check and post — Omni never
    posts it by itself, and never moves money.
    """

    RATE = Decimal('0.20')

    class Status(models.TextChoices):
        PENDING_CONFIG = 'pending_config', 'Waiting for the income account'
        DRAFTED        = 'drafted',        'Draft invoice raised for Finance'
        CANCELLED      = 'cancelled',      'Cancelled'

    case        = models.ForeignKey(ClaimCase, on_delete=models.CASCADE, related_name='veritas_charges')
    letter      = models.OneToOneField(ClaimLetter, on_delete=models.CASCADE, related_name='veritas_charge')
    settlement  = models.DecimalField(max_digits=18, decimal_places=2,
                                      help_text='What we paid the client (the Agreement of Loss net).')
    rate        = models.DecimalField(max_digits=5, decimal_places=4, default=RATE)
    amount      = models.DecimalField(max_digits=18, decimal_places=2)
    status      = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING_CONFIG)
    invoice     = models.ForeignKey('billing.Invoice', null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    note        = models.TextField(blank=True, default='')

    def __str__(self):
        return f'{self.case.claim_ref} Veritas {self.amount}'
