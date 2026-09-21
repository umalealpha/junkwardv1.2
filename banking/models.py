"""
banking/models.py

BankAccount            - maps a GL account to a real bank account
BankStatementFormat    - configurable CSV parsing rules per bank
BankStatement          - header record for one imported statement file
BankStatementLine      - individual transaction row from the statement
"""

import hashlib
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel, Currency

from .line_references import PaymentBasis, extract_line_references


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')


def line_identity(bank_account_id, transaction_date, amount, description, reference):
    """The line's own content, normalised — everything EXCEPT position.

    Used both as the base of the dedupe key and as a grouping key for
    counting how many times this exact content has already been seen.
    """
    return (
        str(bank_account_id),
        transaction_date.isoformat() if hasattr(transaction_date, 'isoformat')
        else str(transaction_date),
        str(Decimal(amount).quantize(TWO_PLACES)),
        (description or '').strip().lower(),
        (reference or '').strip().lower(),
    )


def compute_line_dedupe_key(bank_account_id, transaction_date, amount,
                            description, reference, occurrence: int = 0) -> str:
    """Deterministic fingerprint for one bank-statement line.

    Built from the line's own content — account, date, amount, normalised
    narrative/reference — PLUS ``occurrence``: how many times this exact
    content has already been seen (0 for the first, 1 for the second, ...).

    Why an occurrence counter, and NOT the line's position in the file
    (``line_number``): a real customer CAN be debited the same amount, same
    day, same narrative, twice (e.g. two identical debit-order pulls) — the
    first is occurrence 0, the second occurrence 1, and both survive as
    genuinely different keys. But the commonest real double-import is an
    OVERLAPPING date-range re-pull (e.g. 1-15 Sep, then 1-30 Sep) — the
    repeated transaction lands at a DIFFERENT line_number in the second pull,
    so keying on line_number would miss it entirely and import it twice. An
    occurrence count, reset to 0 for each name+amount+date+reference group at
    the START of every import, does not have that problem: an overlapping
    re-pull that contains the transaction only once assigns it occurrence 0
    again, which collides with the occurrence-0 key already on file — caught.
    """
    parts = list(line_identity(
        bank_account_id, transaction_date, amount, description, reference,
    )) + [str(occurrence)]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# BankAccount
# ---------------------------------------------------------------------------

class BankAccount(AuditableMixin, BaseModel):
    """
    Links a real bank account to its corresponding GL clearing account.
    The GL account must have ``is_bank_account=True``.
    """

    gl_account = models.OneToOneField(
        'ledger.Account',
        on_delete=models.PROTECT,
        related_name='bank_account_detail',
        help_text='GL account (is_bank_account must be True)',
    )
    bank_name      = models.CharField(max_length=100)
    account_name   = models.CharField(max_length=200)
    account_number = models.CharField(max_length=50)
    branch_code    = models.CharField(max_length=20, blank=True, null=True)
    currency_code  = models.ForeignKey(
        Currency, on_delete=models.PROTECT, default='BWP',
    )
    is_active            = models.BooleanField(default=True)
    # CFO directive 2026-05-25: FM wants to hide accounts they don't care
    # about from the FNB Integration UI (e.g. E-Wallet Pro Chimidza)
    # without deactivating them everywhere. Soft-hide flag, FNB page
    # filters it out by default but exposes a "Show hidden" toggle.
    hide_in_banking_ui   = models.BooleanField(default=False)
    # Which accounts the FNB jobs read, decided by a FLAG and never by a name
    # match (CFO 2026-09-20). `pull_fnb_statements --all` used to select on
    # `bank_name icontains 'FNB' OR account_name icontains 'FNB'`, and that is
    # why three of the CFO's six key accounts — Veritas Capital Mgmt, Risk
    # Software Africa, Unicoin - Current AC — had never once been read: their
    # bank_name is "First National Bank Botswana", which does not contain the
    # letters FNB. The same match pulled in the FNBB Credit Card Control A/C,
    # which is a CARD, not a bank account, and fails with HTTP 400 every
    # morning. A name match breaks again the next time somebody renames an
    # account; a flag does not.
    fnb_balance_watch    = models.BooleanField(
        default=False,
        help_text='Read this account from FNB — daily statements and the '
                  'morning balance pulls. Set deliberately, never inferred '
                  'from the account name.',
    )
    current_balance      = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO,
    )
    last_reconciled_date = models.DateField(null=True, blank=True)
    # TWO flags, not one, and the difference matters (CFO 2026-09-20).
    #
    # The first draft used fnb_balance_watch for BOTH jobs. That would have
    # switched OFF the daily statement pull for four accounts it currently
    # reaches — Investment Income, Choppies Kiosk, Alpha Health and the USD
    # account — because they are not among the six the CFO wants on screen.
    # He ruled: keep pulling them, just do not show them. Reading an account
    # and displaying it are different decisions and now have different flags.
    fnb_statement_pull = models.BooleanField(
        default=False,
        help_text='Pull this account\'s daily statement from FNB. Set '
                  'deliberately, never inferred from the account name.',
    )

    class Meta:
        ordering = ['bank_name', 'account_name']

    def __str__(self):
        return f"{self.bank_name} - {self.account_name} ({self.account_number})"


# ---------------------------------------------------------------------------
# BankStatementFormat
# ---------------------------------------------------------------------------

class BankStatementFormat(BaseModel):
    """
    Describes how to parse a CSV bank statement from a specific bank.

    Supply either ``amount_column`` OR both ``debit_column`` and
    ``credit_column`` — not both styles at once.

    ``sign_convention`` only applies when ``amount_column`` is used.
    """

    class SignConvention(models.TextChoices):
        DEBIT_NEGATIVE = 'debit_negative', 'Negative = outflow (most banks)'
        DEBIT_POSITIVE = 'debit_positive', 'Positive = outflow (rare)'

    name      = models.CharField(max_length=100, unique=True)
    bank_name = models.CharField(max_length=100)
    delimiter = models.CharField(max_length=1, default=',')
    encoding  = models.CharField(max_length=20, default='utf-8')
    skip_rows = models.IntegerField(
        default=0,
        help_text='Non-header rows to skip before the data begins',
    )

    # Column names matched case-insensitively against the CSV header
    date_column        = models.CharField(max_length=100)
    date_format        = models.CharField(
        max_length=30, default='%d/%m/%Y',
        help_text="strptime format, e.g. %%d/%%m/%%Y",
    )
    description_column = models.CharField(max_length=100)
    reference_column   = models.CharField(max_length=100, blank=True, null=True)
    balance_column     = models.CharField(max_length=100, blank=True, null=True)

    # Single-amount-column mode
    amount_column    = models.CharField(max_length=100, blank=True, null=True)
    sign_convention  = models.CharField(
        max_length=20,
        choices=SignConvention.choices,
        default=SignConvention.DEBIT_NEGATIVE,
    )

    # Separate-debit-credit-column mode (mutually exclusive with amount_column)
    debit_column  = models.CharField(max_length=100, blank=True, null=True)
    credit_column = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        ordering = ['bank_name', 'name']

    def __str__(self):
        return f"{self.name} ({self.bank_name})"


# ---------------------------------------------------------------------------
# BankStatement
# ---------------------------------------------------------------------------

def _generate_statement_number():
    """Auto-number: STMT-YYYY-NNNNNN"""
    from django.utils import timezone
    from django.db import transaction as db_tx

    year   = timezone.now().year
    prefix = f"STMT-{year}-"
    with db_tx.atomic():
        last = (
            BankStatement.objects
            .select_for_update()
            .filter(statement_number__startswith=prefix)
            .order_by('-statement_number')
            .values_list('statement_number', flat=True)
            .first()
        )
        nxt = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f"{prefix}{nxt:06d}"


class BankStatement(AuditableMixin, BaseModel):
    """Header record for one imported bank statement file."""

    class Status(models.TextChoices):
        IMPORTED         = 'imported',         'Imported'
        IN_PROGRESS      = 'in_progress',      'Reconciliation in progress'
        PENDING_APPROVAL = 'pending_approval', 'Reconciled — awaiting approval'
        RECONCILED       = 'reconciled',       'Reconciled & approved'

    statement_number = models.CharField(max_length=30, unique=True, blank=True)
    bank_account     = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name='statements',
    )
    statement_date  = models.DateField()
    # NULL means "the bank returned no balance", which is NOT the same thing as
    # zero (CFO 2026-09-20). Until this was nullable, fnb/statements.py started
    # both fields at Decimal('0.00') and only overwrote them on an OPBD/CLBD
    # block, so the Claims account (62493282265) reported a confident P0.00
    # every morning while the bank actually held about P256,000. Existing rows
    # keep the 0.00 they were written with — a migration must never rewrite
    # live financial rows, and a historical 0.00 cannot now be told apart from
    # a historical unknown. Every reader handles the null; see
    # banking/services.get_reconciliation_report and banking/serializers.
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2,
                                          null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=18, decimal_places=2,
                                          null=True, blank=True)
    file_name       = models.CharField(max_length=255)
    import_date     = models.DateTimeField(auto_now_add=True)
    imported_by     = models.ForeignKey(
        User, null=True, on_delete=models.SET_NULL,
        related_name='imported_statements',
    )
    status     = models.CharField(
        max_length=20, choices=Status.choices, default=Status.IMPORTED,
    )
    line_count = models.IntegerField(default=0)
    # Segregation of duties (BUG b72695a8, Oprah 2026-06-27): a reconciliation
    # is completed by one person (reconciled_by) and must be independently
    # APPROVED by a different person holding the `bank.approve` permission
    # before it counts as Reconciled. Mirrors the JE submit/approve and
    # PO create/fm_approve two-person controls.
    reconciled_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reconciled_statements',
    )
    approved_by   = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approved_statements',
    )
    approved_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-statement_date', '-import_date']

    def __str__(self):
        return (
            f"{self.statement_number} - "
            f"{self.bank_account.bank_name} {self.statement_date}"
        )

    def clean(self):
        # BUG-004: statement date cannot be in the future. "Today" is BOTSWANA's
        # today (settings.TIME_ZONE) — date.today() is the UTC date on a UTC box,
        # which called every statement dated today "future" between 00:00 and
        # 02:00 Gaborone (same midnight window as the JE guards, f9ff6d56).
        from django.core.exceptions import ValidationError
        from django.utils import timezone as _tz
        if self.statement_date and self.statement_date > _tz.localdate():
            raise ValidationError(
                {'statement_date': 'Statement date cannot be in the future.'}
            )

    def save(self, *args, **kwargs):
        if not self.statement_number:
            self.statement_number = _generate_statement_number()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# BankStatementLine
# ---------------------------------------------------------------------------

class BankStatementLine(BaseModel):
    """One transaction row from an imported bank statement."""

    class MatchStatus(models.TextChoices):
        UNMATCHED        = 'unmatched',        'Unmatched'
        AUTO_MATCHED     = 'auto_matched',     'Auto-matched'
        MANUALLY_MATCHED = 'manually_matched', 'Manually matched'
        EXCLUDED         = 'excluded',         'Excluded'

    statement        = models.ForeignKey(
        BankStatement, on_delete=models.CASCADE, related_name='lines',
    )
    line_number      = models.IntegerField()
    transaction_date = models.DateField()
    description      = models.CharField(max_length=500)
    reference        = models.CharField(max_length=200, blank=True, null=True)

    # Positive = inflow (money received), Negative = outflow (money sent)
    amount          = models.DecimalField(max_digits=18, decimal_places=2)
    running_balance = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
    )

    match_status = models.CharField(
        max_length=20,
        choices=MatchStatus.choices,
        default=MatchStatus.UNMATCHED,
    )
    matched_payment = models.ForeignKey(
        'payments.Payment',
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bank_statement_lines',
    )
    matched_journal_entry = models.ForeignKey(
        'ledger.JournalEntry',
        null=True, blank=True, on_delete=models.SET_NULL,
        related_name='bank_statement_lines',
    )
    match_confidence = models.IntegerField(null=True, blank=True)
    notes            = models.TextField(blank=True, null=True)
    raw_data         = models.JSONField(null=True, blank=True)

    # No-double-import guard (2026-09-12): a deterministic fingerprint of the
    # line's own content, enforced unique AT THE DATABASE LEVEL — see
    # compute_line_dedupe_key() above for what goes into it and why. Computed
    # automatically in save() when not already set.
    dedupe_key = models.CharField(max_length=64, unique=True, editable=False)

    # ── CR-001: what this line was FOR ──────────────────────────────────────
    # Until 2026-09-13 nothing on a statement line carried a claim number or a
    # supplier invoice number in a field anything could read — only the generic
    # `reference` text above. Every consumer re-parsed the free text for
    # itself, and the B6 Claims Payment Movement Report cannot exist
    # without these three: it splits supplier-invoice settlements from payments
    # to individual claimants, and only INVOICE-basis lines may be de-grossed
    # for VAT.
    #
    # Derived ON INSERT ONLY, by line_references.extract_line_references (see
    # save() below). Existing rows stay blank until someone deliberately runs
    # `manage.py backfill_bank_line_references --commit` — a deploy must never
    # rewrite live financial rows on its own.
    #
    # None of the three is in the dedupe_key, and must never be: the key
    # fingerprints what the BANK sent, and adding a derived field to it would
    # change every future key the moment the parser is improved, breaking the
    # no-double-import guard.
    claim_reference = models.CharField(
        max_length=50, blank=True, default='', db_index=True,
        help_text='Claim number this line settled, read off its own text.',
    )
    invoice_reference = models.CharField(
        max_length=50, blank=True, default='',
        help_text='Supplier invoice number this line settled. Blank means the '
                  'money went to an individual claimant, not against an invoice.',
    )
    payment_basis = models.CharField(
        max_length=20, blank=True, default=PaymentBasis.UNKNOWN,
        choices=PaymentBasis.CHOICES,
        help_text='What the payment was settled on. Only INVOICE may be '
                  'de-grossed for VAT — every other basis, UNKNOWN included, '
                  'stays gross.',
    )

    class Meta:
        ordering        = ['statement', 'line_number']
        unique_together = [('statement', 'line_number')]

    def __str__(self):
        direction = 'IN' if self.amount >= 0 else 'OUT'
        return (
            f"{self.statement.statement_number} "
            f"L{self.line_number:03d} "
            f"{self.transaction_date} "
            f"{direction} {abs(self.amount)}"
        )

    def save(self, *args, **kwargs):
        # Occurrence is a batch-scoped concept (how many times this content
        # has already appeared THIS import) — the importer and the FNB pull
        # path always compute and pass it explicitly (see services.py /
        # fnb/statements.py). A direct, one-off create with no explicit
        # dedupe_key gets occurrence 0 — the reasonable default for a single
        # ad-hoc line, not a batch import.
        if not self.dedupe_key:
            self.dedupe_key = compute_line_dedupe_key(
                self.statement.bank_account_id,
                self.transaction_date,
                self.amount,
                self.description,
                self.reference,
            )
        # CR-001: read the claim / invoice / basis off this line's own text —
        # ON INSERT ONLY. Both import paths (banking/services.py and
        # fnb/statements.py) create lines with .objects.create(), so both get
        # this for free with no change to either.
        #
        # Deliberately NOT re-derived on update. A later save must never
        # overwrite a value a person corrected by hand, and — more importantly
        # — a deploy that touches these rows for any other reason must not
        # silently rewrite financial references as a side effect. Filling the
        # rows that already exist is the backfill command's job, and only when
        # somebody runs it.
        if self._state.adding:
            refs = extract_line_references(self.description, self.reference)
            if not self.claim_reference:
                self.claim_reference = refs.claim_reference
            if not self.invoice_reference:
                self.invoice_reference = refs.invoice_reference
            if not self.payment_basis or self.payment_basis == PaymentBasis.UNKNOWN:
                self.payment_basis = refs.payment_basis
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# BankBalanceSnapshot
# ---------------------------------------------------------------------------

class BankBalanceSnapshot(BaseModel):
    """What one account's balance was, the moment we asked the bank.

    One row per watched account per pull — including the pulls that failed,
    because "we could not read it" is the answer the CFO's screen needs and a
    missing row cannot say it.

    These deliberately do NOT live on ``BankStatement``. Three pulls a day
    would multiply statement rows three-fold, and a statement re-saving the
    same seven-day window every morning is exactly what put 20,779 duplicate
    rows into ``BankStatementLine`` (cleaned 2026-09-20). A balance is a
    reading, not a statement; it gets its own thin table and touches nothing
    the reconciliation engine reads.
    """

    class Outcome(models.TextChoices):
        OK                  = 'ok',                  'Balance read'
        NO_BALANCE_RETURNED = 'no_balance_returned', 'Bank answered, no balance block'
        FAILED              = 'failed',              'The read failed'

    class Source(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled pull'
        MANUAL    = 'manual',    'Run by hand'

    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT, related_name='balance_snapshots',
    )
    # Set explicitly by the caller, not auto_now_add: one run stamps every
    # account with the same instant, and a dry run must be able to build a row
    # in memory without saving it.
    taken_at   = models.DateTimeField(db_index=True)
    #: The banking day the figure belongs to (Botswana's today, via localdate).
    as_of_date = models.DateField()

    # NULL whenever the figure is unknown. NEVER 0.00 to mean unknown — that
    # confusion is the whole reason this feature exists.
    opening = models.DecimalField(max_digits=18, decimal_places=2,
                                  null=True, blank=True)
    closing = models.DecimalField(max_digits=18, decimal_places=2,
                                  null=True, blank=True)

    outcome = models.CharField(max_length=24, choices=Outcome.choices)
    #: The bank's own words when it refused. Server-side only — it can echo the
    #: full account number back at us, so it is never put in an API response.
    error_text = models.TextField(blank=True, default='')
    source  = models.CharField(max_length=10, choices=Source.choices,
                               default=Source.SCHEDULED)

    class Meta:
        ordering = ['-taken_at']
        indexes  = [
            models.Index(fields=['bank_account', '-taken_at'],
                         name='bankbal_acct_taken_idx'),
        ]

    def __str__(self):
        shown = 'unknown' if self.closing is None else str(self.closing)
        return (f'{self.bank_account.account_number} {self.as_of_date} '
                f'{self.outcome} {shown}')


# ---------------------------------------------------------------------------
# Bank reconciliation rule engine — imported from rec_rules_models so Django
# picks BankRecRule up under the `banking` app label without splitting models
# across modules at the ORM layer. See banking/rec_rules_models.py.
# ---------------------------------------------------------------------------
from .rec_rules_models import BankRecRule  # noqa: E402,F401


# ---------------------------------------------------------------------------
# BankMatchMemory (L-BANKAI)
# ---------------------------------------------------------------------------

class BankMatchMemory(BaseModel):
    """
    Learns from previously confirmed bank line matches to suggest future ones.

    A "memory" is a link between a normalised counterparty from a bank
    statement line's description and a normalised payee/contact name from
    a confirmed Omni payment. Each time a user confirms this link,
    `times_confirmed` is incremented.

    These memories are used to generate suggestions, but they NEVER auto-match.
    A user always confirms via the standard matching UI.
    """
    company = models.ForeignKey(
        'core.Company',
        on_delete=models.CASCADE,
        related_name='bank_match_memories',
    )
    counterparty_key = models.CharField(
        max_length=120, db_index=True,
        help_text="Normalised key from the statement line's description.",
    )
    payee_key = models.CharField(
        max_length=200,
        help_text="Normalised key from the matched payment's contact/payee name.",
    )
    times_confirmed = models.PositiveIntegerField(default=0)
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    last_confirmed_by = models.ForeignKey(
        User,
        null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    disabled = models.BooleanField(
        default=False,
        help_text="Finance user can disable this memory to stop it from generating suggestions.",
    )

    class Meta:
        unique_together = ('company', 'counterparty_key', 'payee_key')
        ordering = ['-times_confirmed']

    def __str__(self):
        return f'"{self.counterparty_key}" -> "{self.payee_key}" ({self.times_confirmed} times)'
