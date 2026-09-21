"""
bonu/models.py — the BONU legal-benefit revenue line: invoices and the work behind them.

CFO 2026-08-03: *"we need in depth analysis of invoices the work they performed
because lawyers are crooks and they can cheat us."*

Why these models have to exist at all: BONU is a **P9.36M written-premium line**, and
the claims side already carries **890 journal lines worth P6.34M** — every one of them
described `"BONU Clamis"` with **no law firm attached**. There is nothing in the general
ledger to audit. You cannot ask "did this firm bill us twice for the same matter" of a
number whose only description is a typo.

So the invoice DETAIL lands here, one row per billed line, and the forensic checks in
`bonu/forensics.py` run against it. GL posting is deliberately NOT wired yet (CFO:
"for now let us wire to GL later") — this is read-and-analyse only, so nothing here can
move a reported figure.

MEMBER PRIVACY: a legal matter identifies a person and often something sensitive about
their life. `member_ref` holds the scheme's own reference, never a name, never an Omang.
`matter_description` is free text typed by the firm, so it is treated as unsafe and is
redacted before any AI ever sees it (AD-POL-AI-GOV-001).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel


class LawFirm(AuditableMixin, BaseModel):
    """A panel firm that bills BONU work."""

    name = models.CharField(max_length=200, unique=True)
    trading_name = models.CharField(max_length=200, blank=True, default='')
    contact_email = models.CharField(max_length=200, blank=True, default='')
    is_active = models.BooleanField(default=True)

    # The agreed tariff. A rate above this on an invoice line is the single most
    # useful over-billing test there is, and it needs no AI to find.
    agreed_hourly_rate = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Agreed rate per hour in BWP. Blank = no tariff on file, so rate '
                  'checks cannot run for this firm.')
    agreed_fixed_fees = models.JSONField(
        default=dict, blank=True,
        help_text='{service_code: agreed fee} for fixed-fee work such as conveyancing.')
    panel_since = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')

    # Link to the vendor record so a firm's bill can be paid through the protected
    # vendor-bank vault (procurement.VendorBankAccount — maker-checker, immutable
    # once active), instead of re-typing an account number on every payment. The
    # LIVE bank account is resolved at payment time from the contact's ACTIVE
    # vault row, never cached here, so a rotated/retired account can't go stale.
    vendor_contact = models.ForeignKey(
        'billing.Contact', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bonu_law_firms',
        help_text='The billing Contact that carries this firm\'s vaulted bank details.')

    class Meta(BaseModel.Meta):
        ordering = ['name']
        verbose_name = 'BONU law firm'
        verbose_name_plural = 'BONU law firms'

    def __str__(self):
        return self.name


class BonuInvoice(AuditableMixin, BaseModel):
    """One invoice received from a panel firm."""

    class Status(models.TextChoices):
        RECEIVED = 'received', 'Received'
        UNDER_REVIEW = 'review', 'Under review'
        QUERIED = 'queried', 'Queried with the firm'
        APPROVED = 'approved', 'Approved for payment'
        REJECTED = 'rejected', 'Rejected'

    firm = models.ForeignKey(LawFirm, on_delete=models.PROTECT, related_name='invoices')
    invoice_number = models.CharField(max_length=80)
    invoice_date = models.DateField()
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    vat = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    total = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEIVED)
    source_file = models.CharField(max_length=255, blank=True, default='',
                                   help_text='The file this was ingested from, for audit.')
    review_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-invoice_date', 'firm__name']
        constraints = [
            # The same firm cannot present the same invoice number twice. This is the
            # cheapest possible duplicate-billing control and it belongs in the database,
            # not in a report that somebody has to remember to read.
            models.UniqueConstraint(fields=['firm', 'invoice_number'],
                                    name='bonu_uniq_firm_invoice_no'),
        ]
        indexes = [models.Index(fields=['invoice_date'], name='bonu_inv_date_idx')]
        verbose_name = 'BONU invoice'

    def __str__(self):
        return f'{self.firm_id} {self.invoice_number} ({self.total})'

    @property
    def line_total(self):
        return sum((l.amount or Decimal('0')) for l in self.lines.all())

    @property
    def foots(self) -> bool:
        """Do the lines add up to the invoice subtotal? An invoice that does not foot
        is either mis-keyed or padded, and either way it should not be paid."""
        return abs(self.line_total - (self.subtotal or Decimal('0'))) <= Decimal('0.05')


class BonuInvoiceLine(AuditableMixin, BaseModel):
    """One billed item: the work the firm says it performed."""

    class MatterType(models.TextChoices):
        """What KIND of legal work this is.

        A controlled list on purpose. The CFO needs to filter spend by case type —
        "show me every divorce" — and that is impossible against `service_code`
        (free text the firm types) or `matter_description` (a narrative). Categories
        are also safe to reason over: "Firm A, matter 118/2026, divorce" identifies
        nobody, whereas the narrative can.
        """
        DIVORCE = 'divorce', 'Divorce / family'
        CONVEYANCING = 'conveyancing', 'Conveyancing / property transfer'
        CRIMINAL = 'criminal', 'Criminal defence'
        LABOUR = 'labour', 'Labour / employment'
        DEBT = 'debt', 'Debt collection'
        ESTATE = 'estate', 'Deceased estate / will'
        CIVIL = 'civil', 'Civil litigation'
        CONTRACT = 'contract', 'Contract / commercial'
        ROAD = 'road', 'Traffic / road accident'
        TENANCY = 'tenancy', 'Landlord / tenancy'
        ADVICE = 'advice', 'Consultation / advice only'
        OTHER = 'other', 'Other / unclassified'

    class Basis(models.TextChoices):
        HOURLY = 'hourly', 'Hourly'
        FIXED = 'fixed', 'Fixed fee'
        DISBURSEMENT = 'disb', 'Disbursement'
        OTHER = 'other', 'Other'

    invoice = models.ForeignKey(BonuInvoice, on_delete=models.CASCADE, related_name='lines')
    line_no = models.PositiveSmallIntegerField(default=0)

    service_date = models.DateField(null=True, blank=True,
                                    help_text='When the work was performed — not the invoice date.')
    matter_ref = models.CharField(max_length=80, blank=True, default='',
                                  help_text="The firm's matter/file reference.")
    # Scheme reference ONLY. Never a member name, never an Omang.
    member_ref = models.CharField(max_length=80, blank=True, default='', db_index=True)
    service_code = models.CharField(max_length=40, blank=True, default='')
    matter_type = models.CharField(max_length=14, choices=MatterType.choices,
                                   default=MatterType.OTHER, db_index=True,
                                   help_text='The case type, from a fixed list — this is what '
                                             'makes "show me every divorce" possible.')
    matter_description = models.TextField(blank=True, default='',
                                          help_text='Free text from the firm. Treated as unsafe: '
                                                    'redacted before any AI review.')

    # WHO said what kind of case this is. A spend report by case type is only as good as
    # the classification behind it, so the source is recorded rather than assumed. CFO
    # 2026-08-03 decision: the firm states it on the bill (contract term), the AI may
    # pre-fill, and the accountant confirms — but a guess must never look like a fact.
    class ClassifiedBy(models.TextChoices):
        FIRM = 'firm', 'Stated by the firm on the invoice'
        MANUAL = 'manual', 'Set by our accountant'
        AI = 'ai', 'Suggested by AI — not yet confirmed'
        DEFAULT = 'default', 'Never classified'

    matter_type_source = models.CharField(
        max_length=7, choices=ClassifiedBy.choices, default=ClassifiedBy.DEFAULT,
        help_text='An AI guess is reported as a guess. Spend reports say how much of the '
                  'total rests on unconfirmed classification.')

    # WHICH MEMBER, without holding who they are. A one-way token of the member's name, so
    # "the same person appears on five matters across three firms" is answerable while the
    # name itself is never stored (bonu/member_identity.py). This is the field the repeat-
    # claimer checks join on — the single most valuable fraud test in the module, and the one
    # my first pass destroyed by discarding the name outright.
    member_token = models.CharField(
        max_length=20, blank=True, default='', db_index=True,
        help_text='One-way token of the member, for spotting the same person across firms. '
                  'Cannot be turned back into a name.')

    basis = models.CharField(max_length=8, choices=Basis.choices, default=Basis.HOURLY)
    fee_earner = models.CharField(max_length=120, blank=True, default='')
    units = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                help_text='Hours, or 1 for a fixed fee.')
    rate = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))

    class Meta(BaseModel.Meta):
        ordering = ['invoice', 'line_no']
        indexes = [
            models.Index(fields=['matter_ref'], name='bonu_line_matter_idx'),
            models.Index(fields=['service_date'], name='bonu_line_svcdate_idx'),
            models.Index(fields=['matter_type'], name='bonu_line_mtype_idx'),
            models.Index(fields=['fee_earner'], name='bonu_line_earner_idx'),
        ]
        verbose_name = 'BONU invoice line'

    def __str__(self):
        return f'{self.matter_ref or "(no matter)"} {self.amount}'

    @property
    def recomputed(self):
        """units x rate — what the line SHOULD say."""
        if self.units is None or self.rate is None:
            return None
        return (self.units * self.rate).quantize(Decimal('0.01'))

    @property
    def arithmetic_ok(self):
        """False when units x rate does not equal the amount charged. Not fraud on its
        own, but it is where padding hides and it is free to check."""
        rc = self.recomputed
        if rc is None:
            return None
        return abs(rc - (self.amount or Decimal('0'))) <= Decimal('0.05')


class BonuFinding(BaseModel):
    """One thing worth asking the firm about. Produced by the rule engine or by the AI
    reviewer, kept so a query and its answer survive past the screen."""

    class Severity(models.TextChoices):
        HIGH = 'high', 'High'
        MEDIUM = 'medium', 'Medium'
        LOW = 'low', 'Low'

    class Source(models.TextChoices):
        RULE = 'rule', 'Rule'
        AI = 'ai', 'AI review'

    invoice = models.ForeignKey(BonuInvoice, on_delete=models.CASCADE,
                               related_name='findings', null=True, blank=True)
    line = models.ForeignKey(BonuInvoiceLine, on_delete=models.CASCADE,
                             related_name='findings', null=True, blank=True)
    firm = models.ForeignKey(LawFirm, on_delete=models.CASCADE, related_name='findings',
                             null=True, blank=True)

    code = models.CharField(max_length=40, help_text='Stable rule id, e.g. DUP_SAME_MATTER.')
    severity = models.CharField(max_length=6, choices=Severity.choices, default=Severity.MEDIUM)
    source = models.CharField(max_length=4, choices=Source.choices, default=Source.RULE)
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True, default='')
    amount_at_risk = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    question_for_firm = models.TextField(blank=True, default='',
                                         help_text='The exact question to put to the firm.')
    resolved = models.BooleanField(default=False)
    resolution_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-amount_at_risk', 'severity']
        indexes = [models.Index(fields=['code', 'resolved'], name='bonu_find_code_idx')]
        verbose_name = 'BONU finding'

    def __str__(self):
        return f'{self.code} {self.title[:40]} ({self.amount_at_risk})'


# ───────────────────────────────────────────────────────────────────────────────
# RETAINERS — a flat monthly fee is a completely different animal from an hourly
# bill, and the forensic rules above cannot see into it at all.
#
# CFO 2026-08-03: "we have an SLA with some lawyers, for example Jeremiah and
# Taldi. These people do our work for 85,000 BWP per month. The objective was they
# have to manage around 40 cases but sometimes they are not managing 40 cases and
# things are slipping out."
#
# On a retainer nobody sends a padded line item — the risk is the opposite: the
# invoice is always exactly P85,000 and says nothing, so the only question that
# matters is **what did we get for it**. That cannot be answered from an invoice.
# It needs a case register, which is what these models are.
# ───────────────────────────────────────────────────────────────────────────────

class RetainerAgreement(AuditableMixin, BaseModel):
    """A flat monthly fee against a committed caseload."""

    firm = models.ForeignKey(LawFirm, on_delete=models.PROTECT, related_name='retainers')
    name = models.CharField(max_length=140, help_text='e.g. "Jeremiah & Taldi — BONU panel".')
    monthly_fee = models.DecimalField(max_digits=14, decimal_places=2,
                                      help_text='The flat fee, e.g. 85000.00 BWP.')
    committed_cases = models.PositiveSmallIntegerField(
        default=0, help_text='Cases they undertake to have under management each month, e.g. 40.')
    # A retainer only bites if the standard is written down. These are the SLA terms
    # that the scorecard measures against.
    max_days_no_activity = models.PositiveSmallIntegerField(
        default=30, help_text='A case with no recorded movement for this many days is slipping.')
    max_days_to_first_action = models.PositiveSmallIntegerField(
        default=5, help_text='Days from instruction to first recorded action.')
    scope_note = models.TextField(blank=True, default='',
                                  help_text='What the fee covers — and what it does NOT, so extra '
                                            'invoices for in-scope work can be challenged.')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['firm__name', '-start_date']
        verbose_name = 'BONU retainer'

    def __str__(self):
        return f'{self.name} ({self.monthly_fee}/month for {self.committed_cases} cases)'

    @property
    def fee_per_committed_case(self):
        """What we intend to pay per case. P85,000 / 40 = P2,125."""
        if not self.committed_cases:
            return None
        return (self.monthly_fee / self.committed_cases).quantize(Decimal('0.01'))


class LegalCase(AuditableMixin, BaseModel):
    """One member's matter. The unit the retainer is actually measured in."""

    class Status(models.TextChoices):
        INSTRUCTED = 'instructed', 'Instructed — not yet started'
        ACTIVE = 'active', 'Active'
        AWAITING_COURT = 'court', 'Awaiting court date'
        AWAITING_MEMBER = 'member', 'Waiting on the member'
        SETTLED = 'settled', 'Settled'
        WON = 'won', 'Judgment in our member\'s favour'
        LOST = 'lost', 'Judgment against'
        WITHDRAWN = 'withdrawn', 'Withdrawn'
        ABANDONED = 'abandoned', 'Abandoned / went quiet'

    OPEN_STATUSES = ('instructed', 'active', 'court', 'member')

    class FirmType(models.TextChoices):
        EXTERNAL = 'external', 'External law firm'
        IN_HOUSE = 'in_house', 'In-house (Alpha Law)'

    case_ref = models.CharField(max_length=80, help_text="The firm's own file reference.")
    # WHO IS RUNNING THE MATTER. Until Kelvin Kimani's spec (9 Sep 2026) a case
    # could only belong to an external firm, so the office's own matters had
    # nowhere to be opened at all. One or the other, never both — and the
    # DATABASE enforces the pairing (`bonu_case_firm_matches_type`) rather than
    # the form, because an identity check that can be satisfied by a default is
    # not a check. That mistake has already cost two days here: the entity-code
    # bug (24 Jul 2026) and the claims-are-ADIC rule (29 Jul 2026), where
    # unknown input resolved to a default and so PASSED the very test meant to
    # stop it (checklist L6, approved by the CFO 29 Jul 2026).
    firm_type = models.CharField(
        max_length=8, choices=FirmType.choices, default=FirmType.EXTERNAL, db_index=True,
        help_text='In-house or external. Exactly one, and it decides whether a firm is set.')
    firm = models.ForeignKey(LawFirm, null=True, blank=True, on_delete=models.PROTECT,
                             related_name='cases',
                             help_text='The panel firm. Empty ONLY when the matter is in-house.')
    internal_officer = models.CharField(
        max_length=120, blank=True, default='',
        help_text='The in-house officer running it, when we know. Deliberately optional and '
                  'deliberately gates nothing — an unnamed in-house matter is still an '
                  'in-house matter, and refusing to open it would push it off the register.')
    region = models.CharField(
        max_length=40, blank=True, default='', db_index=True,
        help_text='The Botswana city or town the matter sits in, from the fixed list in '
                  'bonu/legal_rules.py. A list and not free text, because the reason for '
                  'capturing it is to group matters, and three spellings are three groups.')
    # The cap in Part 1 of the spec is per CLIENT across all their matters, so
    # "which client" has to be a real link. `member_ref` below is a typed string
    # and cannot be trusted to total money against.
    client = models.ForeignKey('BonuMember', null=True, blank=True, on_delete=models.PROTECT,
                               related_name='legal_cases',
                               help_text='The structured client link. The per-client legal-spend '
                                         'cap is aggregated on THIS, never on a typed name.')
    retainer = models.ForeignKey(RetainerAgreement, null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name='cases',
                                 help_text='Set when this case counts toward a retainer caseload.')
    member_ref = models.CharField(max_length=80, db_index=True,
                                  help_text='Scheme reference only — never a name.')
    matter_type = models.CharField(max_length=14, choices=BonuInvoiceLine.MatterType.choices,
                                   default=BonuInvoiceLine.MatterType.OTHER, db_index=True)
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.INSTRUCTED, db_index=True)

    # The four dates the legal team actually runs a matter on (Kelvin Kimani,
    # 9 Sep 2026). Each is a genuinely different point in time, and the gap
    # between them is the thing the team is trying to see — so collapsing any
    # two of them into one field would destroy the reason for having them.
    received_on = models.DateField(
        null=True, blank=True, db_index=True,
        help_text='When the claim reached us. Days-to-process counts from here, so a case '
                  'with no received date reports "not known" rather than nought days.')
    date_of_loss = models.DateField(null=True, blank=True,
                                    help_text='When the incident or loss happened.')
    matter_arose_on = models.DateField(
        null=True, blank=True,
        help_text='When the legal matter or dispute arose — often later than the loss.')
    firm_contact_on = models.DateField(
        null=True, blank=True,
        help_text='When the law firm (or the in-house team) was FIRST CONTACTED — the '
                  'earlier, separate step before they were formally instructed.')

    instructed_on = models.DateField(
        help_text='Law firm INSTRUCTION date — when they were formally instructed.')
    first_action_on = models.DateField(null=True, blank=True)
    last_activity_on = models.DateField(null=True, blank=True, db_index=True)
    next_action_due = models.DateField(null=True, blank=True)
    court_date = models.DateField(null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)
    outcome_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-instructed_on']
        constraints = [
            models.UniqueConstraint(fields=['firm', 'case_ref'], name='bonu_uniq_firm_case_ref'),
            # An in-house case has NO firm, and Postgres treats every NULL as
            # distinct — so the constraint above silently stops policing the
            # moment in-house exists. This is the other half of it.
            models.UniqueConstraint(fields=['case_ref'], condition=models.Q(firm__isnull=True),
                                    name='bonu_uniq_inhouse_case_ref'),
            # Firm type and firm must agree: external means a firm is set,
            # in-house means one is not. In the database, so it cannot be
            # bypassed by a form, an import, a shell or a future screen.
            models.CheckConstraint(
                check=(models.Q(firm_type='external', firm__isnull=False)
                       | models.Q(firm_type='in_house', firm__isnull=True)),
                name='bonu_case_firm_matches_type'),
        ]
        indexes = [models.Index(fields=['status', 'last_activity_on'], name='bonu_case_status_idx')]
        verbose_name = 'BONU legal case'

    def __str__(self):
        return f'{self.case_ref} ({self.get_status_display()})'

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    def days_quiet(self, as_of=None):
        """Days since anything was recorded. This is what "slipping out" looks like."""
        # Botswana's today. `date.today()` is the box's clock (UTC), which
        # reads yesterday until 02:00 Gaborone — a case touched today then
        # reports -1 days quiet and moves for no reason but the hour.
        as_of = as_of or timezone.localdate()
        ref = self.last_activity_on or self.first_action_on or self.instructed_on
        return (as_of - ref).days if ref else None

    @property
    def is_in_house(self) -> bool:
        return self.firm_type == self.FirmType.IN_HOUSE

    @property
    def handler(self) -> str:
        """Who to show where a 'firm' used to be shown, in-house included."""
        if self.is_in_house:
            return f'In-house — {self.internal_officer}' if self.internal_officer else 'In-house'
        return self.firm.name if self.firm_id else '(no firm set)'

    def days_to_process(self, as_of=None):
        """Days since the claim was received — live while open, frozen at closure.

        None when we were never told when it arrived. An unknown start must not
        read as "processed the same day" — the identical trap that
        `LegalInvoiceSaving.turnaround_days` already guards, where an unknown
        turnaround must never be counted as a met SLA.
        """
        from bonu.legal_rules import days_to_process as _days
        return _days(self.received_on, self.closed_on, as_of=as_of)


class CaseEvent(BaseModel):
    """Something that actually happened on a case. The evidence of work.

    Without this a firm can claim 40 cases "under management" while nothing moves.
    An event is the smallest provable unit of activity, and it is what drives
    `last_activity_on` and therefore every SLA measure.
    """

    class Kind(models.TextChoices):
        INSTRUCTED = 'instructed', 'Instructed'
        CONSULT = 'consult', 'Consultation'
        LETTER = 'letter', 'Letter / correspondence'
        FILING = 'filing', 'Filed at court'
        HEARING = 'hearing', 'Court appearance'
        NEGOTIATION = 'negotiation', 'Negotiation'
        UPDATE = 'update', 'Status update from the firm'
        CLOSED = 'closed', 'Closed'
        NOTE = 'note', 'Internal note'

    case = models.ForeignKey(LegalCase, on_delete=models.CASCADE, related_name='events')
    happened_on = models.DateField()
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.UPDATE)
    detail = models.TextField(blank=True, default='')
    reported_by = models.CharField(max_length=120, blank=True, default='',
                                   help_text='Who told us — the firm, the member, or us.')

    class Meta(BaseModel.Meta):
        ordering = ['-happened_on']
        indexes = [models.Index(fields=['case', '-happened_on'], name='bonu_event_case_idx')]
        verbose_name = 'BONU case event'

    def __str__(self):
        return f'{self.case_id} {self.kind} {self.happened_on}'


# ───────────────────────────────────────────────────────────────────────────────
# ASKING THE FIRM — the only step that actually recovers money.
#
# A finding sitting on a dashboard recovers nothing. The letter does. So a query
# is a tracked object with a reply date on it: raised → sent → replied → closed,
# with what we claimed and what the firm conceded, per firm. That last figure is
# the honest measure of whether this whole exercise is worth running.
# ───────────────────────────────────────────────────────────────────────────────

class QueryLetter(AuditableMixin, BaseModel):
    """One written query to a firm, covering one or more findings."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Drafted — not sent'
        SENT = 'sent', 'Sent to the firm'
        REPLIED = 'replied', 'Firm has replied'
        CONCEDED = 'conceded', 'Firm accepted the query'
        REJECTED = 'rejected', 'Firm stands by the charge'
        WITHDRAWN = 'withdrawn', 'We withdrew the query'

    OPEN_STATUSES = ('draft', 'sent')

    firm = models.ForeignKey(LawFirm, on_delete=models.PROTECT, related_name='queries')
    reference = models.CharField(max_length=40, help_text='Our own query reference.')
    findings = models.ManyToManyField(BonuFinding, related_name='queries', blank=True)

    subject = models.CharField(max_length=200)
    body = models.TextField(help_text='The letter itself, in plain language, question by question.')
    amount_queried = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))
    amount_conceded = models.DecimalField(max_digits=16, decimal_places=2, default=Decimal('0'))

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    sent_on = models.DateField(null=True, blank=True)
    reply_due_on = models.DateField(null=True, blank=True,
                                    help_text='A query with no date on it is a query nobody answers.')
    replied_on = models.DateField(null=True, blank=True)
    chased_count = models.PositiveSmallIntegerField(default=0)
    last_chased_on = models.DateField(null=True, blank=True)
    reply_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-sent_on', '-created_at']
        constraints = [
            models.UniqueConstraint(fields=['firm', 'reference'], name='bonu_uniq_firm_query_ref'),
        ]
        verbose_name = 'BONU query letter'

    def __str__(self):
        return f'{self.reference} → {self.firm_id} ({self.status})'

    def is_overdue(self, as_of=None) -> bool:
        """Sent, past its reply date, still no answer."""
        if self.status != self.Status.SENT or not self.reply_due_on:
            return False
        # Botswana's today: on the box's UTC clock a letter that fell due
        # yesterday still reads as in time until 02:00, on the very
        # morning the chase should start.
        return (as_of or timezone.localdate()) > self.reply_due_on


class ApprovalThreshold(AuditableMixin, BaseModel):
    """What a case type may cost before somebody has to say yes.

    CFO 2026-08-03 recommendation 8: *"Above a set amount the firm needs a yes before
    doing the work, not after."* A limit is only a control if it is checked at the point
    the bill arrives, so it lives here as data and is applied in `panel.py`.
    """

    matter_type = models.CharField(max_length=14, choices=BonuInvoiceLine.MatterType.choices,
                                   unique=True)
    typical_cost = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True,
        help_text='What this kind of case normally costs us. Blank = work it out from our own '
                  'history instead.')
    approval_above = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text='Above this total, the firm must get written approval BEFORE doing the work.')
    warn_multiple = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('2.00'),
        help_text='Warn when a case reaches this multiple of typical — catch it at 2x, not 5x.')
    note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['matter_type']
        verbose_name = 'BONU approval limit'

    def __str__(self):
        return f'{self.matter_type} > {self.approval_above}'


class MemberTokenSalt(BaseModel):
    """The single secret that makes member tokens work — created once, never rotated.

    A token is HMAC(salt, normalised name). Rotating the salt would change every token and
    silently break every "same member" join already made, so there is deliberately no way to
    change it from the application. The salt itself is not a member's data; it is what stops
    anyone turning a token back into a name.
    """

    salt = models.CharField(max_length=128)

    class Meta(BaseModel.Meta):
        verbose_name = 'BONU member token salt'


class IngestedDocument(BaseModel):
    """A bill that arrived — by upload or by email — and what we read off it.

    The parse is kept SEPARATE from the invoice on purpose. Nothing a machine read is
    allowed to become a payable until a person has looked at it next to the document and
    agreed. This row is that waiting room, and it is also the audit trail: the original
    file name, how the text was obtained, and exactly what was proposed before anyone
    touched it.
    """

    class Status(models.TextChoices):
        PARSED = 'parsed', 'Read — waiting for the accountant'
        NEEDS_OCR = 'needs_ocr', 'A scan — needs the vision model'
        FAILED = 'failed', 'Could not be read'
        CONFIRMED = 'confirmed', 'Confirmed into an invoice'
        DISCARDED = 'discarded', 'Discarded'

    class Arrival(models.TextChoices):
        UPLOAD = 'upload', 'Uploaded by the accountant'
        EMAIL = 'email', 'Emailed in by the firm'

    filename = models.CharField(max_length=255)
    arrived_by = models.CharField(max_length=6, choices=Arrival.choices, default=Arrival.UPLOAD)
    from_address = models.CharField(max_length=200, blank=True, default='',
                                    help_text='Who emailed it, when it came in by mail.')
    firm = models.ForeignKey(LawFirm, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='documents')

    extraction_method = models.CharField(max_length=60, blank=True, default='')
    extraction_error = models.TextField(blank=True, default='')
    draft = models.JSONField(default=dict, blank=True,
                             help_text='Exactly what the machine proposed, kept for audit even '
                                       'after the accountant corrects it.')
    text_preview = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PARSED)
    warnings = models.JSONField(default=list, blank=True,
                                help_text='Duplicate alerts and anything else worth seeing BEFORE '
                                          'the invoice is created.')
    invoice = models.ForeignKey(BonuInvoice, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='documents')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status'], name='bonu_doc_status_idx')]
        verbose_name = 'BONU ingested document'

    def __str__(self):
        return f'{self.filename} ({self.status})'


# ---------------------------------------------------------------------------
# Schedule store — the accountant's BONU workbook, captured in Omni
# ---------------------------------------------------------------------------

class BonuScheduleSheet(AuditableMixin, BaseModel):
    """One tab of the BONU performance workbook, captured verbatim so the
    schedule lives in Omni instead of an emailed Excel file.

    The columns are whatever the sheet carried — stored as an ordered list so
    NO information is dropped. Each data row is a `BonuScheduleRow` whose
    `cells` is keyed by these column labels. `amount_column` names the money
    column (when the sheet has one) so the total can be reconciled against the
    ledger; it is optional because some sheets (e.g. bank details) have none.
    """

    key = models.SlugField(max_length=40, unique=True)
    title = models.CharField(max_length=80)
    columns = models.JSONField(default=list)
    amount_column = models.CharField(max_length=80, blank=True, default='')
    source_note = models.CharField(max_length=200, blank=True, default='')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['order', 'title']
        verbose_name = 'BONU schedule sheet'

    def __str__(self):
        return f'{self.title} ({self.key})'


class BonuScheduleRow(AuditableMixin, BaseModel):
    """One row of a schedule sheet. `cells` holds every column's value keyed by
    the column label — nothing is summarised or dropped. Values are stored as
    strings so a hand-edit round-trips exactly; the reconciliation reads the
    amount column numerically."""

    sheet = models.ForeignKey(BonuScheduleSheet, on_delete=models.CASCADE, related_name='rows')
    position = models.PositiveIntegerField(default=0)
    cells = models.JSONField(default=dict)
    note = models.TextField(blank=True, default='')
    updated_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['sheet', 'position', 'created_at']
        indexes = [models.Index(fields=['sheet', 'position'], name='bonu_schedrow_pos_idx')]
        verbose_name = 'BONU schedule row'

    def __str__(self):
        return f'{self.sheet.key} #{self.position}'


# ---------------------------------------------------------------------------
# The in-house legal office — Alpha Law
#
# The Claims Legal Office reviewed the BONU Legal screens on 18 Aug 2026 and
# reported that what the office actually does every day was not on them. The BONU screens above read the PANEL FIRMS' bills.
# The legal office's own work — matters handled in-house instead of being
# referred out, and external bills argued down before they are paid — had no
# home at all, so it lived in a spreadsheet and a browser's local storage.
#
# These models are that home. Four registers and three reference tables:
#   LegalFeeNote        the matters billed in-house  (Section 1)
#   LegalInvoiceSaving  external bills reviewed and reduced (Section 2)
#   LegalAdvisoryEntry  non-litigious advice to ADI departments (Section 4)
#   LegalMonthlyBonus   the monthly performance bonus, entered per month
#   LegalTariffItem     the internal and external rate cards
#   LegalRateMapping    which internal service equals which external one
#   LegalSettings       the thresholds the two bonuses are measured against
#
# NOTHING HERE POSTS TO THE GENERAL LEDGER, AND NOTHING HERE PAYS ANYBODY. The
# bonus figures are a calculation on screen; the payable amount stays a CFO
# decision and a payroll instruction, exactly as it is today. So no frozen
# figure can move from this module.
#
# MEMBER PRIVACY: `client` names a BONU member, which the CFO decided on
# 11 Aug 2026 may be stored and read inside BONU. It is never sent to an
# external model — this module has no AI in it at all (AD-POL-AI-GOV-001).
# ---------------------------------------------------------------------------


class LegalSettings(BaseModel):
    """The thresholds every bonus figure is measured against. One row.

    Kept in the database rather than in code because the tariff schedule is
    renegotiated and the officer must be able to correct a rate on screen
    without waiting for a release.
    """

    quarterly_threshold = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('50000'),
        help_text='Quarterly saving that must be reached before the quarterly '
                  'bonus becomes payable (BWP).')
    quarterly_bonus_pct = models.DecimalField(
        max_digits=6, decimal_places=4, default=Decimal('0.02'),
        help_text='Fraction of the quarterly saving payable once the threshold '
                  'is met. 0.02 = 2%.')
    monthly_bonus_cap = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('6500'),
        help_text='Ceiling on the monthly performance bonus (BWP).')
    external_hourly_rate = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('2250'),
        help_text='Standard external attorney hourly rate (BWP/hr). Drives every '
                  'hourly comparison and the advisory value. Disbursements '
                  '(email, telephone, copies, mileage) keep their own rates.')
    sla_days = models.PositiveIntegerField(
        default=5,
        help_text='Target working days between receiving an external bill and '
                  'reviewing it.')

    # The per-client legal-spend ceiling (Kelvin Kimani, 9 Sep 2026). Kept here
    # beside the other thresholds for the same reason they are: the figures get
    # renegotiated, and the officer must be able to correct one on screen
    # without waiting for a release.
    client_spend_cap = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('80000'),
        help_text='Legal spend ceiling per CLIENT, aggregated across ALL their matters '
                  '(BWP). One client with three matters shares one ceiling of 80,000 — '
                  'not 80,000 each.')
    client_spend_amber = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('60000'),
        help_text='Early-warning level (BWP). At or above this a client shows amber, '
                  '"approaching cap", so the ceiling is seen coming rather than crossed.')
    cap_blocks_capture = models.BooleanField(
        default=True,
        help_text='ON (the CFO decision of 9 Sep 2026): a bill that would take a client '
                  'past the cap is REFUSED, and needs authorisation before it can be '
                  'recorded. OFF: reaching the cap only turns the flag red and the client '
                  'is monitored. Kept as a switch either way so the policy can change '
                  'without a release. Note the trade-off the CFO accepted: a refused bill '
                  'does not disappear, it just stops being written down here — so if '
                  'bills start going unrecorded, turn this OFF rather than let the cap '
                  'totals drift.')

    class Meta(BaseModel.Meta):
        verbose_name = 'BONU legal office settings'
        verbose_name_plural = 'BONU legal office settings'

    def __str__(self):
        return 'BONU legal office settings'

    @classmethod
    def solo(cls):
        """The single settings row, created with the agreed defaults on first read."""
        row = cls.objects.order_by('created_at').first()
        return row or cls.objects.create()


class LegalTariffItem(AuditableMixin, BaseModel):
    """One line of a rate card — ours or the external panel's."""

    class Scope(models.TextChoices):
        INTERNAL = 'internal', 'Internal (Alpha Law)'
        EXTERNAL = 'external', 'External attorney'

    scope = models.CharField(max_length=10, choices=Scope.choices)
    section = models.CharField(max_length=160, blank=True, default='')
    item = models.CharField(max_length=240)
    unit = models.CharField(max_length=60, blank=True, default='')
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    position = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['scope', 'position', 'item']
        verbose_name = 'BONU legal tariff item'
        verbose_name_plural = 'BONU legal tariff items'

    def __str__(self):
        return f'{self.get_scope_display()} — {self.item}'


class LegalRateMapping(AuditableMixin, BaseModel):
    """Which external-tariff line a fee-note service is compared against.

    Without a mapping a fee-note line simply has no external equivalent and is
    reported as "no mapping" rather than silently counted as a zero saving —
    a missing comparison must never read as "we saved nothing".
    """

    class Basis(models.TextChoices):
        FLAT = 'flat', 'Flat'
        PER_HOUR = 'per_hour', 'Per hour'

    fee_description = models.CharField(max_length=240, unique=True)
    internal_unit = models.CharField(max_length=60, blank=True, default='')
    internal_rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    external_item = models.CharField(max_length=240, blank=True, default='')
    external_unit = models.CharField(max_length=60, blank=True, default='')
    external_rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    calc_basis = models.CharField(max_length=10, choices=Basis.choices, default=Basis.FLAT)
    is_disbursement = models.BooleanField(
        default=False,
        help_text='True for items an external attorney recovers as a disbursement '
                  '(email, telephone). These keep their own rate and are NOT '
                  'moved when the standard hourly rate changes.')
    position = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['position', 'fee_description']
        verbose_name = 'BONU legal rate mapping'
        verbose_name_plural = 'BONU legal rate mappings'

    def __str__(self):
        return self.fee_description


class LegalFeeNote(AuditableMixin, BaseModel):
    """One billed line of in-house legal work (Section 1)."""

    date = models.DateField()
    client = models.CharField(max_length=200, help_text='Client / BONU member.')
    portfolio = models.CharField(max_length=60, default='BONU')
    description = models.CharField(max_length=240)
    unit = models.CharField(max_length=60, blank=True, default='')
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'),
                              help_text='Quantity or hours.')
    updated_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-date', 'client']
        verbose_name = 'BONU legal fee note line'
        verbose_name_plural = 'BONU legal fee note lines'
        indexes = [models.Index(fields=['date'])]

    def __str__(self):
        return f'{self.date} {self.client} — {self.description}'

    @property
    def amount(self) -> Decimal:
        return (self.rate or Decimal('0')) * (self.qty or Decimal('0'))


class LegalInvoiceSaving(AuditableMixin, BaseModel):
    """An external attorney's bill, reviewed and agreed down (Section 2).

    `saving` is the money kept in the business, and it is what the quarterly
    bonus is measured on — so the original and the agreed figure are both kept,
    never just the difference.
    """

    date_received = models.DateField(null=True, blank=True)
    date_reviewed = models.DateField()
    invoice_ref = models.CharField(max_length=120)
    external_attorney = models.CharField(max_length=200, blank=True, default='')
    original_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    agreed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    note = models.TextField(blank=True, default='')
    updated_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-date_reviewed', 'invoice_ref']
        verbose_name = 'BONU legal invoice saving'
        verbose_name_plural = 'BONU legal invoice savings'
        indexes = [models.Index(fields=['date_reviewed'])]

    def __str__(self):
        return f'{self.invoice_ref} — {self.external_attorney}'

    @property
    def saving(self) -> Decimal:
        return (self.original_amount or Decimal('0')) - (self.agreed_amount or Decimal('0'))

    @property
    def turnaround_days(self):
        """Days between receiving the bill and reviewing it, or None if we were
        never told when it arrived — an unknown turnaround must not be counted
        as a met SLA."""
        if not self.date_received or not self.date_reviewed:
            return None
        return (self.date_reviewed - self.date_received).days


class LegalAdvisoryEntry(AuditableMixin, BaseModel):
    """Non-litigious advice given to an Alpha Direct department (Section 4).

    No internal rate applies — this is scope evidence. Its value is shown at the
    external hourly rate purely to say what the same advice would have cost if
    it had been bought outside, and it is labelled illustrative on screen.
    """

    date = models.DateField()
    department = models.CharField(max_length=80, blank=True, default='')
    client = models.CharField(max_length=200, blank=True, default='')
    matter_ref = models.CharField(max_length=120, blank=True, default='')
    description = models.CharField(max_length=240)
    type_of_work = models.CharField(max_length=160, blank=True, default='')
    hours = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0'))
    updated_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-date']
        verbose_name = 'BONU legal advisory entry'
        verbose_name_plural = 'BONU legal advisory entries'
        indexes = [models.Index(fields=['date'])]

    def __str__(self):
        return f'{self.date} {self.department} — {self.description}'


class LegalMonthlyBonus(AuditableMixin, BaseModel):
    """The monthly performance bonus for one month, as entered by the officer.

    Deliberately NOT derived. The monthly bonus comes off a threshold table in
    the officer's contract that Omni does not hold, so computing it here would
    be inventing a number. It is entered, capped, and marked on screen as
    subject to the CFO's confirmation.
    """

    month = models.CharField(max_length=7, unique=True, help_text='YYYY-MM')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    updated_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-month']
        verbose_name = 'BONU legal monthly bonus'
        verbose_name_plural = 'BONU legal monthly bonuses'

    def __str__(self):
        return f'{self.month} — {self.amount}'


# ---------------------------------------------------------------------------
# The membership roll — who we are actually allowed to pay for
#
# CFO 2026-08-18: *"We also need a place where I can put all the member details
# so we only pay claims for members who pay premiums. It's very important."*
#
# BONU has 9,000+ members and pays Alpha Direct TWO payments a month for the
# whole scheme. That bulk arrangement is exactly why per-member paid-up status
# CANNOT be derived from our own cash receipts — two lump sums tell us nothing
# about which of 9,000 people is up to date. It can only come from the union's
# own membership list, which is why this register is built around a DATED LOAD
# of that list rather than around our bank statement.
#
# `bonu/member_rules.py` has said the same thing since 3 Aug: "Still missing,
# and it is the big one: whether that member's premium was actually PAID, and
# whether they were on cover on the day the work was done." This is that.
#
# MEMBER PRIVACY: names ARE stored here. The CFO decided that on 11 Aug 2026 for
# the legal office and confirmed it for this register on 18 Aug 2026 as data
# controller. Names stay INSIDE Omni — never to an external model, never
# exported un-anonymised (AD-POL-AI-GOV-001). No Omang is stored: eligibility
# never needs one, so holding 9,000 of them would be risk with no return.
# ---------------------------------------------------------------------------


class BonuMemberListLoad(AuditableMixin, BaseModel):
    """One dated membership list as the union sent it.

    Eligibility is only ever answered against a specific list, so the list
    itself is a record with an as-at date and a row count. Without this a
    refusal could not be explained: "not a member" is an accusation, whereas
    "not on the list dated 31 July, which had 9,014 members" is a fact somebody
    can go and check.
    """

    as_at = models.DateField(
        db_index=True,
        help_text='The date the union\'s list speaks as of — usually a month end.')
    source_name = models.CharField(
        max_length=300, blank=True, default='',
        help_text='The file the union sent, so a figure can be traced back to it.')
    source_note = models.TextField(blank=True, default='')
    rows_seen = models.PositiveIntegerField(default=0)
    members_loaded = models.PositiveIntegerField(default=0)
    loaded_by_email = models.CharField(max_length=200, blank=True, default='')
    is_current = models.BooleanField(
        default=False, db_index=True,
        help_text='The list eligibility is measured against. Exactly one is current.')

    class Meta(BaseModel.Meta):
        ordering = ['-as_at', '-created_at']
        verbose_name = 'BONU membership list load'

    def __str__(self):
        return f'Membership list at {self.as_at} ({self.members_loaded} members)'


class BonuMember(AuditableMixin, BaseModel):
    """One union member, from the union's own list.

    The register is deliberately NOT a summary. `cells` keeps every column the
    union sent, verbatim, the same way BonuScheduleRow does — because the day a
    refusal is challenged, the answer has to be the union's own row, not our
    interpretation of it.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active — paid up'
        ARREARS = 'arrears', 'In arrears'
        SUSPENDED = 'suspended', 'Suspended'
        RESIGNED = 'resigned', 'Resigned / left the union'
        UNKNOWN = 'unknown', 'Status not stated on the list'

    membership_no = models.CharField(
        max_length=60, unique=True, db_index=True,
        help_text='The union\'s membership number. The one thing every list has.')
    full_name = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Stored by CFO decision 18-Aug-2026. Never leaves Omni.')
    member_token = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='Links to the invoice-side token so per-member spend can be '
                  'totalled without carrying a name around.')

    # Where the member is. This is also what the lawyer allocation needs: the
    # CFO's rule is location FIRST, and today no firm or case carries one.
    station = models.CharField(max_length=200, blank=True, default='',
                               help_text='Hospital / clinic / workplace.')
    district = models.CharField(max_length=120, blank=True, default='', db_index=True,
                                help_text='District or town — used to place a case near a firm.')

    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.UNKNOWN, db_index=True)
    monthly_premium = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    paid_up_to = models.DateField(
        null=True, blank=True,
        help_text='Last period the member\'s premium covers, when the list says so.')
    joined_on = models.DateField(null=True, blank=True)
    left_on = models.DateField(null=True, blank=True)

    first_seen = models.ForeignKey(BonuMemberListLoad, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='first_seen_members')
    last_seen = models.ForeignKey(BonuMemberListLoad, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name='last_seen_members',
                                  help_text='The most recent list this member appeared on. A '
                                            'member missing from the current list is the '
                                            'single most important refusal reason there is.')
    cells = models.JSONField(default=dict, blank=True,
                             help_text='The union\'s own columns, kept verbatim.')
    note = models.TextField(blank=True, default='')
    updated_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['membership_no']
        indexes = [models.Index(fields=['status', 'district'], name='bonu_member_status_idx')]
        verbose_name = 'BONU member'

    def __str__(self):
        return f'{self.membership_no} — {self.full_name or "(no name on the list)"}'

    @property
    def is_on_current_list(self) -> bool:
        return bool(self.last_seen_id and self.last_seen.is_current)


# ---------------------------------------------------------------------------
# Legal bills — capture, allocation to a client, and the cap they feed
#
# Part 2 of Kelvin Kimani's spec (9 Sep 2026). The 80,000 per-client ceiling in
# Part 1 is only as good as the numbers feeding it, and until now there was
# nowhere to enter a legal bill against a matter at all. `LegalInvoiceSaving`
# above is a different thing: it records an external bill ARGUED DOWN, for the
# officer's savings bonus. It never says which client the money was spent on,
# so it cannot answer "has this client passed 60,000".
#
# THE SHAPE, AND WHY
#   LegalBill            the bill as the firm sent it — one total, one reference
#   LegalBillAllocation  which matter (or matters) that total is for
#   LegalClientAlias     a firm's spelling of a client, learned once
#
# A bill is a HEADER PLUS LINES rather than one row carrying one claim, because
# the load-bearing question in the spec is whether a single invoice can span
# several matters. It can — so the amount has to be able to split across them.
# Dumping a whole invoice on one matter charges the wrong client.
#
# The client is deliberately NOT stored on the bill or on the allocation. It is
# read through the matter (`allocation.case.client`), so there is exactly one
# answer to "whose spend is this" and no second copy that can drift from the
# first. The running total is computed by summing allocations, never held as a
# number that can go stale.
#
# NO MONEY MOVES HERE. Recording a bill, allocating it and marking it paid are
# workflow records. Every real payment is authorised by the CFO in the FNB app
# with two-factor. Nothing in this module posts to the general ledger.
#
# MEMBER PRIVACY: the firm's spelling of a client's NAME is stored, because a
# bill arriving with a name and no number is the whole problem being solved.
# The CFO decided on 11 Aug 2026, and confirmed for the member register on
# 18 Aug 2026 as data controller, that member names may be stored and read
# inside BONU. They never leave Omni and never reach an external model
# (AD-POL-AI-GOV-001) — the matching in `bonu/legal_rules.py` is plain string
# work with no AI in it at all.
# ---------------------------------------------------------------------------


class LegalBill(AuditableMixin, BaseModel):
    """One legal bill, as the firm or the in-house office sent it."""

    class Source(models.TextChoices):
        EXTERNAL = 'external', 'External law firm'
        IN_HOUSE = 'in_house', 'In-house (Alpha Law)'

    class Stage(models.TextChoices):
        BILLED = 'billed', 'Billed — not yet paid'
        PAID = 'paid', 'Paid'
        REJECTED = 'rejected', 'Rejected / withdrawn'

    #: Stages that count toward a client's cap. BILLED is included on purpose:
    #: the spec asks for the warning to fire BEFORE payment rather than after,
    #: which is the only way a ceiling can be seen coming. A rejected bill
    #: counts for nothing — it was never a cost.
    COUNTS_TOWARD_CAP = ('billed', 'paid')

    class Allocation(models.TextChoices):
        ALLOCATED = 'allocated', 'Allocated to a client'
        UNALLOCATED = 'unallocated', 'Unallocated — needs a person'

    source = models.CharField(max_length=8, choices=Source.choices, default=Source.EXTERNAL,
                              db_index=True)
    firm = models.ForeignKey(LawFirm, null=True, blank=True, on_delete=models.PROTECT,
                             related_name='legal_bills',
                             help_text='Empty ONLY when the bill is in-house.')
    reference = models.CharField(max_length=120,
                                 help_text='The billing party own bill or invoice number.')
    bill_date = models.DateField()
    received_on = models.DateField(null=True, blank=True,
                                   help_text='When the bill reached us.')
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'),
                                 help_text='The bill total (BWP). Its allocations must '
                                           'account for exactly this.')
    stage = models.CharField(max_length=8, choices=Stage.choices, default=Stage.BILLED,
                             db_index=True)

    # What actually left the bank against this bill. Kept beside the amount
    # because "what were we billed" and "what have we paid" are different
    # questions and the CFO asks the second one: a register that holds only the
    # first cannot answer it, which is why this one arrived as an email
    # attachment instead of a screen.
    discount = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0'),
        help_text='Agreed off the bill before payment (BWP).')
    amount_paid = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal('0'),
        help_text='Cash paid against this bill (BWP). Not derived from the stage — '
                  'the stage is derived from THIS.')
    paid_on = models.DateField(
        null=True, blank=True,
        help_text='When the payment went out. Empty where the source register '
                  'recorded a payment with no readable date — the money is real, '
                  'the month it belongs to is not known.')
    source_row = models.PositiveIntegerField(
        null=True, blank=True, db_index=True,
        help_text='Row number in the imported fee-note register, so any figure here '
                  'can be taken back to the line it came from. Also the import key: '
                  're-running a load updates the row rather than adding a second copy.')

    allocation_state = models.CharField(
        max_length=11, choices=Allocation.choices, default=Allocation.UNALLOCATED,
        db_index=True,
        help_text='Unallocated is an EXCEPTION, not a resting state. A bill that cannot be '
                  'tied to a matter and a client stays visible here until somebody assigns '
                  'it — never silently dropped and never counted against a guessed client, '
                  'because one wrong allocation makes two cap totals wrong at once.')
    billed_client_name = models.CharField(
        max_length=200, blank=True, default='',
        help_text='The client name exactly as the firm wrote it, kept so a match can be '
                  'explained afterwards and so the alias can be learned from it.')
    dup_key = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='Firm plus amount plus reference, normalised (legal_rules.bill_dup_key). '
                  'Used to WARN about a suspected duplicate before it is committed. '
                  'Deliberately NOT unique: a firm can legitimately re-issue, and refusing '
                  'the entry outright would push the bill out of Omni.')
    note = models.TextField(blank=True, default='')
    captured_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-bill_date', 'reference']
        constraints = [
            # Same discipline as LegalCase: source and firm must agree, in the
            # database rather than in a form.
            models.CheckConstraint(
                check=(models.Q(source='external', firm__isnull=False)
                       | models.Q(source='in_house', firm__isnull=True)),
                name='bonu_bill_firm_matches_source'),
            models.CheckConstraint(check=models.Q(amount__gt=0),
                                   name='bonu_bill_amount_positive'),
            # The import keys on source_row, so two rows carrying the same one
            # would make `update_or_create` raise instead of updating. Bills
            # captured by hand have no source_row at all, hence the condition.
            models.UniqueConstraint(
                fields=['source_row'], condition=models.Q(source_row__isnull=False),
                name='bonu_bill_one_per_source_row'),
        ]
        indexes = [
            models.Index(fields=['allocation_state', 'stage'], name='bonu_bill_state_idx'),
            models.Index(fields=['bill_date'], name='bonu_bill_date_idx'),
        ]
        verbose_name = 'BONU legal bill'

    def __str__(self):
        return f'{self.reference} — {self.biller} {self.amount}'

    @property
    def biller(self) -> str:
        if self.source == self.Source.IN_HOUSE:
            return 'In-house'
        return self.firm.name if self.firm_id else '(no firm set)'

    @property
    def allocated_total(self) -> Decimal:
        """What the allocations actually account for — summed, never stored."""
        return sum((a.amount for a in self.allocations.all()), Decimal('0'))

    @property
    def outstanding(self) -> Decimal:
        """Still owed on this bill — computed, never stored, so it cannot drift
        away from the three figures it comes from."""
        return (self.amount or Decimal('0')) - (self.discount or Decimal('0')) \
            - (self.amount_paid or Decimal('0'))

    @property
    def counts_toward_cap(self) -> bool:
        return self.stage in self.COUNTS_TOWARD_CAP


class LegalBillAllocation(BaseModel):
    """The share of one bill that belongs to one matter.

    One row for a single-matter bill; several rows for a firm invoice covering
    a few matters. The client is NOT held here — it is read through
    `case.client`, so a matter re-linked to the correct client corrects its
    billing history too, instead of leaving a stale copy behind.
    """

    bill = models.ForeignKey(LegalBill, on_delete=models.CASCADE, related_name='allocations')
    case = models.ForeignKey(LegalCase, on_delete=models.PROTECT,
                             related_name='bill_allocations')
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))

    class Meta(BaseModel.Meta):
        ordering = ['case__case_ref']
        constraints = [
            # One line per matter per bill. Two lines for the same matter is
            # double-counting inside a single bill — the same error the
            # duplicate guard catches between bills.
            models.UniqueConstraint(fields=['bill', 'case'], name='bonu_uniq_bill_case'),
            models.CheckConstraint(check=models.Q(amount__gt=0),
                                   name='bonu_bill_alloc_positive'),
        ]
        verbose_name = 'BONU legal bill allocation'

    def __str__(self):
        return f'{self.bill_id} to {self.case_id}: {self.amount}'


class LegalClientAlias(AuditableMixin, BaseModel):
    """A law firm spelling of a client name, learned the first time.

    Once a person has tied a name-only bill to a client, the firm version of
    the name is recorded here — so the next bill from that firm for that client
    matches on its own. The system learns the spelling once instead of asking
    every time.

    `alias_key` is unique ACROSS ALL CLIENTS on purpose. If two different
    clients could hold the same alias, the alias would itself be ambiguous and
    would auto-match a bill onto whichever row happened to be found first. The
    second attempt to claim a name is refused, and a bill carrying that name
    stays a decision for a person — which is the correct answer, not a
    limitation.
    """

    client = models.ForeignKey('BonuMember', on_delete=models.CASCADE,
                               related_name='name_aliases')
    alias_raw = models.CharField(max_length=200,
                                 help_text='The name exactly as the firm wrote it.')
    alias_key = models.CharField(
        max_length=200, db_index=True,
        help_text='The normalised spelling (bonu/member_identity.normalise) — uppercased, '
                  'punctuation stripped, words sorted, so surname-first still matches.')
    firm = models.ForeignKey(LawFirm, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='client_aliases',
                             help_text='Which firm writes it this way, when we know.')
    learned_by_email = models.CharField(max_length=200, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['alias_raw']
        constraints = [
            models.UniqueConstraint(fields=['alias_key'], name='bonu_uniq_client_alias_key'),
        ]
        verbose_name = 'BONU legal client name alias'

    def __str__(self):
        return f'{self.alias_raw} -> {self.client_id}'
