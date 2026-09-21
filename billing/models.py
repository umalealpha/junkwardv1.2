"""
billing/models.py

Contact management and invoicing for Alpha Direct Financial Management System.

Models:
  - Contact       Customers, vendors, brokers, reinsurers, employees
  - Invoice       Customer invoices, vendor bills, credit notes
  - InvoiceLine   Individual line items with auto-calculated tax

Every monetary field: DecimalField(max_digits=18, decimal_places=2)

AUTOMATIC JOURNAL ENTRY POSTING:
When Invoice.post(user) is called, the system automatically creates and posts
the correct double-entry journal entry. No manual JE creation required.
"""

import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditLog, AuditableMixin, BaseModel, Currency, TaxRate
# The VAT control accounts are defined in ONE place (CFO 2026-09-14) so that
# what VAT POSTS to and what the VAT reconciliation READS can never drift apart.
from ledger.vat_accounts import VAT_INPUT_ACCOUNT, VAT_OUTPUT_ACCOUNT


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')


def _to_bwp(amount, rate):
    """Convert an amount to BWP using the given exchange rate, rounded to 2dp."""
    return (amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Invoice number generator
# ---------------------------------------------------------------------------

_INVOICE_PREFIX = {
    'customer_invoice': 'INV',
    'credit_note':      'CN',
    'vendor_bill':      'BILL',
    'vendor_credit':    'VC',
}


def _generate_invoice_number(invoice_type):
    """
    Thread-safe auto-incrementing invoice number.
    Each prefix (INV / CN / BILL / VC) has its own independent sequence.
    """
    prefix_key = _INVOICE_PREFIX.get(invoice_type, 'INV')
    year       = timezone.now().year
    prefix     = f"{prefix_key}-{year}-"

    with transaction.atomic():
        last = (
            Invoice.objects
            .select_for_update()
            .filter(invoice_number__startswith=prefix)
            .order_by('-invoice_number')
            .values_list('invoice_number', flat=True)
            .first()
        )
        next_seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f"{prefix}{next_seq:06d}"


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------

class Contact(AuditableMixin, BaseModel):
    """A party the business transacts with: customer, vendor, broker, etc."""

    class ContactType(models.TextChoices):
        CUSTOMER   = 'customer',   'Customer'
        VENDOR     = 'vendor',     'Vendor'
        BROKER     = 'broker',     'Broker'
        REINSURER  = 'reinsurer',  'Reinsurer'
        EMPLOYEE   = 'employee',   'Employee'

    contact_type        = models.CharField(max_length=15, choices=ContactType.choices)
    name                = models.CharField(max_length=300)
    registration_number = models.CharField(max_length=50, null=True, blank=True)
    tax_id              = models.CharField(max_length=50, null=True, blank=True)
    email               = models.EmailField(null=True, blank=True)
    phone               = models.CharField(max_length=50, null=True, blank=True)
    address             = models.TextField(null=True, blank=True)
    currency_code       = models.ForeignKey(
                              Currency, on_delete=models.PROTECT,
                              related_name='contacts', default='BWP',
                          )
    payment_terms_days  = models.PositiveIntegerField(default=30)
    # Optional FK to a multi-instalment PaymentTerm. When set this OVERRIDES
    # the int `payment_terms_days` above for Invoice due-date scheduling and
    # for the pay-run / early-pay-discount logic. The int field stays as a
    # cheap fallback so legacy code paths that have not been updated keep
    # working.
    payment_term        = models.ForeignKey(
                              'billing.PaymentTerm',
                              null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='contacts',
                              help_text='Multi-instalment / early-pay-discount '
                                        'term. Overrides payment_terms_days '
                                        'when set.',
                          )
    is_resident         = models.BooleanField(default=True)
    wht_exempt          = models.BooleanField(default=False)
    is_active           = models.BooleanField(default=True)
    graphite_id         = models.CharField(max_length=100, null=True, blank=True)

    # ---- Related party (IAS 24 / NBFIRA disclosure) ------------------------
    is_related_party    = models.BooleanField(
                              default=False,
                              help_text='True if this party is related under IAS 24 — '
                                        'subsidiaries, key management personnel, their close '
                                        'family, or entities they control or significantly '
                                        'influence.',
                          )
    related_party_relationship = models.CharField(
                              max_length=200, blank=True, default='',
                              help_text='Nature of the relationship — e.g. "Director", '
                                        '"Subsidiary", "Spouse of CFO", "Common control".',
                          )

    # Migration-source pointer. Format: "odoo:res.partner:<id>:<vendor|customer>"
    # Blank for contacts created manually or seeded from CSV. Indexed to make
    # the idempotency check on re-runs cheap.
    external_ref        = models.CharField(
                              max_length=100, blank=True, default='', db_index=True,
                          )

    # Owning legal entity. Vendors/customers belong to one Alpha Direct
    # subsidiary at a time (no shared vendor lists across entities — confirmed
    # by CFO 2026-05-18: ADIC vendors must NOT show up when ADSA is selected
    # in the topbar). Nullable for backwards-compatibility during the
    # backfill migration; the API enforces a value on every new write.
    company             = models.ForeignKey(
                              'core.Company', on_delete=models.PROTECT,
                              null=True, blank=True,
                              related_name='contacts',
                              help_text='Owning legal entity. Filters apply '
                                        'per-company in the topbar switcher.',
                          )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Contact'
        verbose_name_plural = 'Contacts'
        ordering            = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_contact_type_display()})"


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------

class Invoice(AuditableMixin, BaseModel):
    """
    A customer invoice, vendor bill, or credit note.

    Posting an invoice automatically creates and posts a balanced journal entry.
    Once posted, the invoice is immutable.
    """

    class InvoiceType(models.TextChoices):
        CUSTOMER_INVOICE = 'customer_invoice', 'Customer Invoice'
        CREDIT_NOTE      = 'credit_note',      'Credit Note'
        VENDOR_BILL      = 'vendor_bill',       'Vendor Bill'
        VENDOR_CREDIT    = 'vendor_credit',     'Vendor Credit'

    class Status(models.TextChoices):
        DRAFT             = 'draft',             'Draft'
        PENDING_APPROVAL  = 'pending_approval',  'Pending Approval'
        REJECTED          = 'rejected',          'Rejected'
        POSTED            = 'posted',            'Posted'
        PARTIALLY_PAID    = 'partially_paid',    'Partially Paid'
        PAID              = 'paid',              'Paid'
        CANCELLED         = 'cancelled',         'Cancelled'
        OVERDUE           = 'overdue',           'Overdue'

    invoice_number = models.CharField(max_length=30, unique=True, blank=True)
    invoice_type   = models.CharField(max_length=20, choices=InvoiceType.choices)
    contact        = models.ForeignKey(
                         Contact, on_delete=models.PROTECT, related_name='invoices',
                     )
    company        = models.ForeignKey(
                         'core.Company', on_delete=models.PROTECT,
                         related_name='invoices', null=True, blank=True,
                     )
    issue_date     = models.DateField()
    due_date       = models.DateField(null=True, blank=True)
    currency_code  = models.ForeignKey(
                         Currency, on_delete=models.PROTECT,
                         related_name='invoices', default='BWP',
                     )
    exchange_rate  = models.DecimalField(
                         max_digits=18, decimal_places=8, default=Decimal('1.00000000'),
                     )
    subtotal       = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    tax_total      = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    total_amount   = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    amount_paid    = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    balance_due    = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    status         = models.CharField(
                         max_length=20, choices=Status.choices, default=Status.DRAFT,
                     )
    # Claims automation 19-Sep-2026: a salvage invoice to Veritas must NOT land in
    # premium receivable (1210) — it would distort the premium debtors book and the
    # collections reports. Left blank, nothing changes: 1210 is still used.
    receivable_account = models.ForeignKey(
        'ledger.Account', null=True, blank=True, on_delete=models.PROTECT,
        related_name='invoices_receivable_override',
        help_text='Override the receivable account this invoice posts to. Blank = premium receivable.',
    )

    journal_entry  = models.ForeignKey(
                         'ledger.JournalEntry', null=True, blank=True,
                         on_delete=models.SET_NULL, related_name='invoices',
                     )

    # Bill approval workflow (vendor_bill only — customer invoices skip this).
    # Tiered routing per BillApprovalPolicy: <P5k auto-approves, P5k-50k needs
    # Manager+, >P50k needs CFO. Creator cannot approve their own bill.
    submitted_for_approval_at = models.DateTimeField(null=True, blank=True)
    submitted_for_approval_by = models.ForeignKey(
                                    User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='invoices_submitted',
                                )
    approval_tier             = models.CharField(max_length=30, blank=True, default='',
                                    help_text='Snapshot of which BillApprovalPolicy tier applied.')
    approved_at               = models.DateTimeField(null=True, blank=True)
    approved_by               = models.ForeignKey(
                                    User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='invoices_approved',
                                )
    rejection_reason          = models.TextField(blank=True, default='')
    source_type    = models.CharField(max_length=50, default='manual')
    source_id      = models.CharField(max_length=100, null=True, blank=True)
    description    = models.TextField(null=True, blank=True)

    # Vendor bill control — every vendor bill must reference an APPROVED PO.
    # Customer invoices and credit notes leave this null. Required-ness is
    # enforced at post() time, NOT at save(), so a draft can be created and
    # the PO attached just before posting.
    purchase_order = models.ForeignKey(
                         'procurement.PurchaseOrder',
                         null=True, blank=True,
                         on_delete=models.PROTECT,
                         related_name='vendor_bills',
                         help_text='Required for vendor bills. Bill cannot be '
                                   'posted without a referenced approved PO.',
                     )

    created_by     = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='invoices_created',
                     )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Invoice'
        verbose_name_plural = 'Invoices'
        # PERF (2026-07-17): AR/AP aging + receivables + the dashboards filter
        # on status and due_date; lists paginate over -created_at unindexed.
        indexes = [
            models.Index(fields=['status', 'due_date'], name='invoice_status_due_idx'),
            models.Index(fields=['-created_at'], name='invoice_created_at_idx'),
        ]

    def __str__(self):
        return f"{self.invoice_number} — {self.contact.name} ({self.total_amount})"

    # ------------------------------------------------------------------
    # save — auto number, auto due_date, immutability guard
    # ------------------------------------------------------------------

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        if not self.invoice_number:
            self.invoice_number = _generate_invoice_number(self.invoice_type)

        # Auto-calculate due_date once
        if self.issue_date and not self.due_date and self.contact_id:
            self.due_date = self.issue_date + datetime.timedelta(
                days=self.contact.payment_terms_days
            )

        # Immutability: block edits to posted/cancelled invoices
        if self.pk:
            try:
                db_status = Invoice.objects.values_list('status', flat=True).get(pk=self.pk)
                if db_status in (self.Status.POSTED, self.Status.CANCELLED):
                    raise ValidationError(
                        f"{self.invoice_number} is {db_status} and cannot be modified."
                    )
            except Invoice.DoesNotExist:
                pass

        super().save(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------

    def recalculate_totals(self):
        """
        Re-sum all lines and update subtotal, tax_total, total_amount, balance_due.
        Does NOT save — call save() after if you want to persist.
        """
        lines = self.lines.all()
        self.subtotal     = sum(ln.line_total   for ln in lines)
        self.tax_total    = sum(ln.tax_amount   for ln in lines)
        self.total_amount = self.subtotal + self.tax_total
        self.balance_due  = self.total_amount - self.amount_paid

    # ------------------------------------------------------------------
    # Journal entry creation (private)
    # ------------------------------------------------------------------

    def _get_ap_account(self, get_acct):
        """Return the accounts-payable account appropriate for this invoice's contact."""
        ct = self.contact.contact_type
        if ct == Contact.ContactType.BROKER:
            return get_acct('2130')    # Broker commission payable
        if ct == Contact.ContactType.REINSURER:
            return get_acct('2120')    # Reinsurance premium payable
        return get_acct('2140')        # Vendor payable (default)

    def _create_journal_entry(self, user, lines):
        """
        Build and post the accounting journal entry for this invoice.
        Returns the posted JournalEntry instance.

        Importing from ledger inside the method to avoid circular imports.
        """
        from ledger.models import Account, JournalEntry, JournalEntryLine

        def get_acct(code):
            try:
                return Account.objects.get(code=code)
            except Account.DoesNotExist:
                raise ValidationError(
                    f"Required account {code} not found in the chart of accounts. "
                    "Run setup_chart_of_accounts first."
                )

        rate          = self.exchange_rate
        invoice_type  = self.invoice_type
        contact_type  = self.contact.contact_type
        is_broker     = (contact_type == Contact.ContactType.BROKER)
        is_sales      = invoice_type in (
                            Invoice.InvoiceType.CUSTOMER_INVOICE,
                            Invoice.InvoiceType.CREDIT_NOTE,
                        )

        # Pre-compute per-line BWP amounts (rounded individually to avoid rounding gaps)
        line_bwp = [_to_bwp(ln.line_total, rate) for ln in lines]
        tax_bwp  = _to_bwp(self.tax_total, rate) if self.tax_total else Decimal('0.00')
        # Total BWP is the SUM of components — guarantees JE balance regardless of rounding
        total_bwp = sum(line_bwp) + tax_bwp

        # Create JE header — inherit company from the invoice
        je = JournalEntry(
            entry_date    = self.issue_date,
            description   = f"{self.invoice_number} — {self.contact.name}",
            source_type   = 'invoice',
            source_id     = self.pk,
            journal_type  = (JournalEntry.JournalType.SALES
                             if is_sales else JournalEntry.JournalType.PURCHASES),
            currency_code = self.currency_code,
            exchange_rate = self.exchange_rate,
            company       = self.company,
            created_by    = user,
        )
        je.save(audit_user=user, audit_description=f"JE for {self.invoice_number}")

        ZERO = Decimal('0.00')

        if invoice_type == Invoice.InvoiceType.CUSTOMER_INVOICE:
            ar = self.receivable_account or get_acct('1210')
            # Dr: Premium receivable (full amount)
            JournalEntryLine.objects.create(
                journal_entry=je, account=ar,
                description=f"{self.invoice_number} — {self.contact.name}",
                debit_amount=self.total_amount, credit_amount=ZERO,
                debit_bwp=total_bwp,            credit_bwp=ZERO,
            )
            # Cr: Revenue account per line
            for ln, bwp in zip(lines, line_bwp):
                JournalEntryLine.objects.create(
                    journal_entry=je, account=ln.account,
                    description=ln.description,
                    debit_amount=ZERO,         credit_amount=ln.line_total,
                    debit_bwp=ZERO,            credit_bwp=bwp,
                )
            # Cr: VAT output payable
            if self.tax_total:
                JournalEntryLine.objects.create(
                    journal_entry=je, account=get_acct(VAT_OUTPUT_ACCOUNT),
                    description=f"VAT on {self.invoice_number}",
                    debit_amount=ZERO,         credit_amount=self.tax_total,
                    debit_bwp=ZERO,            credit_bwp=tax_bwp,
                )

        elif invoice_type == Invoice.InvoiceType.VENDOR_BILL:
            if is_broker and self.tax_total:
                raise ValidationError(
                    "Broker commission bills cannot have VAT. "
                    "Remove tax from all lines before posting."
                )
            ap = self._get_ap_account(get_acct)
            # Dr: Expense account per line
            for ln, bwp in zip(lines, line_bwp):
                JournalEntryLine.objects.create(
                    journal_entry=je, account=ln.account,
                    description=ln.description,
                    debit_amount=ln.line_total, credit_amount=ZERO,
                    debit_bwp=bwp,              credit_bwp=ZERO,
                )
            # Dr: VAT input receivable (non-broker only)
            if self.tax_total and not is_broker:
                JournalEntryLine.objects.create(
                    journal_entry=je, account=get_acct(VAT_INPUT_ACCOUNT),
                    description=f"VAT input on {self.invoice_number}",
                    debit_amount=self.tax_total, credit_amount=ZERO,
                    debit_bwp=tax_bwp,           credit_bwp=ZERO,
                )
            # Cr: Payable (full amount)
            JournalEntryLine.objects.create(
                journal_entry=je, account=ap,
                description=f"{self.invoice_number} — {self.contact.name}",
                debit_amount=ZERO,              credit_amount=self.total_amount,
                debit_bwp=ZERO,                 credit_bwp=total_bwp,
            )

        elif invoice_type == Invoice.InvoiceType.CREDIT_NOTE:
            # Mirror the invoice: a credit note against a salvage invoice must
            # reverse the SAME receivable it debited, not premium receivable.
            ar = self.receivable_account or get_acct('1210')
            # Dr: Revenue accounts (reversal)
            for ln, bwp in zip(lines, line_bwp):
                JournalEntryLine.objects.create(
                    journal_entry=je, account=ln.account,
                    description=f"CN: {ln.description}",
                    debit_amount=ln.line_total, credit_amount=ZERO,
                    debit_bwp=bwp,              credit_bwp=ZERO,
                )
            # Dr: VAT output payable (reversal)
            if self.tax_total:
                JournalEntryLine.objects.create(
                    journal_entry=je, account=get_acct(VAT_OUTPUT_ACCOUNT),
                    description=f"VAT reversal on {self.invoice_number}",
                    debit_amount=self.tax_total, credit_amount=ZERO,
                    debit_bwp=tax_bwp,           credit_bwp=ZERO,
                )
            # Cr: Premium receivable
            JournalEntryLine.objects.create(
                journal_entry=je, account=ar,
                description=f"Credit note {self.invoice_number}",
                debit_amount=ZERO,              credit_amount=self.total_amount,
                debit_bwp=ZERO,                 credit_bwp=total_bwp,
            )

        elif invoice_type == Invoice.InvoiceType.VENDOR_CREDIT:
            ap = self._get_ap_account(get_acct)
            # Dr: Payable (reversal)
            JournalEntryLine.objects.create(
                journal_entry=je, account=ap,
                description=f"Vendor credit {self.invoice_number}",
                debit_amount=self.total_amount, credit_amount=ZERO,
                debit_bwp=total_bwp,            credit_bwp=ZERO,
            )
            # Cr: Expense accounts (reversal)
            for ln, bwp in zip(lines, line_bwp):
                JournalEntryLine.objects.create(
                    journal_entry=je, account=ln.account,
                    description=f"Credit: {ln.description}",
                    debit_amount=ZERO,          credit_amount=ln.line_total,
                    debit_bwp=ZERO,             credit_bwp=bwp,
                )
            # Cr: VAT input (reversal)
            if self.tax_total:
                JournalEntryLine.objects.create(
                    journal_entry=je, account=get_acct(VAT_INPUT_ACCOUNT),
                    description=f"VAT input reversal on {self.invoice_number}",
                    debit_amount=ZERO,          credit_amount=self.tax_total,
                    debit_bwp=ZERO,             credit_bwp=tax_bwp,
                )

        # This validates balance and sets posted_date
        je.post(user=user)
        return je

    # ------------------------------------------------------------------
    # post() — the main public method
    # ------------------------------------------------------------------

    @transaction.atomic
    def post(self, user):
        """
        Validate, create the journal entry, and mark this invoice as posted.
        Raises ValidationError on any rule violation.
        """
        if not self.pk:
            raise ValidationError("Save the invoice before posting it.")

        if self.status not in (self.Status.DRAFT, self.Status.PENDING_APPROVAL):
            raise ValidationError(
                f"Only draft or pending-approval invoices can be posted. "
                f"Current status: {self.status}."
            )

        # Structural audit 2026-05-24 — fiscal-period close check.
        # Canonical AP invariant: no bill / invoice can post into a closed
        # fiscal period. Mirrors JournalEntry._validate_for_posting.
        from ledger.models import FiscalPeriod as _FP
        _open_period = _FP.get_open_period_for_date(self.issue_date)
        if _open_period is None:
            raise ValidationError(
                f"No open fiscal period covers issue date {self.issue_date}. "
                "Reopen the period or amend the invoice issue date before "
                "posting."
            )

        # Structural audit 2026-05-24 — duplicate-bill detection on vendor bills.
        # Canonical AP guard: same vendor + same total + same issue date is
        # almost certainly a duplicate keying. Block at post() time so the
        # draft stays editable but cannot fork into a second GL liability.
        # Width: ±14 days because vendors often re-submit a bill if the
        # original was lost. Use POSTED + PARTIALLY_PAID + PAID states.
        if self.invoice_type == self.InvoiceType.VENDOR_BILL:
            import datetime as _dt
            window_lo = self.issue_date - _dt.timedelta(days=14)
            window_hi = self.issue_date + _dt.timedelta(days=14)
            dup_q = (Invoice.objects
                     .filter(invoice_type=self.InvoiceType.VENDOR_BILL,
                             contact_id=self.contact_id,
                             total_amount=self.total_amount,
                             issue_date__gte=window_lo,
                             issue_date__lte=window_hi,
                             status__in=[self.Status.POSTED,
                                         self.Status.PARTIALLY_PAID,
                                         self.Status.PAID])
                     .exclude(pk=self.pk))
            dup = dup_q.first()
            if dup is not None:
                raise ValidationError(
                    f"Possible duplicate bill: {dup.invoice_number} from "
                    f"{dup.contact.name} dated {dup.issue_date} for "
                    f"{dup.total_amount:,.2f} is already {dup.status}. "
                    "If this is genuinely a separate bill from the same "
                    "vendor for the same amount within 14 days, cancel "
                    "the earlier entry first or adjust the issue date."
                )

        # Bill approval gate: vendor_bill that exceeds the auto-approve ceiling
        # must be approved (approved_at set) before it can post.
        if self.invoice_type == self.InvoiceType.VENDOR_BILL:
            policy = BillApprovalPolicy.current()
            if self.total_amount > policy.auto_approve_ceiling:
                if not self.approved_by_id or not self.approved_at:
                    raise ValidationError(
                        f"Bill total {self.total_amount:,.2f} exceeds the "
                        f"auto-approve ceiling ({policy.auto_approve_ceiling:,.2f}). "
                        "Submit for approval first."
                    )

        # CFO-mandated control: every vendor bill MUST reference an approved
        # PO before it can post to the GL. Closes the loophole where a bill
        # would post + pay without ever passing through procurement.
        if self.invoice_type == self.InvoiceType.VENDOR_BILL:
            if not self.purchase_order_id:
                raise ValidationError(
                    "A vendor bill cannot be posted without a Purchase Order "
                    "reference. Pick the PO this bill belongs to."
                )
            from procurement.models import PurchaseOrder as _PO
            allowed_po_states = {
                _PO.Status.APPROVED,
                _PO.Status.PARTIALLY_RECEIVED,
                _PO.Status.FULLY_RECEIVED,
                _PO.Status.CLOSED,
            }
            if self.purchase_order.status not in allowed_po_states:
                raise ValidationError(
                    f"PO {self.purchase_order.po_number} is "
                    f"{self.purchase_order.get_status_display()}, not approved. "
                    "Only approved POs accept vendor bills."
                )
            if self.purchase_order.supplier_id != self.contact_id:
                raise ValidationError(
                    f"PO {self.purchase_order.po_number} belongs to "
                    f"{self.purchase_order.supplier.name}, not to "
                    f"{self.contact.name}."
                )
            # Date sanity — no back-dating past the PO approval
            if self.purchase_order.cfo_approved_at:
                if self.issue_date < self.purchase_order.cfo_approved_at.date():
                    raise ValidationError(
                        f"Bill issue date ({self.issue_date}) is before the "
                        f"PO was approved "
                        f"({self.purchase_order.cfo_approved_at.date()}). "
                        "Back-dated bills are not accepted."
                    )

        lines = list(self.lines.select_related('account').all())
        if not lines:
            raise ValidationError("Add at least one line before posting the invoice.")

        # Recalculate totals from lines (authoritative source)
        self.recalculate_totals()
        if self.total_amount <= ZERO:
            raise ValidationError("Invoice total must be greater than zero.")

        # Create and post the journal entry
        je = self._create_journal_entry(user, lines)

        # Mark invoice as posted — DB status is still 'draft', so immutability guard passes
        self.status        = self.Status.POSTED
        self.journal_entry = je
        self.save(
            audit_user=user,
            audit_description=f"Posted {self.invoice_number}",
        )

        AuditLog.objects.create(
            table_name='Invoice',
            record_id=str(self.pk),
            action=AuditLog.Action.POST,
            new_values={
                'status':        self.Status.POSTED,
                'journal_entry': str(je.pk),
                'total_amount':  str(self.total_amount),
            },
            user=user,
            description=f"Posted {self.invoice_number}, JE: {je.entry_number}",
        )

        # CFO-mandated: auto-run the 3-way match the moment a vendor bill
        # posts. The system computes the variance and routes to the right
        # approval tier — no human kick-off needed.
        if self.invoice_type == self.InvoiceType.VENDOR_BILL and self.purchase_order_id:
            try:
                from procurement.services import auto_match_on_bill_post
                auto_match_on_bill_post(self, user)
            except Exception as exc:  # noqa: BLE001
                # Auto-match failure must not block the GL post (the bill is
                # already in the ledger). Surface the error via AuditLog so
                # it is investigated.
                AuditLog.objects.create(
                    table_name='Invoice',
                    record_id=str(self.pk),
                    action=AuditLog.Action.UPDATE,
                    new_values={'auto_match_error': str(exc)[:400]},
                    user=user,
                    description=f"Auto-match failed for {self.invoice_number}",
                )

    # ------------------------------------------------------------------
    # Bill approval workflow (vendor_bill only)
    # ------------------------------------------------------------------

    def _required_approver_titles(self):
        """Return the set of user titles allowed to approve at this bill's tier."""
        policy = BillApprovalPolicy.current()
        amt = self.total_amount
        if amt <= policy.auto_approve_ceiling:
            return set()  # auto-approves
        if amt <= policy.manager_ceiling:
            # Manager OR higher
            return {'finance_manager', 'financial_controller', 'cfo'}
        return {'cfo'}  # >P50k → CFO only

    @transaction.atomic
    def submit_for_approval(self, user):
        """
        Move a draft vendor_bill into PENDING_APPROVAL (or auto-approve and
        leave at DRAFT if the amount is below the auto-approve ceiling).
        """
        if self.invoice_type != self.InvoiceType.VENDOR_BILL:
            raise ValidationError("Only vendor bills go through the bill approval workflow.")
        if self.status not in (self.Status.DRAFT, self.Status.REJECTED):
            raise ValidationError(
                f"Cannot submit {self.get_status_display()} bill for approval."
            )
        self.recalculate_totals()
        if self.total_amount <= ZERO:
            raise ValidationError("Bill total must be greater than zero.")

        policy = BillApprovalPolicy.current()
        amt = self.total_amount

        if amt <= policy.auto_approve_ceiling:
            # Auto-approve below the ceiling — caller can post immediately.
            self.approval_tier = 'auto_approved'
            self.approved_by   = user
            self.approved_at   = timezone.now()
            self.submitted_for_approval_by = user
            self.submitted_for_approval_at = timezone.now()
            # Stay at DRAFT so post() runs unchanged.
            self.save(audit_user=user,
                      audit_description=f"{self.invoice_number} auto-approved (≤ {policy.auto_approve_ceiling:,.2f})")
            return

        # Above ceiling — route for approval.
        if amt <= policy.manager_ceiling:
            tier = 'manager'
        else:
            tier = 'cfo'
        self.status                   = self.Status.PENDING_APPROVAL
        self.approval_tier            = tier
        self.submitted_for_approval_by = user
        self.submitted_for_approval_at = timezone.now()
        self.approved_by               = None
        self.approved_at               = None
        self.rejection_reason          = ''
        self.save(audit_user=user,
                  audit_description=f"Submitted {self.invoice_number} for {tier} approval")

    @transaction.atomic
    def approve_bill(self, user):
        """Approve a bill that's pending. Creator cannot approve own bill."""
        if self.status != self.Status.PENDING_APPROVAL:
            raise ValidationError(
                f"Only pending-approval bills can be approved. Current: {self.get_status_display()}"
            )
        if self.created_by_id == user.id or self.submitted_for_approval_by_id == user.id:
            raise ValidationError(
                "Segregation of duties: the bill's creator/submitter cannot approve it."
            )
        # Title check — UserProfile.title is case-insensitive
        title = ''
        try:
            title = (user.profile.title or '').strip().lower().replace(' ', '_')
        except Exception:
            pass
        required = self._required_approver_titles()
        if required and title not in required:
            raise ValidationError(
                f"Your approver title ({title or 'unset'}) is not authorised for "
                f"this tier ({self.approval_tier}). Need one of: "
                + ', '.join(sorted(required))
            )

        self.approved_by = user
        self.approved_at = timezone.now()
        # Stay at PENDING_APPROVAL — post() handles the final transition.
        self.save(audit_user=user,
                  audit_description=f"Approved {self.invoice_number} ({self.approval_tier})")

    @transaction.atomic
    def reject_bill(self, user, reason: str):
        if self.status != self.Status.PENDING_APPROVAL:
            raise ValidationError(
                f"Only pending-approval bills can be rejected. Current: {self.get_status_display()}"
            )
        if self.created_by_id == user.id or self.submitted_for_approval_by_id == user.id:
            raise ValidationError(
                "Segregation of duties: the bill's creator/submitter cannot reject it."
            )
        self.status            = self.Status.REJECTED
        self.rejection_reason  = (reason or '').strip()[:2000]
        self.save(audit_user=user,
                  audit_description=f"Rejected {self.invoice_number}")


# ---------------------------------------------------------------------------
# Bill Approval Policy (singleton — tunable thresholds)
# ---------------------------------------------------------------------------

class BillApprovalPolicy(BaseModel):
    """
    Tunable thresholds for vendor bill approval. Singleton — one active row.
    Defaults from CFO email:
      < P5,000              auto-approve (no human gate)
      P5,000 – P50,000      Manager / FM / Controller / CFO
      > P50,000             CFO only
    """
    auto_approve_ceiling = models.DecimalField(
                              max_digits=18, decimal_places=2,
                              default=Decimal('5000.00'),
                              help_text='Bills at or below this BWP amount auto-approve.',
                          )
    manager_ceiling      = models.DecimalField(
                              max_digits=18, decimal_places=2,
                              default=Decimal('50000.00'),
                              help_text='Above auto-approve and at or below this — '
                                        'Finance Manager / Controller / CFO can approve.',
                          )

    # ── PAY-001 three-tier approval (CFO directive 2026-05-27) ──────────────
    # Tier 1: up to tier1_ceiling      → Finance Manager
    # Tier 2: up to tier2_ceiling      → Head of Finance
    # Tier 3: above tier2_ceiling      → CFO only
    # PLACEHOLDERS: thresholds intentionally null until the CFO sets them in
    # Settings. While null, assign_approval_tier() routes EVERYTHING to Tier 3
    # (CFO) — fail-safe: no bill slips through on an unconfigured threshold.
    tier1_ceiling = models.DecimalField(
                        max_digits=18, decimal_places=2, null=True, blank=True,
                        help_text='PLACEHOLDER — CFO to set. Bills at/below this '
                                  'BWP amount → Tier 1 (Finance Manager).',
                    )
    tier2_ceiling = models.DecimalField(
                        max_digits=18, decimal_places=2, null=True, blank=True,
                        help_text='PLACEHOLDER — CFO to set. Above tier1 and '
                                  'at/below this → Tier 2 (Head of Finance). '
                                  'Above this → Tier 3 (CFO only).',
                    )
    tier1_role    = models.CharField(
                        max_length=40, default='finance_manager',
                        help_text='Title that approves Tier 1 bills.',
                    )
    tier2_role    = models.CharField(
                        max_length=40, default='head_of_finance',
                        help_text='Title that approves Tier 2 bills.',
                    )
    tier3_role    = models.CharField(
                        max_length=40, default='cfo',
                        help_text='Title that approves Tier 3 bills (top tier).',
                    )

    notes                = models.TextField(blank=True, default='')
    updated_by           = models.ForeignKey(
                              User, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='bill_approval_policy_updates',
                          )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Bill Approval Policy'
        verbose_name_plural = 'Bill Approval Policy'

    def __str__(self):
        return (f"Bill approval — auto≤{self.auto_approve_ceiling:,.2f}, "
                f"manager≤{self.manager_ceiling:,.2f}, CFO above")

    @classmethod
    def current(cls) -> 'BillApprovalPolicy':
        policy = cls.objects.order_by('created_at').first()
        if policy is None:
            policy = cls.objects.create()
        return policy

    def assign_tier(self, amount_bwp: Decimal) -> int:
        """Return the approval tier (1/2/3) for a BWP amount.

        PAY-001 (CFO directive 2026-05-27). Thresholds are placeholders
        until the CFO fills them in Settings. Fail-safe: any unset ceiling
        means we cannot prove the bill is "small enough" for a lower tier,
        so it escalates to Tier 3 (CFO). No bill auto-routes to a junior
        approver on an unconfigured threshold.
        """
        amt = Decimal(str(amount_bwp or 0))
        t1 = self.tier1_ceiling
        t2 = self.tier2_ceiling
        if t1 is not None and amt <= t1:
            return 1
        if t2 is not None and amt <= t2:
            return 2
        return 3

    def tier_role(self, tier: int) -> str:
        return {1: self.tier1_role, 2: self.tier2_role, 3: self.tier3_role}.get(tier, self.tier3_role)


# ---------------------------------------------------------------------------
# Invoice Line
# ---------------------------------------------------------------------------

class InvoiceLine(BaseModel):
    """
    A single line on an invoice. tax_rate is copied from TaxRate on creation
    so historical rates are preserved even if the TaxRate changes later.
    Every monetary field: DecimalField(max_digits=18, decimal_places=2).
    """

    invoice     = models.ForeignKey(
                      Invoice, on_delete=models.CASCADE, related_name='lines',
                  )
    account     = models.ForeignKey(
                      'ledger.Account', on_delete=models.PROTECT,
                      related_name='invoice_lines',
                  )
    description = models.CharField(max_length=500)
    quantity    = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('1.0000'))
    unit_price  = models.DecimalField(max_digits=18, decimal_places=2)
    tax_code    = models.ForeignKey(
                      TaxRate, on_delete=models.PROTECT, related_name='invoice_lines',
                  )
    tax_rate    = models.DecimalField(
                      max_digits=5, decimal_places=2, default=Decimal('0.00'),
                  )
    tax_amount  = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))
    line_total  = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0.00'))

    class Meta(BaseModel.Meta):
        ordering            = ['created_at']
        verbose_name        = 'Invoice Line'
        verbose_name_plural = 'Invoice Lines'

    def __str__(self):
        return f"{self.description} — {self.line_total}"

    def save(self, *args, **kwargs):
        # Copy tax_rate from TaxRate on first save (preserves historical rate)
        if self._state.adding and self.tax_code_id:
            self.tax_rate = self.tax_code.rate or Decimal('0.00')

        # Auto-calculate line_total and tax_amount
        self.line_total = (self.quantity * self.unit_price).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        if self.tax_rate:
            self.tax_amount = (self.line_total * self.tax_rate / 100).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
        else:
            self.tax_amount = Decimal('0.00')

        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# PaymentTerm / PaymentTermLine
# ---------------------------------------------------------------------------
# Imported at the bottom so the models register on the billing app_label
# and migrations land in billing/migrations/.
from billing.payment_terms_models import (  # noqa: E402,F401
    PaymentTerm,
    PaymentTermLine,
)

# ---------------------------------------------------------------------------
# ReverseChargeEntry — reverse-charge VAT on imported remote services
# (VAT Amendment Act No.16 of 2025, effective 1 June 2026)
# ---------------------------------------------------------------------------
# Same import-at-the-bottom pattern as PaymentTerm/PaymentTermLine above.
from billing.reverse_charge_models import (  # noqa: E402,F401
    ReverseChargeEntry,
)
