"""
commissions/models.py — monthly agent-commission submission module.

One online form for agents to submit their monthly commission (per-policy
lines) instead of emailing Excel workbooks. Three flows — independent agents
(10% withholding), in-house/payroll agents (no withholding), and the BDU sales
team — are modelled as configuration rows (CommissionGroup), not code branches.

Standalone by design (CFO boundary, mirrors agent_portal): this module
computes + exports a payout file. GL posting, the payment run and sign-off
stay manual finance controls — no ledger/payments coupling here.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models

from core.models import BaseModel

# Month key must be exactly YYYY-MM (01–12) so the unique constraint and the
# payout export never split on a typo like '2026-6'.
PERIOD_VALIDATOR = RegexValidator(r'^\d{4}-(0[1-9]|1[0-2])$',
                                  'Period must be YYYY-MM, e.g. 2026-06.')


class CommissionGroup(BaseModel):
    """One of the three commission flows. Withholding rate + pay route are
    configuration so a policy change (e.g. how BDU is paid) is one field, not
    a code change."""

    class Key(models.TextChoices):
        INDEPENDENT = 'independent', 'Independent agents'
        IN_HOUSE    = 'in_house',    'In-house / payroll agents'
        BDU         = 'bdu',         'BDU domestic sales team'

    class PaysVia(models.TextChoices):
        DIRECT_BANK = 'direct_bank', 'Direct bank payment'
        PAYROLL     = 'payroll',     'Through payroll'

    key = models.CharField(max_length=20, choices=Key.choices, unique=True)
    name = models.CharField(max_length=80)
    # e.g. 0.1000 == 10% withholding tax deducted before payment.
    withholding_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.0000'))
    pays_via = models.CharField(max_length=12, choices=PaysVia.choices, default=PaysVia.DIRECT_BANK)
    owner_name = models.CharField(max_length=120, blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['name']

    def __str__(self):
        return self.name


class CommissionAgent(BaseModel):
    """An agent who submits commission. Name is the natural key (matches how the
    workbooks identify the agent). Belongs to exactly one group."""
    name = models.CharField(max_length=160, unique=True)
    agent_code = models.CharField(max_length=20, blank=True, default='')
    group = models.ForeignKey(CommissionGroup, on_delete=models.PROTECT, related_name='agents')
    # Optional — used to match the signed-in user to their own submissions.
    email = models.CharField(max_length=160, blank=True, default='')
    # An independent agent working DIRECTLY for us has 10% withheld; one working
    # THROUGH a company does not (CFO 2026-07-15). This flag switches withholding
    # off for the via-company case. Irrelevant for payroll groups (they withhold
    # 0% anyway). Effective rate = 0 if works_via_company else group.withholding_rate.
    works_via_company = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['name']

    def __str__(self):
        return self.name


class CommissionBankAccount(BaseModel):
    """Agent bank details for payout. Sensitive — server-side only, access-gated
    at the API and never returned to a non-manager. Agents are payees (not
    policyholders); this mirrors how agent_portal / payroll treat payee bank
    data. Only agents WITH an account number are 'ready to pay'."""
    agent = models.OneToOneField(CommissionAgent, on_delete=models.CASCADE, related_name='bank')
    bank_name = models.CharField(max_length=120, blank=True, default='')
    account_name = models.CharField(max_length=160, blank=True, default='')
    account_number = models.CharField(max_length=40)
    branch_code = models.CharField(max_length=20, blank=True, default='')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    def __str__(self):
        return f"{self.agent.name} · {self.bank_name}"


class CommissionSubmission(BaseModel):
    """One agent's commission for one month. Totals are stored so a later config
    change never silently rewrites a signed-off figure (the rate in force is
    snapshotted at submit time)."""

    class Status(models.TextChoices):
        # Three-stage approval chain (CFO 2026-07-15): agent → 1st review
        # (Bokani/Tlamelo) → 2nd review (Pako/Kago) → final approval (CFO).
        DRAFT         = 'draft',         'Draft'
        SUBMITTED     = 'submitted',     'Submitted — 1st review'
        SECOND_REVIEW = 'second_review', '1st reviewed — 2nd review'
        FINAL_REVIEW  = 'final_review',  '2nd reviewed — final approval'
        APPROVED      = 'approved',      'Approved — awaiting payroll'
        REJECTED      = 'rejected',      'Rejected'
        PAID          = 'paid',          'Paid — payroll processed'

    agent = models.ForeignKey(CommissionAgent, on_delete=models.PROTECT, related_name='submissions')
    # Snapshot of the agent's group at create time (kept even if the agent later moves group).
    group = models.ForeignKey(CommissionGroup, on_delete=models.PROTECT, related_name='submissions')
    period_label = models.CharField(max_length=7, validators=[PERIOD_VALIDATOR],
                                    help_text="Month as YYYY-MM, e.g. 2026-05.")
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.DRAFT)

    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Per-stage sign-off stamps (who approved at each of the three stages).
    first_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')
    first_reviewed_at = models.DateTimeField(null=True, blank=True)
    second_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name='+')
    second_reviewed_at = models.DateTimeField(null=True, blank=True)
    final_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='+')
    final_at = models.DateTimeField(null=True, blank=True)
    # Payroll processing (after CFO final approval) + the notify-out stamp.
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='+')
    paid_at = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True, default='')

    # Computed + stored on recompute/submit.
    gross_commission = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    withholding_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal('0.0000'))
    withholding_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    net_payable = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    # Auto-feed to payroll (CFO 2026-08-28): set when an approved payroll-group
    # commission is pushed into a pending payroll batch. Idempotency guard — a
    # submission already linked is never fed twice.
    payroll_amendment = models.ForeignKey(
        'payroll.PayrollAmendment', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='commission_submissions')

    # The original uploaded workbook, kept so a reviewer can download and verify it
    # before approving (Bokani Makosha 2026-08-12; CFO chose to retain the file
    # rather than the previous parse-then-delete). Only per-policy agent uploads
    # attach a file; served only through the gated `statement` download action.
    statement_file     = models.FileField(upload_to='commission_statements/',
                                           null=True, blank=True)
    statement_filename = models.CharField(max_length=255, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-period_label', 'agent__name']
        # One submission per agent per month.
        constraints = [
            models.UniqueConstraint(fields=['agent', 'period_label'],
                                    name='uniq_commission_agent_period'),
        ]
        indexes = [
            models.Index(fields=['group', 'period_label']),
            models.Index(fields=['status', 'period_label']),
        ]

    def __str__(self):
        return f"{self.agent.name} · {self.period_label} · {self.get_status_display()}"


class CommissionSubmissionLine(BaseModel):
    """A single per-policy line on a submission — the fields the agents' Excel
    workbooks carry today."""

    class TxnType(models.TextChoices):
        NEW_BUSINESS   = 'new_business',   'New business'
        RENEWAL        = 'renewal',        'Renewal'
        ENDORSEMENT    = 'endorsement',    'Endorsement'
        PREVIOUS_MONTH = 'previous_month', 'Previous month'

    submission = models.ForeignKey(CommissionSubmission, on_delete=models.CASCADE, related_name='lines')
    policy_number = models.CharField(max_length=60, blank=True, default='', db_index=True)
    client_name = models.CharField(max_length=160, blank=True, default='')
    transaction_type = models.CharField(max_length=16, choices=TxnType.choices,
                                        default=TxnType.NEW_BUSINESS)
    # Annual / Monthly (from the source 'Data' tab). Blank when not stated.
    frequency = models.CharField(max_length=10, blank=True, default='')
    amount_collected = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    annualised_premium = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    # Commission rate as entered (informational — the payable is commission_amount,
    # not this). Wide precision so an odd/large rate in a source sheet never blocks
    # an import (Patience's June sheet had a rate ≥ 1000).
    commission_rate = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    # 'Amount Applicable for Commission (P)' — the base the commission is worked on.
    amount_applicable = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    collection_date = models.DateField(null=True, blank=True)
    is_policy_closed = models.BooleanField(default=False)
    # The commission earned on this line, as entered (kept verbatim so it matches
    # the agent's own working; see service.computed_commission for a cross-check).
    commission_amount = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))

    class Meta(BaseModel.Meta):
        ordering = ['created_at']

    def __str__(self):
        return f"{self.policy_number or '(no policy)'} · {self.commission_amount}"


class CommissionAmendment(BaseModel):
    """Append-only audit trail of an edit to a submission's lines (CFO 2026-07-17).

    When a staff member disagrees with a premium/rate and amends the collected
    amount, rate or commission on their DRAFT/REJECTED submission, one row is
    written here: who changed it, the full before/after line set, the gross
    before/after, and an Aria (DeepSeek) plain-English note. Never edited or
    deleted — it is the amendment trail Bokani/Tlamelo/CFO can inspect."""
    submission = models.ForeignKey(CommissionSubmission, on_delete=models.CASCADE,
                                   related_name='amendments')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name='+')
    # Full line snapshots (list of dicts) so the trail stands alone even if the
    # submission is later re-edited or deleted-and-recreated.
    old_lines = models.JSONField(default=list)
    new_lines = models.JSONField(default=list)
    old_gross = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    new_gross = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    # Aria's plain-English summary of what changed (deterministic fallback if the
    # DeepSeek call is unavailable — the trail is never blocked on the AI).
    note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [models.Index(fields=['submission', 'created_at'])]

    def __str__(self):
        return f"amend {self.submission_id} · {self.old_gross}→{self.new_gross}"


# ─── Broker commission (Rose Mokgware's request, CFO 2026-09-08) ─────────────
# Replaces the 28-tab "Broker Commission - <Month>.xlsm" workbook. The broker's
# client list is DERIVED from Graphite (policies.agency_id -> agencies), so the
# tabs no longer have to be kept by hand; BrokerPolicy holds only the rows a
# human added or uploaded on top, and the collection status is read live from
# RealPay rather than pasted in from a downloaded report.

class Broker(BaseModel):
    """An intermediary we pay commission to.

    Graphite carries the same broker under several agency rows — Redhill exists
    twice (spelled "Hilrange" AND "Hildrage"), Dynamic three times, Kgare three,
    SATIB twice. Paying per agency row would split one broker's commission, so a
    Broker owns many BrokerAlias rows and the register merges on this side.
    """
    name = models.CharField(max_length=160, unique=True)
    short_name = models.CharField(
        max_length=40, blank=True, default='',
        help_text="What Finance calls them on the workbook tab, e.g. 'Finsef'.")
    is_active = models.BooleanField(default=True)
    withholding_tax = models.BooleanField(
        default=True,
        help_text="Deduct 10% withholding tax from this broker's commission. "
                  "Off for brokers holding a valid exemption.")
    notes = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class BrokerAlias(BaseModel):
    """One Graphite agency name that belongs to this broker."""
    broker = models.ForeignKey(Broker, on_delete=models.CASCADE, related_name='aliases')
    graphite_agency_name = models.CharField(max_length=200)
    graphite_agency_id = models.CharField(max_length=40, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['graphite_agency_name']
        constraints = [
            models.UniqueConstraint(fields=['graphite_agency_name'],
                                    name='uniq_broker_alias_agency_name'),
        ]

    def __str__(self) -> str:
        return f'{self.graphite_agency_name} → {self.broker.name}'


class BrokerPolicy(BaseModel):
    """A policy on a broker's commission sheet that Graphite does not already
    give us — added in-app or uploaded from the old workbook.

    Deliberately NOT a mirror of every Graphite policy: the live list is read
    from Graphite on every request, and duplicating it here would go stale the
    way the workbook did. Rows here are the human overlay, and `source` records
    which is which so nobody has to guess later.
    """
    class Source(models.TextChoices):
        MANUAL = 'manual', 'Added in Omni'
        UPLOAD = 'upload', 'Uploaded from a workbook'

    broker = models.ForeignKey(Broker, on_delete=models.CASCADE, related_name='policies')
    policy_number = models.CharField(max_length=60, db_index=True)
    insured_name = models.CharField(
        max_length=160, blank=True, default='',
        help_text='PII — shown in the auth-gated screen only, never sent to an AI pipeline.')
    period_label = models.CharField(
        max_length=7, blank=True, default='', validators=[PERIOD_VALIDATOR],
        help_text='Month this row belongs to, YYYY-MM. Blank = standing.')

    premium = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    amount_received = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    motor_premium = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    motor_commission = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    non_motor_premium = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    non_motor_commission = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    commission_payable = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    vat = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))

    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MANUAL)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='+')
    # C3d — was this policy found in Graphite when it was added? False covers
    # both "Graphite was unreachable" and every uploaded row, so the register can
    # always tell a checked row from an unchecked one.
    graphite_verified = models.BooleanField(default=False)
    graphite_verified_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['policy_number']
        constraints = [
            models.UniqueConstraint(fields=['broker', 'policy_number', 'period_label'],
                                    name='uniq_broker_policy_period'),
        ]
        indexes = [models.Index(fields=['broker', 'period_label'])]

    def __str__(self) -> str:
        return f'{self.broker.name} · {self.policy_number}'


class BrokerCommissionRate(BaseModel):
    """Effective-dated commission rates (CFO-settled, 17-Sep-2026).

    Effective-dated rather than a single settings row because a rate change is
    a money event: last month's payable must still recompute at last month's
    rate after the rate moves, or every historical figure silently restates.
    The row in force for a period is the newest one whose `effective_from` is
    on or before that period's first day.

    MOTOR 12.5 / NON-MOTOR 20 / VAT 14 / ADMIN 8 / WHT 10 are the settled
    defaults. WHT is per-broker (`Broker.withholding_tax`), not global — some
    brokers are exempt — so the RATE lives here and the on/off decision on the
    broker.
    """
    effective_from = models.DateField(
        unique=True,
        help_text='First day this set of rates applies to. One row per change.')
    motor_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('12.500'))
    non_motor_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('20.000'))
    vat_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('14.000'))
    admin_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('8.000'))
    wht_pct = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('10.000'))
    note = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-effective_from']

    def __str__(self) -> str:
        return f'rates from {self.effective_from:%Y-%m-%d}'

    @classmethod
    def in_force_on(cls, day):
        """The rate row governing `day`, or None when nothing is configured.

        Returns None rather than inventing defaults: a commission figure
        computed from rates nobody configured is a made-up number, and the
        screen says so instead of showing it.
        """
        return cls.objects.filter(effective_from__lte=day).order_by('-effective_from').first()


# ─── C5 / C7 — month close, status history, compliance (19-Sep-2026) ─────────
# CFO decision 19-Sep-2026: the rollover is Finance pressing "Close month"
# (Full Access role only) — never a calendar job and never a report load.


class BrokerStatusHistory(BaseModel):
    """One policy's collection status for one debit month, as recorded.

    Append-only. `source` says how it got here: the month close, or a late
    PROCESSING row that resolved after its month was closed.
    """
    class Source(models.TextChoices):
        CLOSE = 'close', 'Month close'
        LATE = 'late', 'Late resolution'

    broker = models.ForeignKey(Broker, on_delete=models.CASCADE,
                               related_name='status_history')
    policy_number = models.CharField(max_length=60, db_index=True)
    debit_month = models.CharField(max_length=7, validators=[PERIOD_VALIDATOR])
    status = models.CharField(max_length=32)
    source = models.CharField(max_length=8, choices=Source.choices)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['debit_month', 'policy_number', 'created_at']
        indexes = [models.Index(fields=['broker', 'debit_month'])]

    def __str__(self) -> str:
        return f'{self.policy_number} {self.debit_month} {self.status}'


class BrokerMonthClose(BaseModel):
    """A broker's month, closed: the payable snapshot that becomes next
    month's Previous Payable. One row per broker per month — the unique
    constraint is what makes a second close of the same month a no-op."""
    broker = models.ForeignKey(Broker, on_delete=models.CASCADE,
                               related_name='month_closes')
    period = models.CharField(max_length=7, validators=[PERIOD_VALIDATOR])
    commission_excl_vat = models.DecimalField(max_digits=18, decimal_places=2)
    wht = models.DecimalField(max_digits=18, decimal_places=2)
    vat = models.DecimalField(max_digits=18, decimal_places=2)
    current_payable = models.DecimalField(max_digits=18, decimal_places=2)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-period', 'broker__name']
        constraints = [
            models.UniqueConstraint(fields=['broker', 'period'],
                                    name='uniq_broker_month_close'),
        ]

    def __str__(self) -> str:
        return f'{self.broker.name} {self.period} closed'


class BrokerPayableChange(BaseModel):
    """The log of every change to a closed month's payable (late resolution)."""
    month_close = models.ForeignKey(BrokerMonthClose, on_delete=models.CASCADE,
                                    related_name='changes')
    old_payable = models.DecimalField(max_digits=18, decimal_places=2)
    new_payable = models.DecimalField(max_digits=18, decimal_places=2)
    reason = models.CharField(max_length=300)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']


class BrokerCompliance(BaseModel):
    """C7 — the manual Compliance field, per broker per month. Editable only by
    the Broker Commission - Full Access role (enforced in the view)."""
    broker = models.ForeignKey(Broker, on_delete=models.CASCADE,
                               related_name='compliance')
    period = models.CharField(max_length=7, validators=[PERIOD_VALIDATOR])
    compliance = models.CharField(max_length=200, blank=True, default='')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-period', 'broker__name']
        constraints = [
            models.UniqueConstraint(fields=['broker', 'period'],
                                    name='uniq_broker_compliance_period'),
        ]
