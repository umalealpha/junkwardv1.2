"""
petty_cash/models.py

Imprest petty cash system.

Concept:
  • Each PettyCashLocation holds a fixed float (default P15,000) of physical
    cash. The custodian is the person responsible for the tin.
  • A PettyCashVoucher is the document raised every time cash leaves the tin
    (tea, fuel, courier, sundries). Each voucher is approved by a different
    user from the submitter (SoD) and posts a journal entry that DR's the
    relevant expense and CR's the petty cash GL — so the GL stays in sync
    with the physical balance, voucher by voucher.
  • At month-end (or any FiscalPeriod), a PettyCashReimbursement totals all
    posted-but-unreimbursed vouchers for that location and posts a single
    JE: DR petty cash / CR bank, restoring the float to its full P15,000.

Internal-control posture:
  • Maker-checker: voucher creator ≠ approver (segregation of duties).
  • Approver title gated to Finance Manager / Financial Controller / CFO.
  • A voucher cannot be approved if it would push the float negative —
    i.e. amount > current available cash on hand.
  • A reimbursement is itself maker-checker: the user who runs the
    reimbursement must hold an approval-eligible title.
  • Once REIMBURSED, vouchers are immutable.

GL accounts (from setup_chart_of_accounts):
  • 1160 Petty cash             — the imprest float (asset)
  • 1110 FNB BWP operating      — default reimbursing bank
  • 6xxx                        — expense accounts the voucher hits
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditableMixin, BaseModel


ZERO = Decimal('0.00')
DEFAULT_FLOAT = Decimal('15000.00')


# ---------------------------------------------------------------------------
# Number generators
# ---------------------------------------------------------------------------

def _next_voucher_number():
    """PCV-YYYY-NNNNNN, monotonically increasing within a calendar year."""
    year = timezone.now().year
    prefix = f'PCV-{year}-'
    with transaction.atomic():
        last = (
            PettyCashVoucher.objects
            .select_for_update()
            .filter(voucher_number__startswith=prefix)
            .order_by('-voucher_number')
            .values_list('voucher_number', flat=True)
            .first()
        )
        next_seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{next_seq:06d}'


def _next_reimbursement_number():
    """PCR-YYYY-NNNNNN."""
    year = timezone.now().year
    prefix = f'PCR-{year}-'
    with transaction.atomic():
        last = (
            PettyCashReimbursement.objects
            .select_for_update()
            .filter(reimbursement_number__startswith=prefix)
            .order_by('-reimbursement_number')
            .values_list('reimbursement_number', flat=True)
            .first()
        )
        next_seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{next_seq:06d}'


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class PettyCashLocation(AuditableMixin, BaseModel):
    """A physical petty cash tin. Most companies have one; multiple
    locations are supported in case ADI ever opens branch offices."""

    name = models.CharField(max_length=120, unique=True)
    address = models.TextField(
        blank=True,
        help_text='Where the cash physically sits (e.g. "Boardroom safe, '
                  '2nd Floor BAC Two, BIH").',
    )
    float_amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        default=DEFAULT_FLOAT,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Maximum cash float held at this location (Pula).',
    )
    custodian = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_locations',
        help_text='Person responsible for the physical cash + voucher pad.',
    )
    company = models.ForeignKey(
        'core.Company', null=True, blank=True, on_delete=models.PROTECT,
        related_name='petty_cash_locations',
        help_text='Entity that owns this float. Every petty-cash journal entry '
                  'is stamped with it, so the expense rolls up into the correct '
                  "company's books and the historical FY lock applies to "
                  'backdated vouchers.',
    )
    petty_cash_account = models.ForeignKey(
        'ledger.Account', on_delete=models.PROTECT,
        related_name='petty_cash_float_for',
        help_text='GL account that represents this float (default: 1160).',
    )
    reimbursing_bank_account = models.ForeignKey(
        'ledger.Account', on_delete=models.PROTECT,
        related_name='petty_cash_reimburses_from',
        help_text='Bank GL the reimbursement comes out of (default: 1110).',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    # -----------------------------------------------------------------
    # Imprest accounting helpers
    # -----------------------------------------------------------------

    def total_unreimbursed(self):
        """Sum of POSTED vouchers at this location not yet reimbursed.

        These are the vouchers that have already left the tin but whose
        cash has not yet been topped up from the bank.
        """
        return (
            self.vouchers
            .filter(status=PettyCashVoucher.Status.POSTED)
            .aggregate(total=models.Sum('amount'))['total']
            or ZERO
        )

    def cash_on_hand(self):
        """Expected physical cash currently in the tin = float - unreimbursed."""
        return self.float_amount - self.total_unreimbursed()

    def available_for_voucher(self):
        """How much can still be disbursed before the float is empty.

        Includes vouchers still in the signature chain (PENDING_APPROVAL and
        ONE_SIGNATURE) — we treat them as already promised, so a reviewer sees
        realistic headroom.
        """
        committed = (
            self.vouchers
            .filter(status__in=[
                PettyCashVoucher.Status.PENDING_APPROVAL,
                PettyCashVoucher.Status.ONE_SIGNATURE,
                PettyCashVoucher.Status.POSTED,
            ])
            .aggregate(total=models.Sum('amount'))['total']
            or ZERO
        )
        return self.float_amount - committed


# ---------------------------------------------------------------------------
# Voucher
# ---------------------------------------------------------------------------

class PettyCashVoucher(AuditableMixin, BaseModel):
    """One disbursement of cash from the tin.

    Lifecycle:
      DRAFT
        └── submit ──▶ PENDING_APPROVAL
                          ├── sign 1 ──▶ ONE_SIGNATURE
                          │                 ├── sign 2 ──▶ POSTED ──▶ REIMBURSED
                          │                 └── reject ──▶ REJECTED
                          └── reject  ──▶ REJECTED ──▶ reopen ──▶ DRAFT

    Dual sign-off (CFO 2026-07-16): two DISTINCT eligible signers are needed
    to post — the JE is only cut on the second signature.
    """

    class Status(models.TextChoices):
        DRAFT             = 'draft',             'Draft'
        PENDING_APPROVAL  = 'pending_approval',  'Pending Approval'
        # Petty cash needs TWO signatures (CFO 2026-07-16): the first sign
        # moves the voucher here; a second, different signer posts it.
        ONE_SIGNATURE     = 'one_signature',     'One signature — needs 2nd'
        POSTED            = 'posted',            'Posted'
        REIMBURSED        = 'reimbursed',        'Reimbursed'
        REJECTED          = 'rejected',          'Rejected'

    voucher_number = models.CharField(max_length=20, unique=True, editable=False)
    location = models.ForeignKey(
        PettyCashLocation, on_delete=models.PROTECT,
        related_name='vouchers',
    )
    voucher_date = models.DateField(default=timezone.localdate)
    payee = models.CharField(
        max_length=200,
        help_text='Who received the cash (free text — staff name, supplier, etc.).',
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    expense_account = models.ForeignKey(
        'ledger.Account', on_delete=models.PROTECT,
        related_name='petty_cash_vouchers',
        null=True, blank=True,
        help_text='GL expense account this disbursement is charged to. Left '
                  'blank by the person who RAISES the voucher — the petty-cash '
                  'finance team (Keetile, Pako, Legakwa, Tlamelo) codes it when '
                  'they post it (CFO directive 2026-08-10). Required before the '
                  'voucher can be posted.',
    )
    # ── amendment by the custodian (CFO approved 2026-08-05) ─────────────────
    # Keetile: "Please allow the petty cash custodian (approver) to edit or amend both the
    # amount requested by the requestor and the GL line selected." The CFO approved it — the
    # aim is to let the next tier of managers finish the job themselves instead of bouncing
    # vouchers back and forth.
    #
    # It is allowed, and it is RECORDED. The figure the requester actually asked for is kept,
    # with who changed it and why, because an approver silently rewriting an amount is how a
    # petty-cash float stops reconciling and nobody can say when it started.
    original_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='What the requester asked for, kept when a custodian amends the amount.')
    original_expense_account = models.ForeignKey(
        'ledger.Account', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
        help_text='The GL account the requester chose, kept when a custodian changes it.')
    amended_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_amendments')
    amended_at = models.DateTimeField(null=True, blank=True)
    amend_reason = models.TextField(
        blank=True, default='',
        help_text='Why the custodian changed the amount or the GL line. Required to amend.')

    description = models.TextField(
        help_text='What the cash was for. Required.',
    )
    receipt_reference = models.CharField(
        max_length=120, blank=True,
        help_text='Till slip / invoice number / receipt ref. Optional below P50.',
    )
    receipt_attached = models.BooleanField(
        default=False,
        help_text='Tick when the physical receipt is filed with the voucher.',
    )

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT,
    )
    rejection_reason = models.TextField(blank=True)

    # Workflow tracking
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_vouchers_created',
    )
    submitted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_vouchers_submitted',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    # First of two signatures (dual sign-off, CFO 2026-07-16). The SECOND /
    # final signature is stored in approved_by/approved_at, which is also the
    # signer that posts the JE.
    first_approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_vouchers_first_approved',
    )
    first_approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_vouchers_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    # Posting
    journal_entry = models.OneToOneField(
        'ledger.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_voucher',
        help_text='DR expense / CR petty cash JE created on approval.',
    )
    reimbursement = models.ForeignKey(
        'PettyCashReimbursement', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='vouchers',
        help_text='The monthly reimbursement that swept this voucher.',
    )

    class Meta:
        ordering = ['-voucher_date', '-voucher_number']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['location', 'status']),
            models.Index(fields=['voucher_date']),
        ]

    def __str__(self):
        return f'{self.voucher_number} — {self.payee} P{self.amount}'

    def clean(self):
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({'amount': 'Voucher amount must be positive.'})
        if not (self.description or '').strip():
            raise ValidationError({'description': 'Description is required.'})
        if (
            self.expense_account_id
            and self.expense_account.account_type != 'expense'
        ):
            raise ValidationError({
                'expense_account': 'Voucher must be charged to an expense account.',
            })

    def save(self, *args, **kwargs):
        # Immutability: a REIMBURSED voucher is a swept, GL-posted cash
        # movement — it is terminal and must never be edited or un-swept
        # (mirrors JournalEntry.save's posted-entry lock). The legitimate
        # POSTED -> REIMBURSED transition still passes: only a voucher that is
        # ALREADY reimbursed in the DB is frozen.
        if self.pk:
            prior_status = (
                PettyCashVoucher.objects
                .filter(pk=self.pk)
                .values_list('status', flat=True)
                .first()
            )
            if prior_status == self.Status.REIMBURSED:
                raise ValidationError(
                    f'{self.voucher_number} is reimbursed and locked; it '
                    f'cannot be modified. Reverse the reimbursement JE instead.'
                )
        if not self.voucher_number:
            self.voucher_number = _next_voucher_number()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # A posted/reimbursed voucher has a real GL entry behind it; deleting
        # the row would orphan that JE and silently inflate cash-on-hand.
        if self.status in (self.Status.POSTED, self.Status.REIMBURSED):
            raise ValidationError(
                f'Cannot delete {self.voucher_number}: it is '
                f'{self.get_status_display()}. Posted cash movements are '
                f'immutable — reverse the journal entry instead.'
            )
        return super().delete(*args, **kwargs)


# ---------------------------------------------------------------------------
# Reimbursement
# ---------------------------------------------------------------------------

class PettyCashReimbursement(AuditableMixin, BaseModel):
    """A single top-up event that restores a location's float.

    A reimbursement sweeps every POSTED-and-not-yet-reimbursed voucher at
    the location whose voucher_date falls inside the chosen FiscalPeriod
    (or any custom date window), totals them, and posts one JE:

        DR  1160 Petty cash       (total)
        CR  1110 FNB BWP          (total)

    Each swept voucher is then marked REIMBURSED and locked.
    """

    class Status(models.TextChoices):
        DRAFT       = 'draft',       'Draft'
        PENDING_FM  = 'pending_fm',  'Pending FM Review'
        PENDING_CFO = 'pending_cfo', 'Pending CFO Approval'
        POSTED      = 'posted',      'Posted'
        REJECTED    = 'rejected',    'Rejected'

    reimbursement_number = models.CharField(
        max_length=20, unique=True, editable=False,
    )
    location = models.ForeignKey(
        PettyCashLocation, on_delete=models.PROTECT,
        related_name='reimbursements',
    )
    period = models.ForeignKey(
        'ledger.FiscalPeriod', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='petty_cash_reimbursements',
        help_text='Optional — FiscalPeriod the reimbursement is anchored to.',
    )
    period_start = models.DateField(
        help_text='First voucher_date eligible for inclusion (inclusive).',
    )
    period_end = models.DateField(
        help_text='Last voucher_date eligible for inclusion (inclusive).',
    )
    reimbursement_date = models.DateField(default=timezone.localdate)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    voucher_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT,
    )
    rejection_reason = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_reimbursements_created',
    )
    # Three-stage approval chain (CFO directive 2026-07-15):
    #   maker submits -> FM reviews -> CFO commits (posts the bank JE).
    submitted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_reimbursements_submitted',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    fm_reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_reimbursements_fm_reviewed',
    )
    fm_reviewed_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_reimbursements_posted',
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    journal_entry = models.OneToOneField(
        'ledger.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='petty_cash_reimbursement',
    )

    class Meta:
        ordering = ['-reimbursement_date', '-reimbursement_number']

    def __str__(self):
        return (
            f'{self.reimbursement_number} — {self.location.name} '
            f'P{self.total_amount} ({self.voucher_count} vouchers)'
        )

    def save(self, *args, **kwargs):
        if not self.reimbursement_number:
            self.reimbursement_number = _next_reimbursement_number()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Voucher receipt attachment (CFO directive 2026-07-15 — no more Excel; every
# receipt is uploaded into omni against its voucher.)
# ---------------------------------------------------------------------------

def _voucher_receipt_path(instance, filename):
    """media/petty-cash-receipts/YYYY/MM/<voucher-number>-<file>."""
    d = getattr(instance.voucher, 'voucher_date', None) or timezone.localdate()
    return f'petty-cash-receipts/{d:%Y}/{d:%m}/{instance.voucher.voucher_number}-{filename}'


class PettyCashVoucherReceipt(AuditableMixin, BaseModel):
    """A scanned/photographed receipt attached to a petty cash voucher —
    the digital replacement for the physical voucher pad + Excel log."""

    voucher = models.ForeignKey(
        PettyCashVoucher, on_delete=models.CASCADE, related_name='receipts',
    )
    file = models.FileField(upload_to=_voucher_receipt_path)
    filename = models.CharField(max_length=255, help_text='Original filename as uploaded.')
    file_size_bytes = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True, default='')
    uploaded_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='petty_cash_receipts_uploaded',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.filename} on {self.voucher.voucher_number}'
