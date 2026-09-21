"""
hris/leave_encash_models.py — Leave Encashment (CFO directive 2026-07-21).

An employee applies to convert accumulated annual-leave days into cash. The
application runs a 4-step approval chain, then goes for payment:

    employee applies
        → CFO approves          (sits on the CFO's dashboard)
        → HR approves           (Head of Human Capital / HR team)
        → FC *or* FM approves   (Financial Controller or Finance Manager — either)
        → paid                  (Finance loads it for payment)

Alpha Direct valuation rule (CFO 2026-07-21):

    daily rate  = BASIC salary ÷ 22 working days   (BASIC only — no allowances)
    encashment  = daily rate × days encashed
    provision   = daily rate × current annual-leave balance   (per employee)

PAYE (CFO directive 2026-08-17) — the gross is never paid across:

    tax  = PAYE(monthly taxable pay + encashment) − PAYE(monthly taxable pay)
    net  = encashment − tax

i.e. the employee's OWN marginal rate off the active TaxBracket table, not a
flat rate. A cash-out to a serving employee is ordinary taxable employment
income in the month it is paid.

All money figures are BWP snapshots taken at application time — a later salary
change never silently reprices an in-flight or approved encashment. Days are
committed against the leave balance while an application is in flight (any
non-rejected state) so they can't be double-spent as booked leave.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel

ZERO = Decimal('0.00')

# CoS working-days divisor for the daily rate. Changed 22 → 24 (CFO 2026-08-03,
# per Unami Butale): Alpha Direct works Mon–Sat, so 22 (a 5-day month) understated
# the working days; 24 is the CFO's chosen figure.
WORKING_DAYS_PER_MONTH = Decimal('24')


class LeaveEncashment(AuditableMixin, BaseModel):
    """One employee's application to cash out annual-leave days."""

    class Status(models.TextChoices):
        PENDING_CFO     = 'pending_cfo',     'Awaiting CFO approval'
        PENDING_HR      = 'pending_hr',      'Awaiting HR approval'
        PENDING_FINANCE = 'pending_finance', 'Awaiting Finance (FC/FM) approval'
        APPROVED        = 'approved',        'Approved — for payment'
        REJECTED        = 'rejected',        'Rejected'
        PAID            = 'paid',            'Paid'

    # A state still "in flight or committed" holds the days against the balance.
    OPEN_STATUSES = (
        Status.PENDING_CFO, Status.PENDING_HR, Status.PENDING_FINANCE,
        Status.APPROVED, Status.PAID,
    )

    employee = models.ForeignKey(
                   'payroll.Employee', on_delete=models.PROTECT,
                   related_name='leave_encashments')
    profile  = models.ForeignKey(
                   'hris.HRISProfile', null=True, blank=True,
                   on_delete=models.SET_NULL,
                   related_name='leave_encashments',
                   help_text='HRIS profile at application time — the balance the '
                             'days are deducted from.')
    company  = models.ForeignKey(
                   'core.Company', null=True, blank=True,
                   on_delete=models.SET_NULL,
                   related_name='leave_encashments')

    # Only annual leave is encashable today; the field keeps the door open
    # without a schema change if the CFO ever extends it.
    leave_type_code = models.CharField(max_length=30, default='annual')

    class Kind(models.TextChoices):
        # A serving employee cashing out SOME leave (self-service, keeps the
        # minimum residual balance).
        ENCASHMENT = 'encashment', 'Leave encashment'
        # A LEAVER's final leave pay (CFO 2026-09-05): HR raises it, the FULL
        # balance to the last working day is paid, no residual floor, valued
        # on BASIC / 24 like everything else. Same CFO -> HR -> Finance chain.
        SETTLEMENT = 'settlement', 'Leaver final leave pay'

    kind     = models.CharField(max_length=12, choices=Kind.choices,
                                default=Kind.ENCASHMENT, db_index=True)
    last_day = models.DateField(null=True, blank=True,
                                help_text='Settlement only: the last working day the '
                                          'balance was accrued to.')

    days         = models.DecimalField(
                       max_digits=6, decimal_places=2,
                       help_text='Leave days converted to cash.')
    # ── Valuation snapshot (server-computed at application, never client input) ─
    basic_salary = models.DecimalField(
                       max_digits=18, decimal_places=2, default=ZERO,
                       help_text='Monthly BASIC (BWP) from the latest payslip '
                                 'at application time.')
    daily_rate   = models.DecimalField(
                       max_digits=18, decimal_places=2, default=ZERO,
                       help_text='basic_salary ÷ 22 (BWP).')
    amount       = models.DecimalField(
                       max_digits=18, decimal_places=2, default=ZERO,
                       help_text='daily_rate × days (BWP) — the GROSS payout.')
    # ── PAYE on the payout (CFO directive 2026-08-17) ──────────────────────────
    # A cash-out to a serving employee is ordinary taxable employment income, so
    # PAYE must be withheld — we never pay the gross across. Charged at the
    # employee's own marginal rate off the active TaxBracket table:
    #   tax = PAYE(monthly taxable pay + amount) − PAYE(monthly taxable pay)
    tax_base     = models.DecimalField(
                       max_digits=18, decimal_places=2, default=ZERO,
                       help_text='Monthly taxable pay (BWP) the payout was '
                                 'stacked on to find the marginal rate.')
    tax_amount   = models.DecimalField(
                       max_digits=18, decimal_places=2, default=ZERO,
                       help_text='PAYE withheld on the payout (BWP).')
    net_amount   = models.DecimalField(
                       max_digits=18, decimal_places=2, default=ZERO,
                       help_text='amount − tax_amount (BWP) — what the employee '
                                 'is actually paid.')
    balance_at_request = models.DecimalField(
                       max_digits=6, decimal_places=2, default=ZERO,
                       help_text='Annual-leave days available when applied '
                                 '(audit trail for the ≤-balance check).')
    basic_source = models.CharField(
                       max_length=20, blank=True, default='',
                       help_text='Payroll period the BASIC snapshot came from, '
                                 'e.g. 2026-06.')
    reason       = models.TextField(blank=True, default='',
                       help_text='Employee reason / note for the application.')

    # The applicant (the employee, self-service).
    applicant    = models.ForeignKey(
                       User, null=True, blank=True,
                       on_delete=models.SET_NULL,
                       related_name='leave_encashments_applied')
    applicant_email = models.CharField(max_length=254, blank=True, default='')

    status       = models.CharField(
                       max_length=16, choices=Status.choices,
                       default=Status.PENDING_CFO, db_index=True)

    # ── The three approval legs, in order ──────────────────────────────────────
    cfo_approver     = models.ForeignKey(
                           User, null=True, blank=True,
                           on_delete=models.SET_NULL, related_name='+')
    cfo_approved_at  = models.DateTimeField(null=True, blank=True)
    hr_approver      = models.ForeignKey(
                           User, null=True, blank=True,
                           on_delete=models.SET_NULL, related_name='+')
    hr_approved_at   = models.DateTimeField(null=True, blank=True)
    finance_approver = models.ForeignKey(
                           User, null=True, blank=True,
                           on_delete=models.SET_NULL, related_name='+')
    finance_approved_at = models.DateTimeField(null=True, blank=True)

    rejected_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='+')
    rejected_at     = models.DateTimeField(null=True, blank=True)
    rejected_stage  = models.CharField(max_length=16, blank=True, default='',
                          help_text='Which stage rejected it (cfo/hr/finance).')
    decision_notes  = models.TextField(blank=True, default='')

    # Payment — Finance marks it paid once loaded for payment (reuses the
    # original payroll_processed columns; surfaced as "paid" everywhere).
    # The payroll amendment this payout was fed into by hris/leave_pay_feed.py
    # (Build Spec B10). Set = already in a payroll batch, so a re-run updates
    # that row instead of raising a second one.
    payroll_amendment = models.ForeignKey(
                               'payroll.PayrollAmendment', null=True, blank=True,
                               on_delete=models.SET_NULL,
                               related_name='leave_encashments')

    payroll_processed    = models.BooleanField(default=False)
    payroll_processed_by = models.ForeignKey(
                               User, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='+')
    payroll_processed_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Leave Encashment'
        verbose_name_plural = 'Leave Encashments'

    def __str__(self):
        return (f"{self.employee.full_name} — {self.days}d × "
                f"{self.daily_rate} = {self.amount} less PAYE {self.tax_amount} "
                f"= {self.net_amount} ({self.status})")

    # Convenience aliases so payment code reads naturally.
    @property
    def paid(self) -> bool:
        return self.payroll_processed


class LeaveEncashmentPayrollLine(BaseModel):
    """The ONE payroll line that exists for one leave encashment — a claim row
    the DATABASE holds unique.

    WHY (CFO decision 2026-09-13). Until the AUTO-LEAVEPAY feed went live an
    approved encashment was settled directly by Finance. Two routes into
    payroll now exist — the feed, and a person hand-keying an amendment — and
    neither can see the other. "Check first, then write" is not a control: two
    requests, two workers, or a feed re-run racing a hand-keyed row can both
    pass the check and both write.

    So the fact is stored, not inspected: one row per encashment, ``encashment``
    UNIQUE at the database level (``OneToOneField``). A second attempt to raise
    a payroll line for the same encashment is refused by Postgres itself, and
    the caller is told which amendment already carries it.

    This records nothing about money. A payroll amendment is a record; money
    leaves at the bank under a human's two-factor.
    """

    encashment = models.OneToOneField(
        LeaveEncashment, on_delete=models.CASCADE,
        related_name='payroll_line_claim',
        help_text='The encashment. UNIQUE — this is the no-double-pay guard.')
    amendment  = models.ForeignKey(
        'payroll.PayrollAmendment', on_delete=models.CASCADE,
        related_name='leave_encashment_claims',
        help_text='The payroll amendment that already carries this encashment.')

    class Source(models.TextChoices):
        FEED = 'feed', 'Raised by the leave-pay feed'
        HAND = 'hand', 'Hand-keyed amendment'

    source = models.CharField(max_length=8, choices=Source.choices,
                              default=Source.FEED)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Leave Encashment Payroll Line'
        verbose_name_plural = 'Leave Encashment Payroll Lines'

    def __str__(self):
        return f'{self.encashment_id} -> amendment {self.amendment_id} ({self.source})'
