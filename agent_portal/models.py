"""
agent_portal/models.py — UNICOIN sales-agent commission module (standalone).

CFO directive 2026-07-06: the Agent Portal computes commissions per the UNICOIN
SOP (see commission_engine.py), produces the pay-run (approved/rejected lines
with reasons) and the bank payout list, and exports a bank file. GL posting and
the CFO sign-off remain manual finance controls (no ledger/payments coupling).
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import BaseModel


class Agent(BaseModel):
    """A UniCoin Instant Insurance sales agent who earns commission. Name is the
    natural key (it is how the sales/report extracts identify the agent)."""
    name       = models.CharField(max_length=160, unique=True)
    agent_code = models.CharField(max_length=20, blank=True, default='')
    is_active  = models.BooleanField(default=True)
    # Streams the agent works in (list of engine stream keys) — reference only.
    streams    = models.JSONField(default=list, blank=True)
    # Contact + identity from the UniCoin 'Agent ID' master registry — used to
    # address and EMAIL a commission payslip to an agent who has NO omni login
    # (CFO 2026-07-27). ref_id is UniCoin's own Agent ID (e.g. '732', '900001').
    ref_id     = models.CharField(max_length=20, blank=True, default='', db_index=True)
    email      = models.EmailField(blank=True, default='')
    phone      = models.CharField(max_length=40, blank=True, default='')
    agency     = models.CharField(max_length=120, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['name']

    def __str__(self):
        return self.name


class AgentBankAccount(BaseModel):
    """Agent bank details for payout. Sensitive — server-side only, access-gated
    at the API. Only agents WITH bank details are 'ready to pay'."""
    agent          = models.OneToOneField(Agent, on_delete=models.CASCADE, related_name='bank')
    # Source sheets carry account + branch code only (branch code identifies the
    # bank in BW); bank_name is optional and can be filled later.
    bank_name      = models.CharField(max_length=120, blank=True, default='')
    account_name   = models.CharField(max_length=160, blank=True, default='')
    account_number = models.CharField(max_length=40)
    branch_code    = models.CharField(max_length=20, blank=True, default='')
    updated_by     = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='+')

    def __str__(self):
        return f"{self.agent.name} · {self.bank_name}"


class CommissionCycle(BaseModel):
    """A commission run for a date range (drives the 3-month conversion cutoff
    and the pay-run). One 'active' cycle at a time is typical."""
    class Status(models.TextChoices):
        OPEN     = 'open',     'Open'
        APPROVED = 'approved', 'Approved (pay-run signed off)'
        PAID     = 'paid',     'Paid / exported'

    label      = models.CharField(max_length=80)
    start_date = models.DateField()
    end_date   = models.DateField()
    status     = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    # Sign-off stamp (set by service.approve_cycle). An approved/paid cycle is
    # locked against re-ingest until reopened.
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-end_date']

    def __str__(self):
        return f"{self.label} ({self.start_date}→{self.end_date})"


class CommissionLine(BaseModel):
    """One computed commission line for a cycle: what the engine returned for a
    single source row (a policy / claim / incentive), payable or not + why."""
    cycle      = models.ForeignKey(CommissionCycle, on_delete=models.CASCADE, related_name='lines')
    agent      = models.ForeignKey(Agent, on_delete=models.PROTECT, related_name='lines')
    stream     = models.CharField(max_length=24)      # engine stream key
    basis      = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    commission = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    payable    = models.BooleanField(default=False)
    reason     = models.CharField(max_length=200, blank=True, default='')
    # Policy / item reference (source[1]) — key for the never-pay-twice
    # cross-cycle guard (service.ingest_stream_csv). Blank for streams with no
    # policy number (e.g. Proposed Incentives).
    policy_ref = models.CharField(max_length=60, blank=True, default='', db_index=True)
    # The raw source row (list of cells) that produced this line — for audit.
    source     = models.JSONField(default=list, blank=True)
    # Live Graphite verification (CFO 2026-07-07 "bridge plan"): filled by
    # agent_portal.graphite_check against the Graphite read replica. Additive +
    # nullable — a blank status means "not yet checked". The engine's own
    # payable/reason stays the money truth; this is an independent confirmation.
    class GraphiteStatus(models.TextChoices):
        OK          = 'ok', 'Confirmed in Graphite'
        MISMATCH    = 'mismatch', 'Differs from Graphite'
        NOT_FOUND   = 'not_found', 'Not found in Graphite'
        UNAVAILABLE = 'unavailable', 'Graphite unavailable'
        SKIPPED     = 'skipped', 'No policy number to check'
    graphite_status     = models.CharField(max_length=12, blank=True, default='',
                                           choices=GraphiteStatus.choices)
    graphite_note       = models.CharField(max_length=200, blank=True, default='')
    graphite_checked_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['agent__name', 'stream']
        indexes = [
            models.Index(fields=['cycle', 'stream']),
            models.Index(fields=['cycle', 'payable']),
        ]

    def __str__(self):
        return f"{self.agent.name} · {self.stream} · {self.commission}"


class ReportSubmission(BaseModel):
    """One uploaded/pasted daily report — the raw submission as received.

    Mirrors the tool's 'Submitted reports' log, DB-backed: who submitted, for
    which agent and day, which stream, the raw rows, and what the engine made
    of them. The computed CommissionLines stay the money truth; this is the
    upload audit trail (CFO 2026-07-07: collect what people upload here)."""

    class Source(models.TextChoices):
        PASTE = 'paste', 'Pasted rows'
        SHEET = 'sheet', 'Template sheet upload'
        HAND = 'hand', 'Single row by hand'
        ALL_POLICIES = 'all_policies', 'All-policies import'

    cycle = models.ForeignKey(CommissionCycle, on_delete=models.CASCADE, related_name='submissions')
    stream = models.CharField(max_length=24)
    agent_name = models.CharField(max_length=160, blank=True, default='',
                                  help_text="The 'Your name' field on the Daily tab (may differ per row).")
    report_date = models.DateField(null=True, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.PASTE)
    raw_text = models.TextField(help_text='The rows exactly as submitted (CSV/TSV).')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    rows_count = models.PositiveIntegerField(default=0)
    approved_count = models.PositiveIntegerField(default=0)
    rejected_count = models.PositiveIntegerField(default=0)
    payable_bwp = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [models.Index(fields=['cycle', 'stream'])]

    def __str__(self):
        return f"{self.get_source_display()} · {self.stream} · {self.agent_name or self.submitted_by_id}"


class SourceReport(BaseModel):
    """A control report uploaded on the Reports tab (Motlatsi 2026-07-07):
    All Policies / Transaction / Bank Statement. Stored raw; the All-Policies
    report also drives the policy-status check over the cycle's lines."""

    class Kind(models.TextChoices):
        ALL_POLICIES = 'all_policies', 'All Policies Report'
        TRANSACTIONS = 'transactions', 'Transaction Report'
        BANK_STATEMENT = 'bank_statement', 'Bank Statement Report'

    cycle = models.ForeignKey(CommissionCycle, on_delete=models.CASCADE, related_name='source_reports')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    raw_text = models.TextField(help_text='Rows exactly as uploaded/pasted (CSV/TSV).')
    rows_count = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [models.Index(fields=['cycle', 'kind'])]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.rows_count} rows"


class PayoutBatch(BaseModel):
    """An exported bank-payout batch for a cycle — the file handed to the bank.
    Only agents with bank details + a payable total appear in it."""
    cycle       = models.ForeignKey(CommissionCycle, on_delete=models.CASCADE, related_name='payouts')
    created_by  = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    agent_count = models.PositiveIntegerField(default=0)
    total_bwp   = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    held_count  = models.PositiveIntegerField(default=0)   # payable but no bank details
    fmt         = models.CharField(max_length=20, default='csv')

    def __str__(self):
        return f"Payout {self.cycle.label} · {self.agent_count} agents · {self.total_bwp}"


class AgentPayslip(BaseModel):
    """A UniCoin Instant Insurance commission payslip for one agent × one cycle.

    Built from the agent's PAYABLE commission for the cycle (which ties to the
    uploaded pay-run), with Bharath's tax method applied on top (agent_portal.tax).
    Agents have NO omni login, so this is a document handed / emailed to them.

    The money fields are a SNAPSHOT taken at generation time; `breakdown` holds
    the per-stream earning lines so the printed slip itemises how the total was
    earned. Regenerating for the same (cycle, agent) overwrites in place.
    """
    cycle       = models.ForeignKey(CommissionCycle, on_delete=models.CASCADE, related_name='payslips')
    agent       = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='payslips')
    number      = models.CharField(max_length=32, unique=True)   # e.g. UNI-2607-0001

    gross       = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    ex_vat      = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))  # memo
    annual      = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    tax         = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    net         = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))

    # Per-stream earning lines at generation time: [["New Sales", "343.08"], ...]
    breakdown   = models.JSONField(default=list, blank=True)
    # A short list of this cycle's rejected items for the agent, so the slip can
    # honestly say what was NOT paid and why: [["Collection","MIS...","reason"]].
    not_paid    = models.JSONField(default=list, blank=True)

    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    emailed_at   = models.DateTimeField(null=True, blank=True)
    emailed_to   = models.EmailField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['agent__name']
        constraints = [
            models.UniqueConstraint(fields=['cycle', 'agent'], name='uniq_payslip_cycle_agent'),
        ]
        indexes = [models.Index(fields=['cycle', 'agent'])]

    def __str__(self):
        return f"{self.number} · {self.agent.name} · net {self.net}"


class VerificationRun(BaseModel):
    """One 'Verify against Graphite' click — the audit row per run (CFO
    2026-07-07 bridge plan). Records who ran it and the tally of outcomes."""
    cycle          = models.ForeignKey(CommissionCycle, on_delete=models.CASCADE, related_name='verifications')
    run_by         = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name='+')
    lines_checked  = models.PositiveIntegerField(default=0)
    ok_count       = models.PositiveIntegerField(default=0)
    mismatch_count = models.PositiveIntegerField(default=0)
    not_found_count= models.PositiveIntegerField(default=0)
    skipped_count  = models.PositiveIntegerField(default=0)
    unavailable    = models.BooleanField(default=False)  # replica was unreachable

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']

    def __str__(self):
        return f"Verify {self.cycle.label} · {self.ok_count}ok/{self.mismatch_count}mm/{self.not_found_count}nf"
