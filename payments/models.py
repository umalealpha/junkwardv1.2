"""
payments/models.py

Payment recording, allocation against invoices, and withholding tax (WHT).

Models:
  - Payment               A single received or sent payment
  - PaymentAllocation     Links a payment to one or more invoices
  - WithholdingTaxRecord  BURS WHT compliance record per broker payment

Every monetary field: DecimalField(max_digits=18, decimal_places=2)

WHT RULES (Botswana):
  - Applies to ALL payments sent to brokers who are not WHT-exempt
  - Rate: 10% of gross payment
  - Tax year: July 1 – June 30
  - P48,000 annual threshold tracked in cumulative_paid_ytd for compliance reporting
"""

import datetime
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import AuditLog, AuditableMixin, BaseModel, Currency


logger = logging.getLogger(__name__)

TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')
HUNDRED    = Decimal('100.00')
WHT_RATE   = Decimal('10.00')

# Default CoA code for the "Discount received" P&L account. Override via
# settings.DISCOUNT_RECEIVED_ACCOUNT_CODE if a different code is used.
DEFAULT_DISCOUNT_RECEIVED_ACCOUNT_CODE = '400020'


def default_pop_email():
    """Default proof-of-payment recipient (CFO 2026-08-22: Accounts).

    A module-level callable (not a lambda / inline string) so migrations can
    serialise a stable reference to it, and a site can override the address via
    settings.FNB_DEFAULT_POP_EMAIL without a data migration.
    """
    return getattr(settings, 'FNB_DEFAULT_POP_EMAIL',
                   'accountsdept@alphadirect.co.bw')


def _get_discount_received_account():
    """
    Look up the "Discount received" GL account.

    Returns the Account, or None if not present. Callers SKIP posting the
    discount JE line silently and log a warning — the bill still pays, the
    rebate just goes to "wherever else" until the account is seeded. This
    is intentional: we never want to block a CFO pay-run for a missing
    optional account.
    """
    from ledger.models import Account
    code = getattr(
        settings,
        'DISCOUNT_RECEIVED_ACCOUNT_CODE',
        DEFAULT_DISCOUNT_RECEIVED_ACCOUNT_CODE,
    )
    try:
        return Account.objects.get(code=code)
    except Account.DoesNotExist:
        logger.warning(
            'Discount-received account %s missing — early-pay discount '
            'will not post to the GL. Seed the account or set '
            'DISCOUNT_RECEIVED_ACCOUNT_CODE in settings.',
            code,
        )
        return None


def _bwp(amount, rate):
    return (amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _get_tax_year_start(date):
    """Return July 1 of the Botswana tax year that contains *date*."""
    if date.month >= 7:
        return datetime.date(date.year, 7, 1)
    return datetime.date(date.year - 1, 7, 1)


# ---------------------------------------------------------------------------
# Payment number generator
# ---------------------------------------------------------------------------

def _generate_payment_number(payment_type):
    prefix     = 'PAY-IN' if payment_type == 'received' else 'PAY-OUT'
    year       = timezone.now().year
    full_prefix = f"{prefix}-{year}-"

    with transaction.atomic():
        last = (
            Payment.objects
            .select_for_update()
            .filter(payment_number__startswith=full_prefix)
            .order_by('-payment_number')
            .values_list('payment_number', flat=True)
            .first()
        )
        next_seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f"{full_prefix}{next_seq:06d}"


# ---------------------------------------------------------------------------
# Invoice allocation helper  (defined before Payment so confirm() can call it)
# ---------------------------------------------------------------------------

def receivable_override(invoices):
    """The receivable account a receipt should clear, when every invoice it settles
    names the SAME override (the Veritas salvage invoices do — CFO 19-Sep-2026).
    None means behave exactly as before and clear premium receivable (1210)."""
    ids = {getattr(inv, 'receivable_account_id', None) for inv in invoices}
    if len(ids) != 1:
        return None
    only = ids.pop()
    if not only:
        return None
    from ledger.models import Account
    return Account.objects.filter(pk=only).first()


def _update_invoice_from_allocations(invoice):
    """
    Recalculate invoice.amount_paid as the sum of all confirmed/reconciled
    allocations, then update status accordingly.

    Uses a direct DB update to bypass Invoice's immutability guard — this is
    an intentional accounting operation, not a user edit.
    """
    from billing.models import Invoice as InvoiceModel

    total_paid = (
        PaymentAllocation.objects
        .filter(
            invoice=invoice,
            payment__status__in=[
                Payment.Status.CONFIRMED,
                Payment.Status.RECONCILED,
            ],
        )
        .aggregate(total=Sum('amount_allocated'))['total']
        or ZERO
    )

    balance = invoice.total_amount - total_paid

    if balance <= ZERO:
        new_status = InvoiceModel.Status.PAID
    elif total_paid > ZERO:
        new_status = InvoiceModel.Status.PARTIALLY_PAID
    else:
        new_status = InvoiceModel.Status.POSTED

    InvoiceModel.objects.filter(pk=invoice.pk).update(
        amount_paid=total_paid,
        balance_due=balance,
        status=new_status,
    )


def resolve_paying_bank(raw_id):
    """Resolve a paying-bank id to its ledger.Account (the GL bank account).

    The FE bank pickers (/payments/new, /payments/once-off, /payments/upload)
    are populated from /bank-accounts/ and therefore send banking.BankAccount
    ids, while Payment.bank_account FKs ledger.Account. Accept EITHER id here:
    try ledger.Account first, then banking.BankAccount -> its gl_account.
    Returns None if neither matches (or the id is not a valid UUID).
    CFO 2026-07-07 (Pako's "Failure to recognize below bank account").
    """
    from django.core.exceptions import ValidationError as _VE
    from ledger.models import Account as _Account

    if not raw_id:
        return None
    try:
        acc = _Account.objects.filter(
            id=raw_id, is_bank_account=True, is_active=True).first()
    except (_VE, ValueError):
        return None
    if acc:
        return acc
    from banking.models import BankAccount as _BankAccount
    try:
        # is_active on BOTH the banking record and its GL account — a
        # deactivated bank (e.g. the retired First Capital / Stanbic /
        # E-Wallet accounts) must not be usable via a stale/raw id.
        ba = (_BankAccount.objects.select_related('gl_account')
              .filter(id=raw_id, is_active=True).first())
    except (_VE, ValueError):
        return None
    if (ba and ba.gl_account and ba.gl_account.is_bank_account
            and ba.gl_account.is_active):
        return ba.gl_account
    return None


def resolve_fx_rate(currency_code, on_date):
    """Latest APPROVED BoB/manual rate for currency→BWP effective on/before
    *on_date*. Returns Decimal('1') for BWP, the rate, or None when no
    approved rate exists (callers must block rather than default to 1.0 —
    bug class c9355408: a USD 10,000 payment must never book as BWP 10,000).
    """
    if not currency_code or currency_code == 'BWP':
        return Decimal('1.00000000')
    from core.models import ExchangeRate
    row = (ExchangeRate.objects
           .filter(from_currency_id=currency_code, to_currency_id='BWP',
                   effective_date__lte=on_date,
                   approved_by__isnull=False, approved_at__isnull=False)
           .order_by('-effective_date')
           .first())
    return row.rate if row else None


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

class Payment(AuditableMixin, BaseModel):
    """
    A single inbound or outbound payment.
    Call confirm(user) to post the journal entry and lock the record.
    """

    class PaymentType(models.TextChoices):
        RECEIVED = 'received', 'Received'
        SENT     = 'sent',     'Sent'

    class PaymentMethod(models.TextChoices):
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        DEBIT_ORDER   = 'debit_order',   'Debit Order'
        MOBILE_MONEY  = 'mobile_money',  'Mobile Money'
        CASH          = 'cash',          'Cash'
        CHEQUE        = 'cheque',        'Cheque'
        GATEWAY       = 'gateway',       'Payment Gateway'

    class Status(models.TextChoices):
        DRAFT       = 'draft',       'Draft'
        CONFIRMED   = 'confirmed',   'Confirmed'
        RECONCILED  = 'reconciled',  'Reconciled'
        CANCELLED   = 'cancelled',   'Cancelled'

    class ApprovalStatus(models.TextChoices):
        # PAY-003 tier maker-checker (CFO/Oprah directive 2026-05-27).
        # NOT_REQUIRED = legacy / pay-run path that confirms directly under
        # the OutboundPaymentPolicy dual-auth threshold. The two named init
        # paths ("Pay" on a Posted bill, "Create Payment" from a bank-rec
        # line) submit_for_approval → PENDING → approve_payment posts the GL.
        NOT_REQUIRED = 'not_required', 'Not required'
        PENDING      = 'pending',      'Pending approval'
        APPROVED     = 'approved',     'Approved'
        REJECTED     = 'rejected',     'Rejected'

    payment_number = models.CharField(max_length=30, unique=True, blank=True)
    payment_type   = models.CharField(max_length=10, choices=PaymentType.choices)
    contact        = models.ForeignKey(
                         'billing.Contact', on_delete=models.PROTECT,
                         related_name='payments',
                     )
    company        = models.ForeignKey(
                         'core.Company', on_delete=models.PROTECT,
                         related_name='payments', null=True, blank=True,
                     )
    bank_account   = models.ForeignKey(
                         'ledger.Account', on_delete=models.PROTECT,
                         related_name='payments',
                     )
    vendor_bank_account = models.ForeignKey(
                         'procurement.VendorBankAccount',
                         null=True, blank=True,
                         on_delete=models.PROTECT,
                         related_name='payments',
                         help_text='Required for outbound electronic payments. '
                                   'Must be ACTIVE and belong to the same '
                                   'vendor as this payment.',
                     )
    # ── One-off (ad-hoc) payee — CFO directive 2026-07-07 ─────────────────
    # A payment to someone NOT in the vendor master (a genuine one-off). The
    # destination bank is captured INLINE here instead of the Vendor Bank
    # register; `contact` points at a per-company sentinel "Ad-hoc payee". It
    # still runs through the full dual-control (1-FM/FC + 1-CFO/CEO) approval
    # quorum, and is flagged is_once_off so it's easy to review.
    is_once_off          = models.BooleanField(default=False)
    payee_name           = models.CharField(max_length=200, blank=True, default='')
    payee_bank_name      = models.CharField(max_length=120, blank=True, default='')
    payee_account_number = models.CharField(max_length=40, blank=True, default='')
    payee_branch_code    = models.CharField(max_length=20, blank=True, default='')
    payment_date   = models.DateField()
    currency_code  = models.ForeignKey(
                         Currency, on_delete=models.PROTECT,
                         related_name='payments', default='BWP',
                     )
    exchange_rate  = models.DecimalField(
                         max_digits=18, decimal_places=8, default=Decimal('1.00000000'),
                     )
    amount         = models.DecimalField(max_digits=18, decimal_places=2)
    amount_bwp     = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    payment_method = models.CharField(max_length=15, choices=PaymentMethod.choices)
    # blank=True: the UI marks Reference optional — an empty reference must
    # not 400 the whole form (Fable audit 2026-07-07).
    reference      = models.CharField(max_length=200, blank=True, default='')
    description    = models.TextField(null=True, blank=True)
    # ── What the BANK is told (CFO 2026-08-20) ────────────────────────────
    # Until now these three were derived silently, and the derivation produced
    # things nobody chose: the beneficiary name came from the vendor bank
    # account, our reference was always the payment number, and the narration
    # fell back to "One-off - <payee>". The CFO's P10 test on 2026-08-20 went
    # to the bank reading "One-off - PAKO KAGO", which is not a narration
    # anyone would write.
    #
    # Blank means "derive it as before", so nothing changes for a payment that
    # does not set them. Lengths are the ISO 20022 limits FNB enforces:
    # endToEndId 35, name and remittance 140. All three are still folded
    # through fnb_text() before they reach the bank, so an operator cannot type
    # a character that would get the batch rejected.
    bank_beneficiary_name = models.CharField(
        max_length=140, blank=True, default='',
        help_text='Who the bank shows as the beneficiary. Blank = the vendor '
                  "or payee name we hold.")
    bank_our_reference    = models.CharField(
        max_length=35, blank=True, default='',
        help_text='Our own reference for this payment, for matching it back. '
                  'Blank = the payment number. Max 35 characters.')
    bank_narration        = models.CharField(
        max_length=140, blank=True, default='',
        help_text='What the payee sees on their statement. Blank = the '
                  'description, then the reference, then the payment number.')
    # Where FNB emails the proof of payment (POP). Captured when the payment is
    # entered so the payee/vendor gets their POP without anyone chasing it
    # (Tlamelo, CFO 2026-08-22). Defaults to Accounts; editable per payment. A
    # later phase wires DeepSeek to suggest the right address from history.
    remittance_email      = models.EmailField(
        max_length=254, blank=True, default=default_pop_email,
        help_text='Where FNB sends the proof of payment. Defaults to Accounts; '
                  'change it per payment. Blank = no POP email requested.')

    # Stamped when this payment is placed in a SUBMITTED FNB EFT batch — a hard
    # guard so the same payment can never be sent to the bank (paid) twice
    # (Fable audit 2026-07-08).
    bank_submitted_at = models.DateTimeField(null=True, blank=True)
    status         = models.CharField(
                         max_length=12, choices=Status.choices, default=Status.DRAFT,
                     )
    journal_entry  = models.ForeignKey(
                         'ledger.JournalEntry', null=True, blank=True,
                         on_delete=models.SET_NULL, related_name='payments',
                     )

    # Outbound payment dual-authorisation (CFO-mandated for >threshold).
    # First approver is `created_by` (the person who confirmed amount/vendor).
    # Above threshold a SECOND approver is required and must NOT be the same
    # user. Below threshold this stays null and confirm() runs single-signed.
    secondary_approved_at = models.DateTimeField(null=True, blank=True)
    secondary_approved_by = models.ForeignKey(
                                User, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='payments_secondary_approved',
                            )

    # ── PAY-003 tier maker-checker (CFO/Oprah directive 2026-05-27) ─────────
    # Mirrors PAY-001's bill tier ladder (billing.BillApprovalPolicy): route
    # by amount_bwp → Tier1 Finance Manager / Tier2 Head of Finance / Tier3
    # CFO, with the unset-threshold → Tier 3 fail-safe. The approver
    # approves/rejects with a MANDATORY comment; the creator/submitter can
    # never approve their own payment (SoD). No SENT payment posts to GL
    # while approval_status is PENDING or REJECTED.
    approval_status        = models.CharField(
                                 max_length=15, choices=ApprovalStatus.choices,
                                 default=ApprovalStatus.NOT_REQUIRED,
                             )
    approval_tier          = models.PositiveSmallIntegerField(null=True, blank=True)
    approval_comment       = models.TextField(blank=True, default='')
    submitted_for_approval_by = models.ForeignKey(
                                 User, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='payments_submitted',
                             )
    submitted_for_approval_at = models.DateTimeField(null=True, blank=True)
    approval_decided_by    = models.ForeignKey(
                                 User, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='payments_approval_decided',
                             )
    approval_decided_at    = models.DateTimeField(null=True, blank=True)

    created_by     = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='payments_created',
                     )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Payment'
        verbose_name_plural = 'Payments'
        # PERF (2026-07-17): the payments list filters on status + orders by
        # payment_date, and every list paginates over -created_at (BaseModel
        # ordering) with no index. Add both so the list is an index scan.
        indexes = [
            models.Index(fields=['status', '-payment_date'], name='payment_status_date_idx'),
            models.Index(fields=['-created_at'], name='payment_created_at_idx'),
        ]

    def __str__(self):
        return f"{self.payment_number} — {self.contact.name} {self.amount}"

    # ------------------------------------------------------------------
    # save — auto number, auto amount_bwp, immutability guard
    # ------------------------------------------------------------------

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        if not self.payment_number:
            self.payment_number = _generate_payment_number(self.payment_type)

        # Auto-calculate BWP amount
        if self.amount and self.exchange_rate:
            self.amount_bwp = _bwp(self.amount, self.exchange_rate)

        # PAY-CAP-01 (CFO 2026-08-15, Manus QC F3): a P62,403,392,335.00
        # (P62.4bn) draft PAY-IN typo sat unblocked in prod — cash is ~P7.4m,
        # so anything above a sane per-payment ceiling is a data-hygiene bug
        # not a real payment. Reject before it pollutes any downstream tile,
        # exception or FX conversion. Override with the PAYMENT_MAX_AMOUNT_BWP
        # setting when a genuine outsize record is truly needed.
        max_bwp_raw = getattr(settings, 'PAYMENT_MAX_AMOUNT_BWP', '100000000')
        try:
            max_bwp = Decimal(str(max_bwp_raw))
        except Exception:  # noqa: BLE001
            max_bwp = Decimal('100000000')
        if self.amount_bwp is not None and abs(self.amount_bwp) > max_bwp:
            raise ValidationError(
                f"Payment amount {self.amount_bwp:,.2f} BWP exceeds the "
                f"{max_bwp:,.0f} BWP per-payment ceiling (PAY-CAP-01). "
                "If this is a genuine payment, raise PAYMENT_MAX_AMOUNT_BWP "
                "or contact the CFO."
            )

        # Validate bank account
        if self.bank_account_id and not self.bank_account.is_bank_account:
            raise ValidationError(
                f"Account {self.bank_account} is not a bank account. "
                "Only accounts with 'Is bank account' enabled can be used."
            )

        # Immutability: block edits to confirmed/reconciled/cancelled payments
        if self.pk:
            try:
                db_status = Payment.objects.values_list('status', flat=True).get(pk=self.pk)
                if db_status in (self.Status.CONFIRMED, self.Status.RECONCILED,
                                 self.Status.CANCELLED):
                    raise ValidationError(
                        f"{self.payment_number} is {db_status} and cannot be modified."
                    )
            except Payment.DoesNotExist:
                pass

        super().save(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Journal entry construction (private)
    # ------------------------------------------------------------------

    def _compute_early_pay_discount_bwp(self) -> Decimal:
        """
        Walk this payment's allocations and sum the early-payment discount
        we are entitled to take, based on each invoice's contact PaymentTerm.

        For each allocation, if the bill's contact has a PaymentTerm whose
        discount window (issue_date + discount_days) >= payment_date, then:
            discount = amount_allocated * discount_pct / 100

        Returns BWP. Quietly returns ZERO when no term is set or the
        discount has already lapsed.
        """
        if not self.pk:
            return ZERO
        total = ZERO
        for alloc in self.allocations.select_related('invoice', 'invoice__contact').all():
            bill   = alloc.invoice
            term   = getattr(bill.contact, 'payment_term', None)
            if term is None or not term.is_active:
                continue
            for ln in term.lines.order_by('sequence_no', 'created_at'):
                if not ln.discount_pct or not ln.discount_days:
                    continue
                deadline = bill.issue_date + datetime.timedelta(days=int(ln.discount_days))
                if self.payment_date <= deadline:
                    take = (alloc.amount_allocated * ln.discount_pct / HUNDRED).quantize(
                        TWO_PLACES, rounding=ROUND_HALF_UP,
                    )
                    # discount is in invoice currency — convert to BWP using
                    # this payment's locked exchange rate
                    total += (take * self.exchange_rate).quantize(
                        TWO_PLACES, rounding=ROUND_HALF_UP,
                    )
                    break
        return total

    # ------------------------------------------------------------------
    # What the BANK is instructed to pay
    # ------------------------------------------------------------------

    def bank_instruction_amounts(self):
        """(original currency, BWP) that actually leaves our bank for the payee.

        WHICH FIGURE IS CORRECT TO SEND, and why. The GL credits Bank with the
        gross LESS withholding tax LESS any early-settlement discount (see
        `bank_credit_bwp` in _create_journal_entry): the withheld 10% never
        leaves us — it is booked to WHT payable (2170) and remitted to BURS
        separately — and the discount is money the supplier agreed we may keep.
        The cash leaving the account IS what the payee is owed, so that is the
        figure the bank must be instructed.

        Both rails used to send the gross (`p.amount` in fnb/payments.py,
        `p.amount_bwp or p.amount` in payments/eft_export.py). A P100,000
        payment to a non-exempt broker therefore booked Cr Bank 90,000 +
        Cr WHT payable 10,000 while the file told the bank 100,000: the broker
        is overpaid by the withholding and a BURS liability stands against cash
        that already left. Omni moves no money, but this is the number the CFO
        sees presented as correct when he authorises on his phone.

        Derived with the SAME arithmetic and the same ROUND_HALF_UP as the
        journal entry — including the divide-through-the-rate for the original
        currency — so the bank file and the ledger cannot drift by a thebe.
        """
        gross = self.amount or ZERO
        # amount_bwp defaults to 0 and is only filled in by save(), and the
        # payload builder is also called against unsaved preview stand-ins —
        # so derive it the way save() does rather than instructing zero.
        gross_bwp = self.amount_bwp or _bwp(gross, self.exchange_rate)
        if self.payment_type != self.PaymentType.SENT:
            return gross, gross_bwp

        # WHT. A confirmed payment has the record that was actually booked;
        # before confirm() the same rule confirm() will apply is computed here,
        # so a draft row submitted to FNB is never sent gross by accident.
        wht_bwp = ZERO
        # ONLY a booked WithholdingTaxRecord nets the instruction down.
        #
        # The first version also computed the tax speculatively for any
        # contact_type='broker' with wht_exempt False — and wht_exempt DEFAULTS
        # to False (billing/models.py). So a broker payment loaded to the bank
        # before confirm() would have gone out 10% LIGHTER than the invoice,
        # on Omni's guess that withholding applies. If Alpha Direct is not
        # actually operating the 10% BURS withholding, that underpays the
        # broker — and whether it operates it is a TAX POSITION, not a thing
        # this function gets to assume.
        #
        # Keyed on the record, the two sides cannot disagree: confirm() books
        # the record and credits Bank net in the SAME transaction, so before
        # confirm there is no journal entry to be out of step with, and after
        # it there is a figure rather than a guess.
        record = (WithholdingTaxRecord.objects.filter(payment_id=self.pk).first()
                  if self.pk else None)
        if record is not None:
            wht_bwp = _bwp(record.wht_amount, self.exchange_rate)

        # Early-settlement discount — same priority order as confirm().
        preset = getattr(self, '_early_pay_discount_bwp', None)
        if preset is not None and Decimal(str(preset)) > ZERO:
            discount_bwp = Decimal(str(preset)).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP)
        else:
            discount_bwp = self._compute_early_pay_discount_bwp()
        if discount_bwp > gross_bwp:
            discount_bwp = gross_bwp

        bank_bwp = gross_bwp - wht_bwp - discount_bwp
        if bank_bwp < ZERO:
            bank_bwp = ZERO
        try:
            bank_orig = (bank_bwp / self.exchange_rate).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP)
        except (ZeroDivisionError, InvalidOperation):
            bank_orig = bank_bwp
        return bank_orig, bank_bwp

    def _get_counterpart_account(self, get_acct):
        """Return the receivable or payable account for this payment."""
        ct = self.contact.contact_type
        if self.payment_type == self.PaymentType.RECEIVED:
            if ct == 'customer':
                # An invoice may sit in a receivable of its own rather than premium
                # receivable (1210) — the Veritas salvage invoices do (CFO
                # 19-Sep-2026). The receipt must clear the SAME account the invoice
                # debited, or 260001 stays open and 1210 goes short. Only honoured
                # when every allocated invoice agrees, so a mixed receipt still
                # behaves exactly as before.
                acct = receivable_override(
                    alloc.invoice for alloc in self.allocations.select_related('invoice').all()
                )
                if acct is not None:
                    return acct
                return get_acct('1210')
            if ct == 'reinsurer':
                return get_acct('1230')
            return get_acct('1240')
        else:  # SENT
            if ct == 'broker':
                return get_acct('2130')
            if ct == 'reinsurer':
                return get_acct('2120')
            if ct == 'employee':
                return get_acct('2150')
            return get_acct('2140')

    def _create_journal_entry(self, user, wht_amount_bwp, net_amount_bwp,
                              discount_amount_bwp=None):
        """
        Build the journal entry lines for this payment.
        Returns the unposted JournalEntry.

        discount_amount_bwp: BWP amount of early-payment discount captured
        on this payment. When > 0 (SENT path only) an extra credit line is
        posted against the "Discount received" P&L account and the bank
        credit is reduced by the same amount.
        """
        from ledger.models import Account, JournalEntry, JournalEntryLine

        def get_acct(code):
            try:
                return Account.objects.get(code=code)
            except Account.DoesNotExist:
                raise ValidationError(
                    f"Required account {code} not found. "
                    "Run setup_chart_of_accounts first."
                )

        if discount_amount_bwp is None:
            discount_amount_bwp = ZERO

        bank    = self.bank_account
        counter = self._get_counterpart_account(get_acct)
        gross   = self.amount_bwp
        je_type = (JournalEntry.JournalType.CASH_RECEIPTS
                   if self.payment_type == self.PaymentType.RECEIVED
                   else JournalEntry.JournalType.CASH_PAYMENTS)

        je = JournalEntry(
            entry_date    = self.payment_date,
            description   = f"{self.payment_number} — {self.contact.name}",
            source_type   = 'payment',
            source_id     = self.pk,
            journal_type  = je_type,
            currency_code = self.currency_code,
            exchange_rate = self.exchange_rate,
            company       = self.company,
            created_by    = user,
        )
        je.save(audit_user=user)

        if self.payment_type == self.PaymentType.RECEIVED:
            # Dr: Bank account
            JournalEntryLine.objects.create(
                journal_entry=je, account=bank,
                description=f"Received: {self.reference}",
                debit_amount=self.amount, credit_amount=ZERO,
                debit_bwp=gross,          credit_bwp=ZERO,
            )
            # Cr: Receivable
            JournalEntryLine.objects.create(
                journal_entry=je, account=counter,
                description=f"{self.payment_number} — {self.contact.name}",
                debit_amount=ZERO,        credit_amount=self.amount,
                debit_bwp=ZERO,           credit_bwp=gross,
            )

        else:  # SENT
            has_wht      = wht_amount_bwp > ZERO
            has_discount = discount_amount_bwp > ZERO

            # Dr: Payable (gross — the full amount we owed the vendor)
            JournalEntryLine.objects.create(
                journal_entry=je, account=counter,
                description=f"{self.payment_number} — {self.contact.name}",
                debit_amount=self.amount, credit_amount=ZERO,
                debit_bwp=gross,          credit_bwp=ZERO,
            )

            # Cash actually leaving the bank = net (after WHT) minus discount.
            bank_credit_bwp = net_amount_bwp - discount_amount_bwp
            # Original-currency bank credit derives from the BWP-side cash by
            # dividing through the locked exchange rate so the JE balances in
            # both currency columns.
            try:
                bank_credit_orig = (bank_credit_bwp / self.exchange_rate).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP,
                )
            except (ZeroDivisionError, Exception):
                bank_credit_orig = bank_credit_bwp

            # Cr: Bank (net of WHT and net of discount)
            JournalEntryLine.objects.create(
                journal_entry=je, account=bank,
                description=f"Paid: {self.reference}",
                debit_amount=ZERO,          credit_amount=bank_credit_orig,
                debit_bwp=ZERO,             credit_bwp=bank_credit_bwp,
            )
            # Cr: WHT payable (if applicable)
            if has_wht:
                wht_account = get_acct('2170')
                wht_orig = (wht_amount_bwp / self.exchange_rate).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP
                )
                JournalEntryLine.objects.create(
                    journal_entry=je, account=wht_account,
                    description=f"WHT on {self.payment_number}",
                    debit_amount=ZERO,     credit_amount=wht_orig,
                    debit_bwp=ZERO,        credit_bwp=wht_amount_bwp,
                )
            # Cr: Discount received (P&L credit) — captured early-pay rebate
            if has_discount:
                disc_account = _get_discount_received_account()
                if disc_account is not None:
                    try:
                        disc_orig = (discount_amount_bwp / self.exchange_rate).quantize(
                            TWO_PLACES, rounding=ROUND_HALF_UP,
                        )
                    except (ZeroDivisionError, Exception):
                        disc_orig = discount_amount_bwp
                    JournalEntryLine.objects.create(
                        journal_entry=je, account=disc_account,
                        description=f"Early-pay discount on {self.payment_number}",
                        debit_amount=ZERO,    credit_amount=disc_orig,
                        debit_bwp=ZERO,       credit_bwp=discount_amount_bwp,
                    )

        return je

    # ------------------------------------------------------------------
    # PAY-003 tier maker-checker (mirror of PAY-001 bill tier ladder)
    # ------------------------------------------------------------------

    def _approval_policy(self):
        """The canonical tier engine lives on billing.BillApprovalPolicy.

        Payments reuse the SAME ladder so a bill and its payment route by
        the identical thresholds (Tier1 Finance Manager, Tier2 Head of
        Finance, Tier3 CFO; unset ceiling → Tier 3 fail-safe).
        """
        from billing.models import BillApprovalPolicy
        return BillApprovalPolicy.current()

    def assign_payment_tier(self):
        """Return (tier:int, role:str) for this payment's amount_bwp."""
        policy = self._approval_policy()
        tier = policy.assign_tier(self.amount_bwp or ZERO)
        return tier, policy.tier_role(tier)

    def _allowed_approver_titles(self) -> set:
        """Titles allowed to approve this payment's tier.

        The tier's own role, plus the top tier (CFO can approve anything).
        Normalised the same way billing.Invoice.approve_bill does.
        """
        policy = self._approval_policy()
        tier = self.approval_tier or 3
        return {
            (policy.tier_role(tier) or '').strip().lower().replace(' ', '_'),
            (policy.tier3_role or 'cfo').strip().lower().replace(' ', '_'),
        }

    def _approver_role(self, user):
        """Classify an approver for the outbound dual-control quorum
        (CFO ruling 2026-07-07): 'fm' = Finance Manager / Financial Controller,
        'exec' = CFO or CEO (a superuser founder). Returns None if not
        authorised to approve an outbound payment.
        """
        if user is None:
            return None
        title = ''
        try:
            title = (user.profile.title or '').strip().lower().replace(' ', '_')
        except Exception:  # noqa: BLE001
            pass
        if title in ('finance_manager', 'financial_controller'):
            return PaymentApproval.Role.FM
        if title == 'cfo' or getattr(user, 'is_superuser', False):
            return PaymentApproval.Role.EXEC
        return None

    def _outbound_quorum(self):
        """(met, n_fm_fc, n_exec). Dual control (CFO ruling 2026-07-07):
        an outbound bank payment needs ONE FM/FC approval AND ONE distinct
        CFO/CEO approval — two different people, one finance one exec.
        PaymentApproval's unique(payment, approver) guarantees the counts are
        distinct people, and the maker (creator/submitter) can never approve
        their own payment (SoD, enforced in approve_payment)."""
        roles = list(
            PaymentApproval.objects.filter(payment=self).values_list('role', flat=True)
        )
        n_fm = sum(1 for r in roles if r == PaymentApproval.Role.FM)
        n_exec = sum(1 for r in roles if r == PaymentApproval.Role.EXEC)
        return (n_fm >= 1 and n_exec >= 1), n_fm, n_exec

    @transaction.atomic
    def submit_for_approval(self, user, *, notify=True):
        """Route a DRAFT outbound payment into the tier approval queue.

        Assigns the tier from amount_bwp and parks the payment at
        approval_status=PENDING. confirm() will refuse to post until an
        authorised, different approver approves it.
        """
        if not self.pk:
            raise ValidationError("Save the payment before submitting it for approval.")
        if self.status != self.Status.DRAFT:
            raise ValidationError(
                f"Only draft payments can be submitted for approval. "
                f"Current status: {self.status}."
            )
        if self.payment_type != self.PaymentType.SENT:
            raise ValidationError("Only outbound (sent) payments route through tier approval.")
        # Fable audit 2026-07-08: a payment already awaiting signatures must not
        # be re-submitted (a stale tab / direct call would wipe the signatures
        # already collected and re-spam approver tasks). Reject it first to amend.
        if self.approval_status == self.ApprovalStatus.PENDING:
            raise ValidationError(
                "This payment is already awaiting approval. Reject it first if it "
                "needs changes — that clears the signatures and lets you re-submit.")
        if not self.amount or self.amount <= ZERO:
            raise ValidationError("Payment amount must be greater than zero.")
        # Keep amount_bwp current before tier routing.
        if self.amount and self.exchange_rate:
            self.amount_bwp = _bwp(self.amount, self.exchange_rate)

        # Fable audit 2026-07-07: signatures never survive a reject/edit/
        # re-submit cycle. Without this, 2 FM signatures from round 1 plus a
        # single fresh CFO signature would meet quorum on an AMENDED payment
        # the FMs never saw.
        PaymentApproval.objects.filter(payment=self).delete()

        tier, role = self.assign_payment_tier()
        self.approval_tier            = tier
        self.approval_status          = self.ApprovalStatus.PENDING
        self.submitted_for_approval_by = user
        self.submitted_for_approval_at = timezone.now()
        self.approval_decided_by      = None
        self.approval_decided_at      = None
        self.approval_comment         = ''
        self.save(
            audit_user=user,
            audit_description=(
                f"Submitted {self.payment_number} for Tier {tier} ({role}) approval"
            ),
        )
        # Tell the signers (HIGH OmniTask each, like JE approval) — after
        # commit so a rolled-back submit never leaves ghost tasks.
        if notify:
            # Tell the signers (one HIGH OmniTask each). Bulk upload passes
            # notify=False and raises ONE summary task for the whole batch
            # instead of flooding every approver's inbox (Fable audit).
            def _notify(p=self, u=user):
                try:
                    from core.notifications import notify_payment_submitted
                    notify_payment_submitted(p, submitter=u)
                except Exception:   # noqa: BLE001 — never block the submit
                    logger.exception('payment approval OmniTask fan-out failed')
            transaction.on_commit(_notify)
        return tier, role

    @transaction.atomic
    def approve_payment(self, user, comment: str):
        """Approve a pending payment and post it to the GL (DR AP / CR Bank).

        Enforces: pending state, SoD (creator/submitter ≠ approver), the
        approver's title matches the assigned tier (or CFO), and a
        MANDATORY non-empty comment. On success calls confirm() so the GL
        post + allocation update + bill→Paid all happen in one transaction.
        """
        # Concurrency guard (premortem 2026-06-10): lock + re-read committed
        # approval_status. Without it two simultaneous approve calls
        # (double-click / retry) both see PENDING and both call confirm() →
        # the payment posts to the GL TWICE = double cash-out. The row lock
        # serialises them; the loser sees APPROVED and aborts.
        if self.pk:
            _locked = (
                Payment.objects.select_for_update()
                .filter(pk=self.pk)
                .values_list('approval_status', flat=True)
                .first()
            )
            if _locked is not None and _locked != self.ApprovalStatus.PENDING:
                raise ValidationError(
                    f"Only pending-approval payments can be approved. "
                    f"Current: {_locked}."
                )
        if self.approval_status != self.ApprovalStatus.PENDING:
            raise ValidationError(
                f"Only pending-approval payments can be approved. "
                f"Current: {self.get_approval_status_display()}."
            )
        comment = (comment or '').strip()
        if not comment:
            raise ValidationError("An approval comment is mandatory.")
        if user.id == self.created_by_id or user.id == self.submitted_for_approval_by_id:
            raise ValidationError(
                "Segregation of duties: the payment's creator/submitter "
                "cannot approve it."
            )
        role = self._approver_role(user)   # 'fm' (FM/FC) | 'exec' (CFO/CEO) | None
        if self.payment_type == self.PaymentType.SENT:
            # CFO ruling 2026-07-05: outbound bank payments are approved only by
            # Finance Managers / Financial Controllers and the CFO / CEO.
            if role is None:
                raise ValidationError(
                    "Outbound payments are approved by Finance Managers / Financial "
                    "Controllers and a CFO/CEO. Your title is not authorised to approve."
                )
        else:
            title = ''
            try:
                title = (user.profile.title or '').strip().lower().replace(' ', '_')
            except Exception:
                pass
            allowed = self._allowed_approver_titles()
            if title not in allowed:
                raise ValidationError(
                    f"Your approver title ({title or 'unset'}) is not authorised "
                    f"for Tier {self.approval_tier}. Need one of: "
                    + ', '.join(sorted(t for t in allowed if t))
                )

        # Record this signature (one per user — the unique constraint + this
        # guard stop the same person signing twice to fake a quorum).
        if PaymentApproval.objects.filter(payment=self, approver=user).exists():
            raise ValidationError("You have already approved this payment.")
        PaymentApproval.objects.create(
            payment=self, approver=user,
            role=(role or PaymentApproval.Role.FM), comment=comment[:2000],
        )

        # CFO ruling 2026-07-07 (supersedes 2026-07-05): every OUTBOUND (sent)
        # bank payment needs DUAL CONTROL — ONE FM/FC approval AND ONE distinct
        # CFO/CEO approval — before it posts to the GL. Until the quorum is met
        # the payment stays PENDING, and confirm() refuses to post it.
        if self.payment_type == self.PaymentType.SENT:
            met, n_fm, n_exec = self._outbound_quorum()
            self.approval_comment = comment[:2000]
            if not met:
                self.save(
                    audit_user=user,
                    audit_description=(
                        f"Approval recorded on {self.payment_number} "
                        f"({n_fm}×FM/FC + {n_exec}×CFO/CEO; need 1×FM/FC + 1×CFO/CEO)"
                    ),
                )
                return {'quorum_met': False, 'fm_fc': n_fm, 'exec': n_exec,
                        'need': '1 Finance Manager/Controller + 1 CFO/CEO'}
            self.approval_status       = self.ApprovalStatus.APPROVED
            self.approval_decided_by   = user
            self.approval_decided_at   = timezone.now()
            self.secondary_approved_by = user
            self.secondary_approved_at = timezone.now()
            self.save(
                audit_user=user,
                audit_description=(
                    f"Quorum met ({n_fm}×FM/FC + {n_exec}×CFO/CEO) — approved "
                    f"{self.payment_number}"
                ),
            )
            self.confirm(user)
            def _close(p=self):
                try:
                    from core.notifications import close_payment_approval_tasks
                    close_payment_approval_tasks(p, 'approved')
                except Exception:   # noqa: BLE001
                    logger.exception('payment approval task close failed')
            transaction.on_commit(_close)
            return {'quorum_met': True, 'fm_fc': n_fm, 'exec': n_exec}

        # Non-outbound (received) — single approval posts (legacy behaviour).
        self.approval_status     = self.ApprovalStatus.APPROVED
        self.approval_comment    = comment[:2000]
        self.approval_decided_by = user
        self.approval_decided_at = timezone.now()
        self.secondary_approved_by = user
        self.secondary_approved_at = timezone.now()
        self.save(
            audit_user=user,
            audit_description=f"Approved {self.payment_number} (Tier {self.approval_tier})",
        )
        self.confirm(user)
        return {'quorum_met': True, 'fm_fc': 0, 'exec': 0}

    @transaction.atomic
    def reject_payment(self, user, comment: str):
        """Reject a pending payment. Leaves it at DRAFT so it can be amended
        and re-submitted. Comment mandatory, SoD enforced."""
        if self.approval_status != self.ApprovalStatus.PENDING:
            raise ValidationError(
                f"Only pending-approval payments can be rejected. "
                f"Current: {self.get_approval_status_display()}."
            )
        comment = (comment or '').strip()
        if not comment:
            raise ValidationError("A rejection comment is mandatory.")
        if user.id == self.created_by_id or user.id == self.submitted_for_approval_by_id:
            raise ValidationError(
                "Segregation of duties: the payment's creator/submitter "
                "cannot reject it."
            )
        self.approval_status     = self.ApprovalStatus.REJECTED
        self.approval_comment    = comment[:2000]
        self.approval_decided_by = user
        self.approval_decided_at = timezone.now()
        self.save(
            audit_user=user,
            audit_description=f"Rejected {self.payment_number} (Tier {self.approval_tier})",
        )
        def _close(p=self):
            try:
                from core.notifications import close_payment_approval_tasks
                close_payment_approval_tasks(p, 'rejected')
            except Exception:   # noqa: BLE001
                logger.exception('payment approval task close failed')
        transaction.on_commit(_close)

    # ------------------------------------------------------------------
    # confirm() — the main public method
    # ------------------------------------------------------------------

    @transaction.atomic
    def confirm(self, user, *, _system=False):
        """
        Create and post the journal entry, apply WHT if applicable,
        update invoice allocations, and lock this payment as confirmed.

        ``_system=True`` is reserved for automated pay-run/batch paths that
        carry their own controls — it exempts the payment from the
        interactive dual-control quorum requirement below. Never expose it
        to a request handler.
        """
        if not self.pk:
            raise ValidationError("Save the payment before confirming it.")

        # Concurrency guard (premortem 2026-06-10): lock + re-read committed
        # status so two callers racing into confirm() (approve path + a
        # parallel retry, or two pay-run threads) can't both post the GL
        # entry. The row lock serialises them; the loser sees CONFIRMED and
        # aborts here instead of double-posting.
        _locked = (
            Payment.objects.select_for_update()
            .filter(pk=self.pk)
            .values_list('status', flat=True)
            .first()
        )
        if _locked is not None and _locked != self.Status.DRAFT:
            raise ValidationError(
                f"Only draft payments can be confirmed. Current status: {_locked}."
            )

        if self.status != self.Status.DRAFT:
            raise ValidationError(
                f"Only draft payments can be confirmed. Current status: {self.status}."
            )

        if not self.amount or self.amount <= ZERO:
            raise ValidationError("Payment amount must be greater than zero.")

        # ── PAY-003 tier gate ──────────────────────────────────────────────
        # A payment routed into the tier queue cannot post to GL until an
        # authorised approver (≠ creator/submitter) has approved it. Payments
        # that never entered the queue (NOT_REQUIRED — e.g. the pay-run path)
        # fall through to the legacy dual-auth check below.
        if self.approval_status == self.ApprovalStatus.PENDING:
            raise ValidationError(
                f"{self.payment_number} is pending Tier {self.approval_tier} "
                "approval. The assigned approver must approve it before it "
                "can post to the GL."
            )
        if self.approval_status == self.ApprovalStatus.REJECTED:
            raise ValidationError(
                f"{self.payment_number} was rejected in approval and cannot "
                "post. Amend and re-submit it for approval."
            )
        # CFO ruling 2026-07-05 hardening (Fable audit 2026-07-07): an
        # interactive OUTBOUND payment may not post while NOT_REQUIRED —
        # that would sidestep the dual-control quorum entirely (create a
        # draft, hit /confirm/, GL posted with zero approvals). Outbound
        # posts happen through approve_payment() once the quorum is met.
        # Automated pay-run/batch paths pass _system=True.
        if (self.payment_type == self.PaymentType.SENT
                and self.approval_status == self.ApprovalStatus.NOT_REQUIRED
                and not _system):
            raise ValidationError(
                f"{self.payment_number} has not been through payment approval. "
                "Submit it for approval — it posts automatically once "
                "a Finance Manager/Controller and a CFO/CEO have signed."
            )

        # ── Vendor KYC guard (CFO directive — procurement/kyc_service.py) ──
        # Additive: passes through silently for missing / flagged / ok KYC.
        # Only blocks when KYC is EXPIRED and amount > KYC_BLOCK_THRESHOLD_BWP.
        from procurement.kyc_service import check_kyc_for_payment
        check_kyc_for_payment(self)

        # ── Structural audit 2026-05-24: fiscal-period close check ─────────
        # Canonical AP invariant: no payment may post into a closed fiscal
        # period. Mirrors JournalEntry._validate_for_posting; closes the
        # loophole that lets a late payment land inside a CFO-locked period
        # and corrupt the audited TB.
        from ledger.models import FiscalPeriod as _FP
        _open_period = _FP.get_open_period_for_date(self.payment_date)
        if _open_period is None:
            raise ValidationError(
                f"No open fiscal period covers payment_date "
                f"{self.payment_date}. Reopen the period or amend the "
                "payment date before confirming."
            )

        # ── Dual authorisation above threshold (CFO-mandated) ──────────────
        # Outbound payments above OutboundPaymentPolicy.dual_auth_threshold
        # require a second approver. Confirm() is the second-approver action;
        # the first approver is whoever created the draft. Same user blocked.
        if self.payment_type == self.PaymentType.SENT:
            policy = OutboundPaymentPolicy.current()
            if self.amount_bwp > policy.dual_auth_threshold:
                if user.id == self.created_by_id:
                    raise ValidationError(
                        "Segregation of duties: this outbound payment exceeds "
                        f"the dual-authorisation threshold "
                        f"(BWP {policy.dual_auth_threshold:,.2f}). "
                        "A second approver — different from the creator — "
                        "must confirm it."
                    )
                self.secondary_approved_by = user
                self.secondary_approved_at = timezone.now()

        # ── Vendor bank account check (CFO-mandated control) ───────────────
        # Outbound electronic payments must hit an APPROVED vendor bank
        # account. Cash and cheque are exempt — there's no destination
        # bank account in those cases.
        ELECTRONIC_METHODS = {
            self.PaymentMethod.BANK_TRANSFER,
            self.PaymentMethod.DEBIT_ORDER,
            self.PaymentMethod.MOBILE_MONEY,
            self.PaymentMethod.GATEWAY,
        }
        if (self.payment_type == self.PaymentType.SENT
                and self.payment_method in ELECTRONIC_METHODS
                and self.is_once_off):
            # One-off payee: destination bank is captured inline (no vendor-bank
            # register). Still requires an account number, and the payment still
            # passes the dual-control (1-FM/FC + 1-CFO/CEO) quorum before it reaches here.
            if not (self.payee_account_number or '').strip():
                raise ValidationError(
                    "One-off payment needs the payee's bank account number.")
        elif (self.payment_type == self.PaymentType.SENT
                and self.payment_method in ELECTRONIC_METHODS):
            from procurement.models import VendorBankAccount
            if not self.vendor_bank_account_id:
                raise ValidationError(
                    f"Outbound electronic payment to {self.contact.name} "
                    "requires a vendor bank account. Have the Finance Manager "
                    "approve one on /vendor-banking before confirming."
                )
            if self.vendor_bank_account.status != VendorBankAccount.Status.ACTIVE:
                raise ValidationError(
                    f"Vendor bank account is "
                    f"{self.vendor_bank_account.get_status_display()}, "
                    "not ACTIVE. Get it approved by the Finance Manager first."
                )
            if self.vendor_bank_account.contact_id != self.contact_id:
                raise ValidationError(
                    f"Vendor bank account belongs to "
                    f"{self.vendor_bank_account.contact.name}, not to "
                    f"{self.contact.name}. Pick the correct account."
                )

        # ── Bill ↔ PO match check (CFO-mandated control) ───────────────────
        # Outbound payments that are allocated to a vendor bill MUST go
        # through a cleared bill ↔ PO match — either a clean MATCHED state,
        # or an explicitly OVERRIDE-approved variance. A bill stuck in
        # NEEDS_TIER1_APPROVAL / NEEDS_TIER2_APPROVAL / TIER1_APPROVED /
        # REJECTED cannot be paid.
        if self.payment_type == self.PaymentType.SENT:
            from billing.models import Invoice
            from procurement.models import POBillMatch

            allocated_bill_ids = list(
                self.allocations
                .filter(invoice__invoice_type=Invoice.InvoiceType.VENDOR_BILL)
                .values_list('invoice_id', flat=True)
            )
            for bill_id in allocated_bill_ids:
                payable = POBillMatch.objects.filter(
                    bill_id=bill_id,
                    match_status__in=[
                        POBillMatch.MatchStatus.MATCHED,
                        POBillMatch.MatchStatus.OVERRIDE,
                    ],
                ).exists()
                if not payable:
                    raise ValidationError(
                        f"Cannot confirm: vendor bill is not cleared by a "
                        f"completed PO match. Resolve the bill ↔ PO match "
                        f"(approve any variance) before paying."
                    )

        gross_bwp = self.amount_bwp

        # ── Early-payment discount calculation ───────────────────────────
        # SENT payments only. Priority:
        #   1. self._early_pay_discount_bwp set by the pay-run commit
        #      service (which already computed it from the PaymentTerm)
        #   2. Walk allocations and recompute from each bill's contact
        #      PaymentTerm, treating payment_date as the discount probe.
        discount_amount_bwp = ZERO
        if self.payment_type == self.PaymentType.SENT:
            preset = getattr(self, '_early_pay_discount_bwp', None)
            if preset is not None and preset > ZERO:
                discount_amount_bwp = Decimal(str(preset)).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP,
                )
            else:
                discount_amount_bwp = self._compute_early_pay_discount_bwp()
            # Never let the discount exceed the cash leaving the bank
            if discount_amount_bwp > gross_bwp:
                discount_amount_bwp = gross_bwp

        # ── WHT calculation ──────────────────────────────────────────────
        wht_amount_bwp = ZERO
        wht_record     = None

        is_broker_payment = (
            self.payment_type == self.PaymentType.SENT
            and self.contact.contact_type == 'broker'
            and not self.contact.wht_exempt
        )

        if is_broker_payment:
            tax_year_start = _get_tax_year_start(self.payment_date)
            cumulative_ytd = (
                WithholdingTaxRecord.objects
                .filter(contact=self.contact, tax_year_start=tax_year_start)
                .aggregate(total=Sum('gross_amount'))['total']
                or ZERO
            )
            wht_amount     = (self.amount * WHT_RATE / 100).quantize(
                                 TWO_PLACES, rounding=ROUND_HALF_UP
                             )
            wht_amount_bwp = _bwp(wht_amount, self.exchange_rate)
            net_amount     = self.amount - wht_amount
            net_amount_bwp = gross_bwp - wht_amount_bwp

            wht_record = WithholdingTaxRecord(
                payment          = self,
                contact          = self.contact,
                gross_amount     = self.amount,
                wht_rate         = WHT_RATE,
                wht_amount       = wht_amount,
                net_amount       = net_amount,
                tax_year_start   = tax_year_start,
                cumulative_paid_ytd = cumulative_ytd + self.amount,
            )
        else:
            net_amount_bwp = gross_bwp

        # ── Journal entry ─────────────────────────────────────────────
        je = self._create_journal_entry(
            user, wht_amount_bwp, net_amount_bwp,
            discount_amount_bwp=discount_amount_bwp,
        )
        # Fable audit 2026-08-19 (Prathap penny test PAY-OUT-2026-000008):
        # scope the ledger raw-draft bypass to authorised branches only.
        #
        # confirm() is a shared method: outbound (SENT) payments arrive here
        # ONLY when the dual-control quorum is met (approve_payment sets
        # approval_status=APPROVED before calling confirm), and system-driven
        # pay-run/refund/salvage confirms pass _system=True. Both branches
        # ARE authorised — the JE post is not a "direct post from draft".
        # RECEIVED (unapproved) drafts confirmed via the /confirm/ endpoint
        # remain gated by the ledger guard as before.
        #
        # Without this scoping, if the CFO signed FIRST any FM/FC
        # second-signer's click ran je.post() as themselves — a non-superuser
        # without can_post_directly — and the ledger guard raised "Direct
        # posting is not permitted", stranding the payment in draft despite
        # quorum being met.
        _quorum_or_system = (
            _system
            or self.approval_status == self.ApprovalStatus.APPROVED
        )
        je.post(user=user, _allow_direct=_quorum_or_system)

        # ── Save WHT record ───────────────────────────────────────────
        if wht_record:
            wht_record.save()

        # ── Confirm payment ───────────────────────────────────────────
        self.status        = self.Status.CONFIRMED
        self.journal_entry = je
        # DB status is still DRAFT at this point so immutability guard passes
        self.save(
            audit_user=user,
            audit_description=f"Confirmed {self.payment_number}",
        )

        # ── Update invoice allocations ────────────────────────────────
        for allocation in self.allocations.select_related('invoice').all():
            _update_invoice_from_allocations(allocation.invoice)

        AuditLog.objects.create(
            table_name='Payment',
            record_id=str(self.pk),
            action=AuditLog.Action.POST,
            new_values={
                'status':        self.Status.CONFIRMED,
                'journal_entry': str(je.pk),
                'amount_bwp':    str(self.amount_bwp),
                'wht_applied':   bool(wht_record),
            },
            user=user,
            description=f"Confirmed {self.payment_number}, JE: {je.entry_number}",
        )


# ---------------------------------------------------------------------------
# Payment Allocation
# ---------------------------------------------------------------------------

class PaymentApproval(BaseModel):
    """One signature on an outbound payment's approval quorum.

    CFO ruling 2026-07-07 (dual control): an outbound bank payment posts only
    after ONE FM/FC approval AND ONE distinct CFO/CEO approval. Each approver
    signs at most once (unique constraint), and none may be the payment's
    creator/submitter (enforced in Payment.approve_payment).
    """
    class Role(models.TextChoices):
        FM   = 'fm',   'Finance Manager / Financial Controller'
        EXEC = 'exec', 'CFO / CEO'

    payment     = models.ForeignKey(
                      'Payment', on_delete=models.CASCADE, related_name='approvals',
                  )
    approver    = models.ForeignKey(
                      User, on_delete=models.PROTECT, related_name='payment_approvals',
                  )
    role        = models.CharField(max_length=4, choices=Role.choices)
    comment     = models.TextField(blank=True, default='')
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Payment Approval'
        verbose_name_plural = 'Payment Approvals'
        constraints = [
            models.UniqueConstraint(
                fields=['payment', 'approver'],
                name='uniq_payment_approver',
            ),
        ]

    def __str__(self):
        return f"{self.payment.payment_number} · {self.approver} ({self.role})"


class PaymentAllocation(BaseModel):
    """Links a payment to an invoice for partial or full settlement."""

    payment          = models.ForeignKey(
                           Payment, on_delete=models.CASCADE, related_name='allocations',
                       )
    invoice          = models.ForeignKey(
                           'billing.Invoice', on_delete=models.PROTECT,
                           related_name='allocations',
                       )
    amount_allocated = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Payment Allocation'
        verbose_name_plural = 'Payment Allocations'

    def __str__(self):
        return (
            f"{self.payment.payment_number} -> "
            f"{self.invoice.invoice_number} : {self.amount_allocated}"
        )

    def clean(self):
        """Structural audit 2026-05-24: overpayment guard.

        Canonical AP invariant: SUM(PaymentAllocation.amount_allocated) for an
        Invoice MUST NOT exceed invoice.total. Without this check a user
        could allocate the same Bill across multiple Payments for a total
        greater than the obligation, creating a phantom AP credit balance
        and ultimately a cash leak. django-ledger + OCA enforce this at the
        line level.
        """
        super().clean() if hasattr(super(), 'clean') else None
        from decimal import Decimal as _Decimal
        from django.core.exceptions import ValidationError
        amt = self.amount_allocated or _Decimal('0.00')
        if amt < 0:
            raise ValidationError({'amount_allocated': 'Allocation must be non-negative.'})
        if self.invoice_id and amt:
            total = self.invoice.total_amount or _Decimal('0.00')
            existing = (PaymentAllocation.objects
                        .filter(invoice_id=self.invoice_id)
                        .exclude(pk=self.pk)
                        .aggregate(s=models.Sum('amount_allocated'))['s']
                        or _Decimal('0.00'))
            if existing + amt > total + _Decimal('0.01'):  # 1c tolerance
                raise ValidationError({
                    'amount_allocated': (
                        f"Allocation {amt} would over-pay invoice "
                        f"{self.invoice.invoice_number}: prior allocations "
                        f"{existing} + this {amt} > invoice total {total}."
                    ),
                })

    def save(self, *args, **kwargs):
        # Run the overpayment guard before persisting so a buggy caller
        # cannot bypass clean() by going straight to .save().
        self.full_clean(exclude=['payment', 'invoice'])
        super().save(*args, **kwargs)
        # If the payment is already confirmed, update the invoice immediately
        if self.payment.status in (
            Payment.Status.CONFIRMED, Payment.Status.RECONCILED
        ):
            _update_invoice_from_allocations(self.invoice)


# ---------------------------------------------------------------------------
# Withholding Tax Record
# ---------------------------------------------------------------------------

class WithholdingTaxRecord(BaseModel):
    """
    BURS compliance record for WHT deducted on broker commission payments.
    Tracks cumulative YTD amounts for the annual P48,000 threshold.
    """

    payment             = models.OneToOneField(
                              Payment, on_delete=models.PROTECT,
                              related_name='wht_record',
                          )
    contact             = models.ForeignKey(
                              'billing.Contact', on_delete=models.PROTECT,
                              related_name='wht_records',
                          )
    gross_amount        = models.DecimalField(max_digits=18, decimal_places=2)
    wht_rate            = models.DecimalField(max_digits=5,  decimal_places=2)
    wht_amount          = models.DecimalField(max_digits=18, decimal_places=2)
    net_amount          = models.DecimalField(max_digits=18, decimal_places=2)
    tax_year_start      = models.DateField()
    cumulative_paid_ytd = models.DecimalField(max_digits=18, decimal_places=2)
    remitted_to_burs    = models.BooleanField(default=False)
    remittance_date     = models.DateField(null=True, blank=True)
    burs_reference      = models.CharField(max_length=100, null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Withholding Tax Record'
        verbose_name_plural = 'Withholding Tax Records'

    def __str__(self):
        return (
            f"WHT {self.wht_amount} on {self.payment.payment_number} "
            f"({self.contact.name})"
        )


# ---------------------------------------------------------------------------
# PAY-003 — post-payment Balance-Sheet tie assertion
# ---------------------------------------------------------------------------

def record_bs_tie_check(payment, user=None):
    """Prove the AP aging schedule still ties to the Balance Sheet after a
    payment posts (PAY-003 spec: "variance must be zero on each run").

    Recomputes PAY-002's AP-aging↔BS reconciliation as of the payment date,
    scoped to the payment's company, and writes the result to AuditLog.
    Returns the reconciliation dict.

    Called from the API layer AFTER the payment's posting transaction has
    committed — deliberately NOT inside confirm()'s atomic block, so a
    reconciliation hiccup can never roll back a validly posted payment.
    A non-zero variance is surfaced (logged WARNING + audit row) for
    investigation, but does not reverse the payment.
    """
    try:
        from reporting.reports import build_ap_aging
        rep = build_ap_aging(payment.payment_date, company_id=payment.company_id)
        recon = rep.get('reconciliation', {}) or {}
        reconciled = bool(recon.get('reconciled'))
        variance = recon.get('variance')
        AuditLog.objects.create(
            table_name='Payment',
            record_id=str(payment.pk),
            action=AuditLog.Action.UPDATE,
            new_values={
                'bs_tie_check':  'PAY-003',
                'as_of':         payment.payment_date.isoformat(),
                'aging_total':   str(recon.get('aging_total')),
                'bs_ap_balance': str(recon.get('bs_ap_balance')),
                'variance':      str(variance),
                'reconciled':    reconciled,
            },
            user=user,
            description=(
                f"BS tie after {payment.payment_number}: "
                f"variance {variance} ({'OK' if reconciled else 'INVESTIGATE'})"
            ),
        )
        if not reconciled:
            logger.warning(
                "PAY-003 BS tie FAILED after %s: AP aging %s vs BS AP %s, "
                "variance %s (company=%s, as_of=%s)",
                payment.payment_number, recon.get('aging_total'),
                recon.get('bs_ap_balance'), variance,
                payment.company_id, payment.payment_date,
            )
        return recon
    except Exception:  # noqa: BLE001 — never let the check break the response
        logger.exception("PAY-003 BS tie check errored for payment %s", payment.pk)
        return {}


# ---------------------------------------------------------------------------
# Outbound Payment Policy (singleton — dual-auth threshold)
# ---------------------------------------------------------------------------

class OutboundPaymentPolicy(BaseModel):
    """
    Tunable threshold for outbound-payment dual authorisation. Singleton.

    Defaults: any outbound payment whose BWP-equivalent amount exceeds
    P 50,000 requires a second approver who is not the creator.
    Below the threshold a single user (creator → confirmer) can complete
    the payment.
    """
    dual_auth_threshold = models.DecimalField(
                              max_digits=18, decimal_places=2,
                              default=Decimal('50000.00'),
                              help_text='BWP amount above which a second approver is required.',
                          )
    notes               = models.TextField(blank=True, default='')
    updated_by          = models.ForeignKey(
                              User, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='outbound_payment_policy_updates',
                          )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Outbound Payment Policy'
        verbose_name_plural = 'Outbound Payment Policy'

    def __str__(self):
        return f"Outbound payment dual-auth above BWP {self.dual_auth_threshold:,.2f}"

    @classmethod
    def current(cls) -> 'OutboundPaymentPolicy':
        policy = cls.objects.order_by('created_at').first()
        if policy is None:
            policy = cls.objects.create()
        return policy


# ---------------------------------------------------------------------------
# PaymentBatch / PaymentBatchLine (pay-run)
# ---------------------------------------------------------------------------
# Imported at the bottom so the models register on the payments app_label
# and migrations land in payments/migrations/.
from payments.batch_models import (  # noqa: E402,F401
    PaymentBatch,
    PaymentBatchLine,
)
