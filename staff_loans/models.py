"""
staff_loans/models.py

StaffLoanApplication — the request → CFO approval → sign → HR disbursement
workflow that sits in front of the existing payroll EmployeeLoan facility.

Design note
-----------
The *money* (principal, amortisation, monthly payroll deduction, outstanding
balance) is the existing, live `payroll.EmployeeLoan` + `LoanRepayment` +
`apply_loan_repayments()` engine. This app does NOT re-implement any of that.
It owns the paperwork: who applied, what for, the CFO's decision, the signed
undertaking, the blue-book collateral for vehicle loans, and the disbursement
+ its general-ledger entry. On disbursement it creates ONE EmployeeLoan and
lets the payroll engine take over the monthly deductions.

Kept out of payroll/ on purpose: payroll has a parallel work-stream and its own
migration line; a standalone app avoids stepping on it.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel

from . import policy


class StaffLoanApplication(AuditableMixin, BaseModel):
    """One staff-loan or vehicle-loan request and everything that happens to it."""

    class LoanType(models.TextChoices):
        STAFF = policy.STAFF, 'Staff Loan'
        VEHICLE = policy.VEHICLE, 'Vehicle Loan'

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING_CFO = 'pending_cfo', 'Pending CFO approval'
        APPROVED = 'approved', 'Approved — awaiting signature'
        SIGNED = 'signed', 'Signed — awaiting Finance release'
        ACTIVE = 'active', 'Disbursed — repaying'
        DECLINED = 'declined', 'Declined'
        CANCELLED = 'cancelled', 'Cancelled'

    # --- who + what -----------------------------------------------------------
    employee = models.ForeignKey(
        'payroll.Employee', on_delete=models.PROTECT,
        related_name='staff_loan_applications',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='staff_loans_created',
        help_text='Who filled in the application (usually the employee).',
    )
    loan_type = models.CharField(max_length=10, choices=LoanType.choices)
    amount_requested = models.DecimalField(max_digits=18, decimal_places=2)
    term_months_requested = models.PositiveIntegerField(
        help_text='Requested number of monthly repayments.',
    )
    reason = models.TextField(help_text='What the loan is for.')

    # Snapshot of the one-month cap basis at submit time (audit trail).
    monthly_salary_snapshot = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
    )

    # Attestations (CFO 2026-07-15).
    no_other_loans_declared = models.BooleanField(
        default=False,
        help_text='Applicant confirms they have no other loans with any bank or financial institution.',
    )
    purchased_via_veritas = models.BooleanField(
        default=False,
        help_text='Vehicle loans only: the vehicle was purchased through the Veritas salvage yard.',
    )

    # --- vehicle-loan collateral (blue book) ---------------------------------
    vehicle_description = models.CharField(max_length=200, blank=True, default='')
    vehicle_reg = models.CharField(max_length=40, blank=True, default='')
    blue_book_holder = models.CharField(
        max_length=20, choices=policy.BLUE_BOOK_HOLDERS, blank=True, default='',
        help_text='Whose name the blue book is held under until the loan clears.',
    )
    blue_book_received = models.BooleanField(
        default=False,
        help_text='HR confirms the blue book is physically held before disbursing.',
    )
    blue_book_returned = models.BooleanField(default=False)
    blue_book_returned_at = models.DateTimeField(null=True, blank=True)

    # --- status ---------------------------------------------------------------
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.DRAFT,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    # --- CFO decision ---------------------------------------------------------
    cfo_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='staff_loans_cfo_decided',
    )
    cfo_decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default='')
    decline_reason = models.TextField(blank=True, default='')
    approved_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
    )
    approved_term_months = models.PositiveIntegerField(null=True, blank=True)
    annual_rate_pct = models.DecimalField(
        max_digits=6, decimal_places=3, default=policy.DEFAULT_ANNUAL_RATE_PCT,
        help_text='Annual interest rate applied. Pre-filled with the scheme '
                  'default; the CFO may change it per loan.',
    )

    # --- employee signature (promissory note / undertaking) ------------------
    signature_data_url = models.TextField(blank=True, default='')
    signatory_full_name = models.CharField(max_length=200, blank=True, default='')
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_ip = models.CharField(max_length=45, blank=True, default='')

    # --- HR disbursement ------------------------------------------------------
    disbursed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='staff_loans_disbursed',
    )
    disbursed_at = models.DateTimeField(null=True, blank=True)
    disbursement_ref = models.CharField(
        max_length=80, blank=True, default='',
        help_text='Payment / PACOTO reference for the pay-out.',
    )
    disbursement_bank_code = models.CharField(max_length=30, blank=True, default='')
    # CFO 15-Sep-2026: Finance releases the loan and the payment is loaded into
    # Omni's payment queue automatically. Recording WHICH payment request pays
    # this loan is the audit trail — without it there is nothing tying the
    # posted journal entry to the money that actually left the bank.
    payment_request_ref = models.CharField(
        max_length=48, blank=True, default='',
        help_text='The Omni payment request raised to pay this loan.',
    )
    payment_request_error = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Set when the payment request could NOT be raised, so the '
                  'loan is not silently disbursed with nobody paying it.',
    )

    # --- links to the money + the ledger -------------------------------------
    employee_loan = models.OneToOneField(
        'payroll.EmployeeLoan', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='staff_loan_application',
        help_text='The live amortising loan created on disbursement.',
    )
    issuance_journal_entry = models.ForeignKey(
        'ledger.JournalEntry', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
        help_text='The GL entry posted when the loan was issued.',
    )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Staff Loan Application'
        verbose_name_plural = 'Staff Loan Applications'

    def __str__(self):
        return f'{self.employee.full_name} · {self.get_loan_type_display()} · P{self.amount_requested} ({self.status})'

    # --- derived amounts (use approved values once set, else requested) ------
    @property
    def effective_amount(self) -> Decimal:
        return self.approved_amount if self.approved_amount is not None else self.amount_requested

    @property
    def effective_term(self) -> int:
        return int(self.approved_term_months or self.term_months_requested or 0)

    @property
    def interest_amount(self) -> Decimal:
        return policy.flat_interest(self.effective_amount, self.annual_rate_pct, self.effective_term)

    @property
    def total_repayable(self) -> Decimal:
        return policy.total_repayable(self.effective_amount, self.annual_rate_pct, self.effective_term)

    @property
    def monthly_instalment(self) -> Decimal:
        return policy.monthly_instalment(self.effective_amount, self.annual_rate_pct, self.effective_term)

    @property
    def outstanding(self) -> Decimal | None:
        return self.employee_loan.outstanding if self.employee_loan_id else None

    @property
    def is_vehicle(self) -> bool:
        return self.loan_type == self.LoanType.VEHICLE

    def clean(self):
        if self.is_vehicle and self.status not in (self.Status.DRAFT,):
            # Once it leaves draft a vehicle loan must name where the blue book sits.
            if not self.blue_book_holder:
                raise ValidationError({'blue_book_holder': 'Say whose name holds the blue book (Alpha Direct or Veritas).'})


class StaffLoanRate(AuditableMixin, BaseModel):
    """The scheme interest rate over time.

    A monthly cron (`refresh_staff_loan_rate`) reads the Bank of Botswana
    Monetary Policy Rate (the reliably-published public rate) and stores a new
    row: annual_rate = MoPR + spread. The newest row is the rate new loans
    pre-fill with; the CFO may still set a different rate on any single loan,
    and may add a `manual` row here to override the scheme rate.
    """

    class Source(models.TextChoices):
        AUTO = 'auto', 'Auto (Bank of Botswana)'
        MANUAL = 'manual', 'Manual (set by CFO/Finance)'
        SEED = 'seed', 'Seed (initial value)'

    effective_from = models.DateField(help_text='Date this rate takes effect for new loans.')
    annual_rate_pct = models.DecimalField(
        max_digits=6, decimal_places=3,
        help_text='The scheme rate applied to new loans = reference + spread.',
    )
    reference_name = models.CharField(
        max_length=120, default='Bank of Botswana Monetary Policy Rate',
    )
    reference_rate_pct = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True,
        help_text='The public rate this was built from (e.g. the MoPR).',
    )
    spread_pct = models.DecimalField(
        max_digits=6, decimal_places=3, default=policy.DEFAULT_SPREAD_PCT,
        help_text='Margin added to the public reference rate.',
    )
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.AUTO)
    source_url = models.CharField(max_length=300, blank=True, default='')
    note = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='staff_loan_rates_set',
    )

    class Meta(BaseModel.Meta):
        ordering = ['-effective_from', '-created_at']
        verbose_name = 'Staff Loan Rate'
        verbose_name_plural = 'Staff Loan Rates'

    def __str__(self):
        return f'{self.annual_rate_pct}% from {self.effective_from} ({self.source})'
