"""
taskboard/models.py

Reminder engine + enforced completion for Omni's EXISTING task model,
core.OmniTask. Phase 1 of the meeting-discipline programme.

Omni already ships core.OmniTask (assigner / assignee / title / body / due_at /
priority / status / completed_at / seen_at) and core.OmniTaskComment. Rather than
duplicate that, this app ADDS only the two pieces the meeting-discipline brief
needs that do not exist yet:

  Notification    assign-day (one gentle toast) / due-day / overdue reminders that
                  drive the non-dismissible force-action modal.
  CompletionNote  enforced note captured on completion — minimum dwell time AND
                  minimum characters, validated SERVER-SIDE so editing the DOM
                  cannot bypass the gate.

The due-day/overdue sweep (cron -> manage.py sweep_task_reminders), the live push,
the completion endpoint, and the dashboards are built on top of these models in
later PRs (see .claude/specs/taskboard/design.md). carry_over_count / meeting
links belong to the Phase-2 meeting layer and are deferred.

DPA note: per-person completion rate + interaction_seconds are employee monitoring
under the Botswana Data Protection Act 2024 -> gated on a DPIA + staff privacy
notice before go-live (see requirements.md).
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models

from core.models import BaseModel, OmniTask


def money_dec(v) -> Decimal:
    """Read a money value as Decimal, rounded HALF UP to the thebe.

    THE ONE money parser for this module. taskboard.payment_views._dec delegates
    here on purpose: a reconciliation guard that parses amounts differently from
    the total it checks is not a guard at all — two parsers is precisely how the
    P90k cap was bypassed. Decimal's own default is banker's rounding, which
    silently turns 10.005 into 10.00, so HALF UP is stated explicitly (house
    rule, CFO 2026-08-09).
    """
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0.00')


class Notification(BaseModel):
    """Reminder record for a core.OmniTask.

    assign_day = one gentle, dismissible toast; due_day / overdue drive the
    non-dismissible force-action modal until the task is completed.
    """

    class Type(models.TextChoices):
        ASSIGN_DAY = "assign_day", "Assigned"
        DUE_DAY = "due_day", "Due today"
        OVERDUE = "overdue", "Overdue"

    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="task_notifications"
    )
    task = models.ForeignKey(
        OmniTask, on_delete=models.CASCADE, related_name="reminders"
    )
    type = models.CharField(max_length=12, choices=Type.choices)
    seen_at = models.DateTimeField(null=True, blank=True)
    acknowledged = models.BooleanField(
        default=False,
        help_text="True once the user clears the force-modal (due/overdue).",
    )

    class Meta(BaseModel.Meta):
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "acknowledged"]),
            models.Index(fields=["task", "type"]),
        ]

    def __str__(self):
        return f"{self.get_type_display()} -> user {self.recipient_id} (task {self.task_id})"


class PaymentLoadOverride(BaseModel):
    """PAY-WIN-02 (CFO 2026-09-01): a request from a non-CFO user to LOAD a
    payment OUTSIDE the 08:00-09:15 morning loading window, plus the CFO's
    decision on it.

    Every row is the audit trail of who tried to load off-window and why - the
    CFO asked for this precisely so he can see who does not follow the loading
    discipline. An APPROVED row whose ``for_date`` is today lets that user load
    for the rest of that same local day; a PENDING row is just a request waiting
    for him. Nothing here moves money - loading a payment is only a workflow
    record; the CFO still authorises the real payment himself in FNB.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for CFO"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"

    requested_by = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="payment_load_overrides"
    )
    reason = models.TextField(
        help_text="Why they need to load a payment outside the morning window."
    )
    for_date = models.DateField(
        help_text="The local day this override is (or would be) valid for."
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payment_load_overrides_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=["requested_by", "for_date", "status"]),
            models.Index(fields=["status", "for_date"]),
        ]

    def __str__(self):
        return (f"load override {self.for_date} user {self.requested_by_id} "
                f"[{self.status}]")


class TaskReminderEmailLog(BaseModel):
    """One row per (recipient, calendar date) that the daily task-reminder
    email actually went out.

    Makes services.email_open_reminders() idempotent for the day. The sweep
    command documents itself as "safe to run several times a day", but that was
    only ever true for the in-app Notification rows — the EMAIL had no guard, so
    a duplicated /etc/cron.d entry, a manual re-run, or any re-trigger resent the
    same 'N task(s) due or overdue' digest (Pramod, Sr IT, 2026-07-24: the
    reminder was landing 5-6x/day, a duplicate even in the same folder).

    Mirrors the LeaveExcuseAutoResponse sent-ledger pattern. The unique
    constraint is what enforces one-send-per-person-per-day even under two
    concurrent runs firing in the same minute.
    """

    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="task_reminder_email_logs"
    )
    sent_on = models.DateField(help_text="Local date the digest was emailed.")

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "sent_on"],
                name="uniq_task_reminder_email_per_day",
            ),
        ]
        indexes = [models.Index(fields=["recipient", "sent_on"])]

    def __str__(self):
        return f"task reminder email -> user {self.recipient_id} on {self.sent_on}"


class CompletionNote(BaseModel):
    """Enforced note captured when an OmniTask is marked done.

    The note-length gate is validated SERVER-SIDE in the complete endpoint (never
    trust the DOM): len(body) >= MIN_NOTE_CHARS. interaction_seconds is still
    recorded for audit, but the 30s dwell gate was removed (CFO 2026-07-18).
    """

    MIN_DWELL_SECONDS = 0  # dwell gate removed (CFO 2026-07-18); seconds still recorded
    # CFO 2026-07-24: marking a task Done must NOT force a note — a simple Done
    # should complete in one tap, like the reject path lets you comment or not.
    # The note field stays (optional, useful for the payment presets); only the
    # hard length gate is dropped. 0 = optional.
    MIN_NOTE_CHARS = 0

    task = models.OneToOneField(
        OmniTask, on_delete=models.CASCADE, related_name="completion_note"
    )
    author = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="task_completion_notes"
    )
    body = models.TextField()
    interaction_seconds = models.PositiveIntegerField(
        default=0,
        validators=[MinValueValidator(MIN_DWELL_SECONDS)],
        help_text="Measured dwell time on the completion modal (kept for audit).",
    )

    def __str__(self):
        return f"Completion note for OmniTask {self.task_id}"


class WhatsAppContact(BaseModel):
    """CFO-only staff phonebook for WhatsApp reminders (CFO directive 2026-07-14).

    A lightweight, copy-paste phonebook the CFO controls himself — deliberately
    SEPARATE from HRIS employee data (which carries payroll). Numbers are visible
    only to CFO-authority users (same gate as the Secrets Vault). Used to fire
    WhatsApp reminders at staff — especially managers — who miss task deadlines.
    """

    name       = models.CharField(max_length=160)
    phone      = models.CharField(max_length=32,
                                  help_text="E.164, e.g. +2677xxxxxxx (8-digit local numbers auto-prefixed 267).")
    role       = models.CharField(max_length=120, blank=True, default='',
                                  help_text="Free text, e.g. 'Claims Manager'.")
    is_manager = models.BooleanField(default=False,
                                     help_text="Flag managers so reminders can target them first.")
    active     = models.BooleanField(default=True)
    notes      = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name="whatsapp_contacts_created")

    class Meta(BaseModel.Meta):
        ordering            = ['-is_manager', 'name']
        verbose_name        = 'WhatsApp Contact'
        verbose_name_plural = 'WhatsApp Contacts'

    def __str__(self):
        return f"{self.name} ({self.phone})"


class WhatsAppMessage(BaseModel):
    """Audit log of every reminder sent (or attempted). Keeps a record of who
    was reminded, when, by whom, and whether the send succeeded."""

    class Status(models.TextChoices):
        SENT     = 'sent',     'Sent'
        # Meta returned HTTP 200 but delivery is NOT guaranteed — a free-form
        # message outside the recipient's 24h reply window is accepted and then
        # silently discarded. Recording those as SENT is how a total WhatsApp
        # outage stayed invisible in Graphite V2 for months (handover 2026-08-03).
        ACCEPTED = 'accepted', 'Accepted by WhatsApp (delivery not confirmed)'
        FAILED   = 'failed',   'Failed'
        QUEUED   = 'queued',   'Queued'

    to_name     = models.CharField(max_length=160, blank=True, default='')
    to_phone    = models.CharField(max_length=32)
    body        = models.TextField()
    status      = models.CharField(max_length=8, choices=Status.choices,
                                   default=Status.QUEUED)
    provider_id = models.CharField(max_length=160, blank=True, default='',
                                   help_text="Message id returned by the WhatsApp API.")
    error       = models.TextField(blank=True, default='')
    task        = models.ForeignKey(OmniTask, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="whatsapp_reminders")
    sent_by     = models.ForeignKey(User, null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name="whatsapp_messages_sent")

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']

    def __str__(self):
        return f"WhatsApp to {self.to_phone} [{self.status}]"


class PaymentRequest(BaseModel):
    """A payment authorisation request raised by Finance and routed to the CFO
    for payment (CFO 2026-07-15 "task issues" — stop the authorisation emails).

    Staff fill a structured form; the system renders the exact authorisation
    table (deterministic, matches the format the team already emails) and drops
    it into the CFO's task inbox as an OmniTask he can pay or reassign (e.g.
    escalate above his limit to the CEO). No customer PII: payees are vendors /
    beneficiaries, bank details are Alpha Direct's own accounts.
    """

    class Category(models.TextChoices):
        CLAIM      = 'claim',      'Claim payments'
        SUPPLIER   = 'supplier',   'Supplier payments'
        VENDOR     = 'vendor',     'Vendor payments'
        PETTY_CASH = 'petty_cash', 'Petty cash'
        # CFO 2026-08-11: premium refunds are neither operational nor claims, and
        # they were being typed twice — once into the Graphite refund module and
        # again here by hand. They now arrive on their own tab, raised by the
        # overnight importer from the refunds Finance already approved in Graphite
        # (taskboard/management/commands/import_graphite_refunds.py).
        PREMIUM_REFUND = 'premium_refund', 'Premium refunds'
        # B8 (CFO spec 2026-09-13): Refund is a THIRD top-level payment type
        # beside claims and operations — not a category hiding inside
        # operations, where a reversal was being counted as an expense. These
        # two ARE hand-raised; premium refunds above are not, and that
        # difference is the whole control (see IMPORTER_ONLY_CATEGORIES).
        ERRONEOUS_REFUND = 'erroneous_refund', 'Refund — erroneous payment'
        EXCESS_REFUND    = 'excess_refund',    'Excess refunds'
        UNICOIN    = 'unicoin',    'Unicoin payments'
        # A staff loan disbursement (CFO 2026-09-15). Deliberately its OWN
        # category rather than filed under supplier/vendor: the payee is an
        # employee, not a vendor, and the commission run taught us what happens
        # when a payment type hides inside a category that means something else.
        STAFF_LOAN = 'staff_loan', 'Staff loan disbursement'
        QUANTUM    = 'quantum',    'Quantum payments'
        RSA        = 'rsa',        'Risk Software Africa'
        VERITAS    = 'veritas',    'Veritas Capital Mgmt'
        ADH        = 'adh',        'Alpha Direct Health'
        GCE        = 'gce',        'GCE payments'
        OTHER      = 'other',      'Other'

    #: The Graphite refund this request came from, e.g. RFND-000012. Set ONLY by
    #: the overnight importer, and UNIQUE when set: a refund that has already
    #: become a payment request can never become a second one. This is the guard
    #: the duplicate-payment incident of 2026-08-09 was missing — there, eleven
    #: groups were paid twice because nothing tied a payment back to its source.
    # 64, matching customer_refunds.CustomerRefund.graphite_ref. They disagreed
    # (40 here, 64 there) until 2026-09-11: a 41-64 character reference raised a
    # DataError inside the shared builder, which the hand-off's best-effort
    # catch logged and swallowed — the refund would reach FNB while its request
    # silently never existed. An idempotency key must be the same width on both
    # sides of the join or it is not one key.
    graphite_ref = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='Graphite refund reference this request was raised from.')

    #: The payment this request reverses — the bank reference, payment-request
    #: reference or receipt number of the money that actually went out. REQUIRED
    #: on a hand-raised refund (PAY-REFUND-02), enforced on the server so the
    #: form is not the only thing holding it.
    original_payment_ref = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='The reference of the payment being refunded.')

    #: B7 — the reference of the request this one was copied from. The copy is a
    #: brand-new DRAFT with its own reference that goes through the ordinary
    #: create path (and so through every control on it); this line is the audit
    #: trail saying where it came from. The SOURCE is never touched.
    duplicated_from_ref = models.CharField(
        max_length=48, blank=True, default='',
        help_text='The payment request this one was copied from.')

    #: Categories no human raises by hand — the importer owns them, so the create
    #: endpoint refuses them and nobody can hand-key a refund alongside the feed.
    IMPORTER_ONLY_CATEGORIES = ('premium_refund',)

    #: The three kinds of refund. They are one top-level payment type on the
    #: raise screen and one tab on the register — a refund reverses money that
    #: already went out, it is not an operational expense.
    REFUND_CATEGORIES = ('premium_refund', 'erroneous_refund', 'excess_refund')

    #: The refunds a person may raise. Premium refunds are deliberately absent:
    #: the overnight importer owns those (CFO 2026-08-11). Each of these two
    #: MUST carry the reference of the payment being reversed — a refund with no
    #: original payment is not a refund, it is a payment with a nicer name, and
    #: nothing then stops the same amount being refunded twice.
    HAND_RAISED_REFUND_CATEGORIES = ('erroneous_refund', 'excess_refund')

    # Claims vs operations (CFO 2026-07-29) — the raiser is asked this FIRST,
    # because a repairer's invoice settled through claims payable was being
    # raised as a claim and so escaped the supplier terms gate entirely.
    # Answering "claims" now forces the second question below, and claims are
    # only valid for ADIC (the licensed insurer) — no other group company
    # settles claims.
    CLAIMS_ONLY_ENTITY_CODE = 'ADIC'

    class ClaimPayeeType(models.TextChoices):
        """Who is actually receiving a claims payment."""
        CLIENT   = 'client',   'The client / policyholder direct'
        PROVIDER = 'provider', 'A supplier, repairer or service provider'

    # Two-stage authorisation (CFO 2026-07-23): a request must be signed off by
    # a finance approver (Pako / Kago / Legakwa) BEFORE it reaches the CFO's
    # view. It only becomes a CFO task once one of them approves.
    #
    # Terminal states (bug ktshutlhedi 2026-07-25): a request that has been paid
    # or cleared must LEAVE the queue. Previously the only states were the two
    # pending ones + rejected, so a request the CFO paid stayed 'pending_cfo'
    # for ever (payment happens on the linked task, which never fed back here).
    class Status(models.TextChoices):
        # DRAFT (CFO handover 2026-09-02): a request being PREPARED — filled from a
        # dropped invoice or half-typed. It is editable/deletable and enters NO
        # approval or FNB path until the raiser submits it. Everything below is a
        # submitted request.
        DRAFT           = 'draft',           'Draft — not yet submitted'
        # EXCEPTION (CFO 2026-09-02): a submitted request that tripped a
        # fraud-risk control (a changed bank account, or anything deemed
        # possible fraud). It is NEVER a dead-end — the raiser is told it has
        # gone to the committee, and a three-of-six committee sign-off moves it
        # on (approve → pending_finance; reject → rejected). The CFO is NOT a
        # gate here; his clear is records-only. Entering a payment is never
        # blocked — that is the whole point ("we are not blocking people from
        # entering the payments; it should be able to make a payment").
        EXCEPTION       = 'exception',       'Exception — with the committee'
        PENDING_FINANCE = 'pending_finance', 'Pending finance sign-off'
        PENDING_CFO     = 'pending_cfo',     'Pending CFO authorisation'
        REJECTED        = 'rejected',        'Rejected at finance sign-off'
        PAID            = 'paid',            'Paid / authorised'
        CANCELLED       = 'cancelled',       'Cleared / cancelled'

    # A request in one of these no longer needs action — it drops out of the
    # active queue (still reachable via the "show cleared" history view).
    TERMINAL_STATUSES = ('rejected', 'paid', 'cancelled')

    ref             = models.CharField(max_length=48, unique=True)
    entity          = models.CharField(max_length=120,
                                       default='Alpha Direct Insurance Company')
    # What the payment is for — drives the task title so the CFO can triage
    # (petty cash / vendor / supplier / claim) at a glance (CFO 2026-07-15).
    # 20, not 12: 'premium_refund' is 14 characters and the old width could not
    # hold it (fields.E009 on makemigrations).
    category        = models.CharField(max_length=20, choices=Category.choices,
                                       blank=True, default='')
    # Required when category=CLAIM. 'provider' means a third party is billing us
    # on the claim (panel beater, parts supplier, hospital) — that pack carries
    # a real invoice and is gated exactly like a supplier payment. 'client'
    # means the settlement goes straight to the policyholder, where there is no
    # third-party invoice to date or age, so the terms gate does not apply.
    claim_payee_type = models.CharField(max_length=10, blank=True, default='',
                                        choices=ClaimPayeeType.choices)
    currency        = models.CharField(max_length=3, default='BWP',
                                       help_text='BWP / ZAR / USD / INR')
    subject         = models.CharField(max_length=200)
    # ── The POP inherits the request SUBJECT (Kelvin Kimani spec 2026-09-08) ──
    # The proof of payment used to take its own heading, typed or defaulted
    # separately from the request it came out of, so a POP and its request could
    # read as two unrelated documents. It now inherits SUBJECT — and the value
    # is STAMPED here at submission rather than read live, because the spec is
    # explicit that the POP carries the subject "as it stood at submission":
    # amending the subject afterwards must never silently retitle a POP that has
    # already gone out. Blank on the 100-odd requests raised before this existed;
    # pop_heading() falls back for those, so no old row reads as untitled.
    pop_subject     = models.CharField(
                          max_length=200, blank=True, default='',
                          help_text='The POP heading, copied from SUBJECT as it '
                                    'stood when the request was submitted. Falls '
                                    'back to the request reference.')
    payee           = models.CharField(max_length=200, blank=True, default='')
    # ── How the money should actually leave (Kelvin Kimani spec 2026-09-08) ──
    # A request can hold several invoice lines for one supplier — the E.G
    # Couriers example carries 8 lines under one payee — and those lines were
    # always presented as a single lump with no statement of how the money
    # should leave. The inputter now records ONE decision, and the approver and
    # whoever keys the bank both see the intent:
    #   BULK       one payment for the supplier total, settling all invoices.
    #   INDIVIDUAL one payment per invoice, each tied to its own invoice number.
    # It is a GROUPING decision over lines that already exist — amounts never
    # change, and both methods must sum to exactly the same TOTAL PAYABLE
    # (see reconciliation_error below, which is enforced, not advisory).
    class ProcessingMethod(models.TextChoices):
        BULK       = 'bulk',       'Bulk — one payment for the total'
        INDIVIDUAL = 'individual', 'Individual — one payment per invoice'

    processing_method = models.CharField(
                            max_length=10, choices=ProcessingMethod.choices,
                            default=ProcessingMethod.BULK,
                            help_text='How the money leaves: one bulk payment, '
                                      'or one payment per invoice.')
    # [{description, gl_code, ref, amount}] — one row per payment line.
    line_items      = models.JSONField(default=list)
    total           = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    # Drop Box (CFO handover 2026-09-02): the invoice file a DRAFT was read from,
    # and the fields the reader was unsure about — the raiser confirms these
    # before submitting. Empty on a hand-typed request. Never used once submitted.
    draft_source_file = models.CharField(max_length=255, blank=True, default='')
    draft_needs_check = models.JSONField(default=list, blank=True)
    # The amount the invoice reader found (Drop Box safety catch, PAY-AMT-01):
    # the entered amount is compared to this and a mismatch is flagged on submit.
    # Null = hand-typed / not read. Never used once submitted.
    draft_read_amount = models.DecimalField(max_digits=16, decimal_places=2,
                                            null=True, blank=True)
    account_name    = models.CharField(max_length=120, blank=True, default='')
    # ── Enough detail to pay it, captured ONCE (CFO 2026-08-20) ───────────────
    # "the team loads the payment request and then they load the payments again
    # in FNB … create a place where they can enter the bank account details in
    # the payment request itself therefore they do not duplicate the work."
    #
    # bank_name and account_number already existed. These two did not, and FNB
    # cannot be paid without a branch: creditorAgent.branchId is mandatory in
    # RMB spec V-03 section 1.6. Blank branch falls back to the universal
    # FNB-to-FNB branch, which is right for an FNB payee and wrong for anyone
    # else — so it is asked for rather than assumed.
    branch_code                  = models.CharField(
                                       max_length=20, blank=True, default='',
                                       help_text='Payee branch code. Needed for '
                                                 'any bank other than FNB.')
    account_type                 = models.CharField(
                                       max_length=10, blank=True, default='',
                                       choices=[('CACC', 'Cheque / current'),
                                                ('SVGS', 'Savings')],
                                       help_text='Blank is treated as cheque / '
                                                 'current.')

    # ── The link to the bank load, so nobody types it twice ──────────────────
    # Set when finance signs the request off and Omni loads it into FNB. The
    # money still does not move here: it lands in the CFO's FNB queue and he
    # authorises it on his phone with two-factor (CFO 2026-08-20). These fields
    # exist so the load is visible, cannot happen twice, and a failure is
    # readable instead of silent.
    payment                      = models.ForeignKey(
                                       'payments.Payment', null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name='payment_requests')
    fnb_batch                    = models.ForeignKey(
                                       'fnb.FNBBatchSubmission', null=True, blank=True,
                                       on_delete=models.SET_NULL,
                                       related_name='payment_requests')
    fnb_loaded_at                = models.DateTimeField(null=True, blank=True)
    # ── What kind of payment this is, which decides the wording ───────────
    # Finance, 2026-08-20: "Three separate defaults, driven by payment type:
    # ex-gratia, AOL, CIL, repair, third party, refund, supplier invoice."
    # Refund is split into client and internal because they gave those two
    # different formats, and OTHER exists for the gap they named — reinsurance,
    # payroll and statutory payments, which they asked to type by hand.
    # The wording itself, decided on the REQUEST so it is what the approver
    # reads and what the bank is later told — one source, not two.
    bank_narration               = models.CharField(
                                       max_length=140, blank=True, default='',
                                       help_text='What the payee sees on their '
                                                 'statement. 140 characters.')
    bank_our_reference           = models.CharField(
                                       max_length=35, blank=True, default='',
                                       help_text='Our own reference for matching '
                                                 'the bank line back. 35 chars.')
    bank_payment_type            = models.CharField(
                                       max_length=20, blank=True, default='',
                                       help_text='Decides the default wording '
                                                 'on the bank statement.')

    # Why this payee's bank account differs from the one we paid last time
    # (PAY-BANK-01, CFO 2026-08-20). Kept because a changed supplier account is
    # the classic invoice fraud, and the answer is the audit trail.
    bank_change_reason           = models.TextField(
                                       blank=True, default='',
                                       help_text='Required when the payee bank '
                                                 'account differs from the one '
                                                 'last used for them.')
    fnb_load_error               = models.TextField(
                                       blank=True, default='',
                                       help_text='Why the automatic load did not '
                                                 'happen. Blank means it did, or '
                                                 'was not attempted yet.')
    account_number  = models.CharField(max_length=64,  blank=True, default='')
    bank_name       = models.CharField(max_length=120, blank=True, default='')
    opening_balance = models.DecimalField(max_digits=16, decimal_places=2,
                                          null=True, blank=True)
    due_date        = models.DateField(null=True, blank=True)
    # ── Supplier payment terms control (CFO 2026-07-28) ───────────────────────
    # A supplier invoice raised in July was settled in July at full value while
    # the supplier book was unpaid and an offshore settlement discount was
    # available. The pack showed a reference and an amount only, so nobody could
    # see the invoice wasn't due. For category=SUPPLIER the raiser must now give
    # the invoice number, invoice date, agreed term and due date on EVERY line
    # (enforced in taskboard.payment_views._validate_supplier_terms), and:
    #   payment_date          — the date the money actually leaves. Must not be
    #                           before the earliest due date on the request.
    #   early_payment_reason  — the ONLY way to settle early, and it is written
    #                           on the record instead of buried in an email.
    #   funds_already_moved   — declares that cash was transferred to fund this
    #                           request BEFORE it was authorised, so the
    #                           approver sees the money already moved.
    # Claims settlements and recurring operational payments are NOT gated.
    payment_date         = models.DateField(null=True, blank=True)
    early_payment_reason = models.TextField(blank=True, default='')
    funds_already_moved  = models.BooleanField(default=False)
    # ── Duplicate payment control (CFO 2026-08-03) ────────────────────────────
    # PAY-DUP-01. Ten requests worth BWP 789,626.85 were sitting in the CFO's
    # queue and BWP 219,600.10 of them was already paid or double-counted —
    # one request was a strict subset of another, and five lines had been settled
    # days earlier. Nothing checked. Every line is now matched on reference AND
    # amount against every live and paid request (taskboard.payment_duplicates).
    #   duplicate_override_reason — the ONLY way past a hard clash. Written on
    #                               the record and printed on the pack, so an
    #                               override is a decision on the file.
    #   duplicate_matches         — the clashes as they stood when the request
    #                               was raised, kept for the audit trail even
    #                               after the other request changes state.
    #   2026-08-09: 25 characters of free text was the ONLY thing standing
    #   between a known duplicate and the money. Claim G2026004368, ARCON CRAFTS,
    #   BWP 13,662.67 was correctly detected on PAY/ADIC/2026/08/07/0004 as
    #   already carried on PAY/ADIC/2026/07/28/0001 — and BOTH are marked paid.
    #   The person who created the duplicate cleared their own warning.
    #   An override now needs three things, not one: a reason from a fixed list,
    #   a document, and a second person who is not the raiser.
    duplicate_override_reason = models.TextField(blank=True, default='')
    duplicate_matches         = models.JSONField(default=list, blank=True)

    class OverrideCategory(models.TextChoices):
        BANK_REJECTED   = 'bank_rejected',   'The bank rejected the first attempt'
        PARTIAL         = 'partial',         'Partial payment of a larger amount'
        SEPARATE        = 'separate_invoice', 'Genuinely a separate invoice'
        PRIOR_CANCELLED = 'prior_cancelled', 'The earlier request was cancelled'
        OTHER           = 'other',           'Other — explain in the reason'

    duplicate_override_category = models.CharField(
        max_length=32, blank=True, default='', choices=OverrideCategory.choices,
        help_text='Why this duplicate may be paid. Free text alone is not enough.')
    duplicate_override_evidence = models.ForeignKey(
        'taskboard.PaymentRequestAttachment', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='override_evidence_for',
        help_text='The document proving the override — e.g. the bank rejection advice.')
    duplicate_override_approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='duplicate_overrides_approved',
        help_text='Must NOT be the person who raised the request.')
    duplicate_override_approved_at = models.DateTimeField(null=True, blank=True)
    inputter        = models.CharField(max_length=160, blank=True, default='')
    verifier        = models.CharField(max_length=160, blank=True, default='')
    # Covering summary written by the AI (reasoning_complete); deterministic
    # fallback if no engine is configured.
    summary         = models.TextField(blank=True, default='')
    # The exact authorisation table (house HTML) shown to the CFO.
    formatted_html  = models.TextField(blank=True, default='')
    created_by      = models.ForeignKey(User, null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='payment_requests_created')
    task            = models.ForeignKey(OmniTask, null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='payment_request')

    # Two-stage authorisation state (CFO 2026-07-23).
    status            = models.CharField(max_length=16, choices=Status.choices,
                                         default=Status.PENDING_FINANCE, db_index=True)
    # PAY-WIN-02 window abolished (CFO 2026-09-02): raising a payment outside the
    # old 08:00-09:15 morning window is no longer blocked. A non-CFO who does it
    # is flagged here, and the count surfaces as a "did not plan the payment load
    # in time" concern in that person's monthly performance feedback. This moves
    # no money and never blocks; the CFO is exempt (never flagged).
    loaded_off_window = models.BooleanField(default=False, db_index=True)
    # Stage 1 — the finance approver (Pako / Kago / Legakwa) who signed off.
    first_approver    = models.ForeignKey(User, null=True, blank=True,
                                          on_delete=models.SET_NULL,
                                          related_name='payment_requests_first_approved')
    first_approved_at = models.DateTimeField(null=True, blank=True)
    # Set only when a finance approver rejects at stage 1.
    rejected_by       = models.ForeignKey(User, null=True, blank=True,
                                          on_delete=models.SET_NULL,
                                          related_name='payment_requests_rejected')
    rejected_at       = models.DateTimeField(null=True, blank=True)
    decision_notes    = models.TextField(blank=True, default='')

    # ── Exception → committee (CFO 2026-09-02) ───────────────────────────────
    # Set when a submitted request tripped a fraud-risk control and was routed
    # to the committee instead of dead-ending the raiser. The committee (three
    # of six, never the raiser, never the CFO) decides; MONEY NEVER MOVES here —
    # FNB + the CFO's own 2-factor stay the real gate. See the EXCEPTION status.
    exception_control  = models.CharField(max_length=20, blank=True, default='',
                                          help_text='e.g. PAY-BANK-01')
    exception_reason   = models.TextField(blank=True, default='',
                                          help_text='the plain message shown to the raiser')
    exception_raised_at = models.DateTimeField(null=True, blank=True)
    # The committee's decision. Blank while still with the committee.
    exception_decision  = models.CharField(
        max_length=8, blank=True, default='',
        choices=[('approve', 'Approved by committee'), ('reject', 'Rejected by committee')])
    exception_decided_at = models.DateTimeField(null=True, blank=True)
    # The CFO's records-only sign-off. NON-BLOCKING: never gates the payment.
    exception_cleared_by = models.ForeignKey(User, null=True, blank=True,
                                             on_delete=models.SET_NULL,
                                             related_name='payment_exceptions_cleared')
    exception_cleared_at = models.DateTimeField(null=True, blank=True)

    # ── Cancel, pre-sign-off (Kelvin Kimani spec 2026-09-08) ─────────────────
    # "Cancel never hard-deletes. A cancelled line or request is marked
    # cancelled and retained with its full detail, who cancelled it, when, and
    # why — never erased." A pulled payment is exactly what an auditor later
    # asks "why" about, so the reason is captured explicitly rather than left
    # to the before/after log. Attribution is pulled from the logged-in user,
    # never typed. The existing CANCELLED status carries the state; these three
    # carry the account of it.
    cancelled_reason = models.TextField(
                           blank=True, default='',
                           help_text='Why this request was pulled. Required on a '
                                     'cancel; an auditor asks this first.')
    cancelled_by     = models.ForeignKey(
                           User, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name='payment_requests_cancelled')
    cancelled_at     = models.DateTimeField(null=True, blank=True)

    # ── Same-request idempotency (CFO 2026-09-14: "a retry, a double click or
    # a re-sent message cannot create the same record twice") ────────────────
    # A one-off key the raising client generates once per submit attempt and
    # keeps sending unchanged on every retry of THAT click. Blank on every
    # request raised before this existed and on any client that does not send
    # one — a check-then-write in Python cannot close this gap (that is
    # exactly the race a double-click creates), so the real guard is the
    # partial UNIQUE index below, mirroring
    # paymentrequest_one_per_graphite_refund immediately above and payroll's
    # uniq_orchestration_running_per_period_company: a NON-NULL mirror column
    # (blank string, never NULL) with a condition of `__gt=''`, because NULLs
    # stay distinct under a unique index and would guard nothing.
    client_request_id = models.CharField(
        max_length=64, blank=True, default='', db_index=True,
        help_text='Set by the raising client once per submit attempt; repeats '
                  'on a retry of that same attempt so the database can refuse '
                  'a second row for it.')

    # Escalated to the CFO by the same-day ageing job (taskboard.escalation),
    # NOT by the 09:30 digest. Set once, the moment the escalation email goes
    # out, so a request already flagged is never emailed about twice.
    escalated_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Payment request'
        verbose_name_plural = 'Payment requests'
        constraints = [
            # One payment request per Graphite refund. Partial, so the blank
            # default on every hand-raised request does not collide.
            models.UniqueConstraint(
                fields=['graphite_ref'],
                condition=models.Q(graphite_ref__gt=''),
                name='paymentrequest_one_per_graphite_refund'),
            # One payment request per client submit attempt. Partial for the
            # same reason as above — most requests (and every one raised
            # before this field existed) carry no key at all.
            models.UniqueConstraint(
                fields=['client_request_id'],
                condition=models.Q(client_request_id__gt=''),
                name='paymentrequest_one_per_client_request_id'),
        ]

    # ── Per-line CFO authorisation — "approve 9, hold 1" (CFO 2026-08-31) ─────
    # A batch pack (e.g. 10 claims to 10 payees) is loaded into FNB as ONE batch
    # at finance sign-off; at the bank the CFO can authorise 9 and hold 1. These
    # helpers let Omni's record match that reality: each entry in line_items may
    # carry a 'line_status' (approved / held / rejected; absent or '' = pending).
    # MONEY NEVER MOVES HERE — the load already happened at stage 1. This is the
    # CFO's per-line authorisation record + the queue state. A 'held' line keeps
    # the request open (his words: "wait for 1"); the request closes the moment
    # every line is either approved or rejected.
    LINE_RESOLVED = ('approved', 'rejected')   # terminal for a line
    LINE_OPEN     = ('', 'held')               # keeps the request open

    @staticmethod
    def line_state(ln) -> str:
        """The per-line status of one line_items entry ('' = pending)."""
        return ((ln or {}).get('line_status') or '') if isinstance(ln, dict) else ''

    def unresolved_lines(self) -> list:
        """Indexes still needing a CFO decision (pending or held)."""
        return [i for i, ln in enumerate(self.line_items or [])
                if self.line_state(ln) in self.LINE_OPEN]

    def approved_lines(self) -> list:
        return [i for i, ln in enumerate(self.line_items or [])
                if self.line_state(ln) == 'approved']

    @property
    def all_lines_resolved(self) -> bool:
        """True once no line is pending or held (all approved or rejected)."""
        return not self.unresolved_lines()

    def line_progress(self) -> dict:
        """Counts per per-line state, for the '9/10 authorised' badge."""
        from collections import Counter
        lines = self.line_items or []
        c = Counter(self.line_state(ln) or 'pending' for ln in lines)
        return {'total': len(lines), 'approved': c.get('approved', 0),
                'held': c.get('held', 0), 'rejected': c.get('rejected', 0),
                'pending': c.get('pending', 0)}

    # ── Amend / cancel window (Kelvin Kimani spec 2026-09-08) ───────────────
    # "Amend and cancel are available only while the request is in its
    # pre-sign-off state. The moment finance signs off and the request moves to
    # final submission, it locks." Changing a signed-off request is recall /
    # reject by finance, which is a different process and out of scope.
    #
    # EXCEPTION is deliberately NOT in this window: a request sitting with the
    # committee is being decided by three named people on the pack in front of
    # them, and editing it under their feet would have them signing something
    # else. Flagged for the CFO in the handover.
    AMENDABLE_STATUSES = ('draft', 'pending_finance')

    @property
    def is_amendable(self) -> bool:
        return self.status in self.AMENDABLE_STATUSES

    def amend_lock_reason(self) -> str:
        """Why this request cannot be amended, in plain English. '' = it can."""
        if self.is_amendable:
            return ''
        if self.status == self.Status.EXCEPTION:
            return ('This request is with the exception committee. It cannot be '
                    'changed while three people are deciding it — ask them to '
                    'reject it, then raise it again.')
        if self.status == self.Status.CANCELLED:
            return 'This request is already cancelled.'
        if self.status == self.Status.REJECTED:
            return 'This request was rejected. Raise a new one.'
        return ('Finance has already signed this request off, so it is locked. '
                'A signed-off request is recalled or rejected by finance — it is '
                'not amended here.')

    def recalculate_total(self):
        """Re-derive TOTAL PAYABLE from the lines that are still payable.

        Every amend runs this "so the figures never drift from the lines behind
        them". Section B's liquidity position is derived from the total and the
        opening balance at render time, so it follows automatically — there is
        no second copy of it to keep in step.
        """
        payable = self.payable_lines(self.line_items)
        self.total = sum((money_dec(ln.get('amount')) for ln in payable),
                         Decimal('0.00'))
        return self.total

    # ── Processing method: what it records (Kelvin Kimani spec 2026-09-08) ───
    @property
    def is_individual(self) -> bool:
        return self.processing_method == self.ProcessingMethod.INDIVIDUAL

    @staticmethod
    def payable_lines(line_items) -> list:
        """The lines that will actually be paid — cancelled ones are retained
        on the record but are not payments (nothing here is ever deleted)."""
        return [ln for ln in (line_items or [])
                if isinstance(ln, dict) and not ln.get('cancelled')]

    @staticmethod
    def processing_summary(line_items, method) -> tuple:
        """(word, payment_count, label) for a set of lines and a method.

        A staticmethod so the authorisation pack — which renders from a plain
        dict, before the row exists — reads the SAME arithmetic the saved
        request does, instead of a second copy that can drift.

        The liquidity total is identical under both methods; only the count
        changes. A request with nothing left to pay implies no payments.
        """
        payable = PaymentRequest.payable_lines(line_items)
        individual = (method == PaymentRequest.ProcessingMethod.INDIVIDUAL)
        word = 'INDIVIDUAL' if individual else 'BULK'
        n = 0 if not payable else (len(payable) if individual else 1)
        return word, n, f'{word} ({n} payment{"" if n == 1 else "s"})'

    def payment_count(self) -> int:
        """How many payments this method implies — the count the approver sees
        next to the liquidity check. 1 under Bulk, N under Individual."""
        return self.processing_summary(self.line_items, self.processing_method)[1]

    def processing_method_label(self) -> str:
        """"BULK (1 payment)" — printed plainly on the pack."""
        return self.processing_summary(self.line_items, self.processing_method)[2]

    @staticmethod
    def show_processing_choice(line_items) -> bool:
        """Is the toggle a live decision on this request?

        With a single payable line bulk and individual are the same one payment,
        so the choice is meaningless — default to Bulk silently and do not put
        the question in front of the raiser.
        """
        return len(PaymentRequest.payable_lines(line_items)) > 1

    @staticmethod
    def reconciliation_error(line_items, total, *, currency: str = 'BWP') -> str | None:
        """The build-time assertion the spec demands, as a real guard.

        "Bulk total and the sum of the individual payments must reconcile
        exactly to TOTAL PAYABLE, or the request does not finalise."

        Both methods package the SAME lines, so there is one sum to check and it
        must be exact — not near. Returns None when the request may finalise,
        or the message to refuse it with. Decimal throughout: a float sum of
        eight invoice amounts is exactly how a request finalises 1 thebe out.
        """
        payable = PaymentRequest.payable_lines(line_items)
        parts = sum((money_dec(ln.get('amount')) for ln in payable), Decimal('0.00'))
        payable_total = money_dec(total)
        if parts == payable_total:
            return None
        return (f'The payment lines do not add up to the total. The lines come to '
                f'{currency} {parts:,.2f} and TOTAL PAYABLE reads '
                f'{currency} {payable_total:,.2f} — a difference of '
                f'{currency} {abs(payable_total - parts):,.2f}. Bulk and Individual '
                f'must both settle exactly this total, so the request cannot be '
                f'finalised until the two agree.')

    # ── POP heading (Kelvin Kimani spec 2026-09-08) ──────────────────────────
    # "One source, no mismatch, and the POP ties back to its request at a
    # glance." SUBJECT is the single source of truth — never the internal REF,
    # which is a routing reference and not a meaningful heading, and never the
    # Section A "Ref #" column, which carries roughly the same text but is typed
    # per line. Where SUBJECT and the line ref differ, SUBJECT wins.
    #
    # A plain hyphen, NOT the em dash the spec's example prints. An em dash in a
    # field that reaches FNB rejects the whole batch on RR10 "invalid character
    # set" — proven in production 2026-08-19. The heading is a document/email
    # title today, but it names a payment, so it is kept inside the safe set.
    POP_SUBJECT_JOIN = ' - '

    @staticmethod
    def pop_subject_at_submission(subject: str, ref: str) -> str:
        """The value to stamp into pop_subject when a request is submitted.

        The request SUBJECT, or the request reference when SUBJECT is blank —
        the spec's guard so a POP is never left untitled.
        """
        return ((subject or '').strip() or (ref or '').strip())[:200]

    def pop_heading(self) -> str:
        """The POP heading for this request (one POP, under Bulk).

        Reads the stamped value. Requests raised before the field existed have
        it blank, so they fall back the same way a blank subject does.
        """
        return (self.pop_subject or '').strip() or self.pop_subject_at_submission(
            self.subject, self.ref)

    def pop_heading_for_line(self, line, *, individual: bool | None = None) -> str:
        """The POP heading for ONE payment line.

        Under Bulk there is one payment and one POP, so every line shares the
        request heading. Under Individual there are N payments and N POPs, and
        an identical heading on all of them makes them indistinguishable — so
        the invoice number (or the line's own Ref #, or its description) is
        appended: "MCS 1162 JULY - IN102985".
        """
        base = self.pop_heading()
        # Defaults to how this request is actually being processed; the argument
        # is only for asking "what would the heading be under Individual?".
        if individual is None:
            individual = self.is_individual
        if not individual:
            return base
        ln = line if isinstance(line, dict) else {}
        tail = str(ln.get('invoice_number') or ln.get('ref')
                   or ln.get('description') or '').strip()
        if not tail or tail.upper() in base.upper():
            # Nothing to add, or the heading already names it — appending would
            # only repeat the request subject back to itself.
            return base
        return (base + self.POP_SUBJECT_JOIN + tail)[:200]

    def __str__(self):
        return f"{self.ref} — {self.currency} {self.total}"


class PaymentReleaseSignoff(BaseModel):
    """One committee member's sign-off on a payment EXCEPTION (CFO 2026-09-02).

    A fraud-risk exception (a changed bank account, or one deemed possible
    fraud) is decided by a committee: THREE distinct members of a six-person
    pool, never the person who raised the payment and never the CFO. This row
    is one member's signature; three of them decide the exception. Signatures
    are IMMUTABLE — the record of who released a payment must never be editable.
    MONEY NEVER MOVES: the committee clears an Omni workflow record; FNB + the
    CFO's own 2-factor stay the real gate.
    """
    request        = models.ForeignKey(PaymentRequest, on_delete=models.CASCADE,
                                        related_name='release_signoffs')
    signer         = models.ForeignKey(User, null=True, on_delete=models.SET_NULL,
                                        related_name='payment_release_signoffs')
    # Denormalised so the audit trail survives the signer's account being deleted.
    signer_email   = models.CharField(max_length=254, blank=True, default='')
    decision       = models.CharField(
        max_length=8, choices=[('approve', 'Approve'), ('reject', 'Reject')])
    is_independent = models.BooleanField(
        default=False, help_text='signer is on the independent side (Legakwa / Oprah)')
    # For a changed/new bank account the signer records a real call-back — who
    # they spoke to, on what number (from our records, NEVER the invoice), and
    # when (created_at). A bare tick is not enough — that is how invoice
    # redirection fraud gets signatures on it.
    called_who     = models.CharField(max_length=120, blank=True, default='')
    called_number  = models.CharField(max_length=40, blank=True, default='')
    note           = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        constraints = [
            # A member may sign a given exception at most once.
            models.UniqueConstraint(
                fields=['request', 'signer'],
                name='one_release_signoff_per_member_per_request'),
        ]

    def __str__(self):
        return f"{self.signer_email} {self.decision} on {self.request_id}"


class PaymentRequestChange(BaseModel):
    """One recorded amendment or cancellation on a payment request
    (Kelvin Kimani spec 2026-09-08).

    "Logs the change — the field, its before and after value, who changed it,
    and when." The whole data model the amend/cancel feature adds: this log, a
    cancelled flag on lines and on the request, and a reason field on cancels.

    Rows are IMMUTABLE and never deleted. The point of a change log is that it
    cannot be tidied up afterwards; a log an amend could rewrite would tell an
    auditor nothing. Attribution is stamped from the logged-in user and
    denormalised, so the trail survives that account being removed — and is
    NEVER typed (same audit-integrity rule as the committee signatures).
    """

    class Action(models.TextChoices):
        AMEND          = 'amend',          'Amended'
        CANCEL_LINE    = 'cancel_line',    'Line cancelled'
        CANCEL_REQUEST = 'cancel_request', 'Request cancelled'

    request     = models.ForeignKey(PaymentRequest, on_delete=models.CASCADE,
                                    related_name='changes')
    action      = models.CharField(max_length=16, choices=Action.choices)
    #: 1-based line number, or null for a request-level change.
    line        = models.PositiveIntegerField(null=True, blank=True)
    #: The field that moved, e.g. 'amount'. Blank on a cancel — the action IS
    #: the change there, and the reason carries the why.
    field       = models.CharField(max_length=40, blank=True, default='')
    value_before = models.TextField(blank=True, default='')
    value_after  = models.TextField(blank=True, default='')
    #: Required on a cancel, never asked for on an amend — "the before/after log
    #: IS the record of what changed. The 'why' is only required where a payment
    #: is pulled entirely."
    reason      = models.TextField(blank=True, default='')

    actor            = models.ForeignKey(User, null=True, on_delete=models.SET_NULL,
                                         related_name='payment_request_changes')
    # Denormalised at write time — "Amended by / Cancelled by [Name] ·
    # [Department]", pulled from the logged-in user, never typed.
    actor_name       = models.CharField(max_length=160, blank=True, default='')
    actor_department = models.CharField(max_length=100, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['created_at']
        indexes  = [models.Index(fields=['request', 'created_at'])]

    def __str__(self):
        where = f'line {self.line} ' if self.line else ''
        return f'{self.get_action_display()} {where}on {self.request_id}'

    @property
    def attribution(self) -> str:
        """"Amended by Kago Tshutlhedi · Finance" — the readable one-liner."""
        who = self.actor_name or 'a removed account'
        verb = ('Cancelled by' if self.action != self.Action.AMEND else 'Amended by')
        dept = f' · {self.actor_department}' if self.actor_department else ''
        return f'{verb} {who}{dept}'


def _pr_attach_path(instance, filename):
    return f'payment_requests/{instance.request_id}/{filename}'


class PaymentRequestAttachment(BaseModel):
    """Optional supporting document on a payment request — e.g. an agreement of
    loss for a claim payment, an invoice, a quote (CFO 2026-07-15). Users attach
    any evidence they choose; nothing is required. Served ONLY through the
    authenticated download endpoint (never a raw /media/ URL)."""

    request       = models.ForeignKey(PaymentRequest, on_delete=models.CASCADE,
                                      related_name='attachments')
    file          = models.FileField(upload_to=_pr_attach_path)
    original_name = models.CharField(max_length=255, blank=True, default='')
    uploaded_by   = models.ForeignKey(User, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='payment_request_attachments')

    class Meta(BaseModel.Meta):
        ordering = ['created_at']

    def __str__(self):
        return self.original_name or (self.file.name or '').split('/')[-1]


class TaskboardSetting(BaseModel):
    """One named config value Finance can change on screen, no deploy needed —
    same key/value pattern as payroll.models.PayrollSetting. Values are always
    stored as text; taskboard.escalation parses to the type it expects.

    Introduced for the payment ageing-escalation threshold (CFO 2026-09-14):
    "take the ageing threshold from whatever the code already treats as
    overdue; if nothing defines it, make it a SETTING Finance can change on
    screen" — see taskboard.escalation.stale_days().
    """

    key         = models.CharField(max_length=100, unique=True)
    value       = models.CharField(max_length=300)
    description = models.CharField(max_length=300, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['key']
        verbose_name        = 'Taskboard Setting'
        verbose_name_plural = 'Taskboard Settings'

    def __str__(self):
        return f'{self.key} = {self.value}'
