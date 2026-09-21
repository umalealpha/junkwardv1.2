"""
payroll/salary_advance_models.py — Early Salary Advance (Build Spec B12,
requested by Unopa Male; CFO decision 13 September 2026 put it in scope).

An advance is **not a payment**. It is a RECEIVABLE from the employee that is
recovered from a later payslip — the same shape as a staff loan, which is why
it reuses the staff-loan machinery (`staff_loans.services.
ensure_loan_repayment_component` is the ONE place the GL account is bound, and
that account code comes from `payroll.config`, never a constant here).

The three controls, settled by the CFO on 13 September 2026:

  1. **The CFO approves every advance individually.** No delegation, no
     auto-approval, no threshold below which it is waved through.
  2. **Maximum one third of the person's monthly salary** — measured on the
     BASIC off their latest payslip, the same basis leave encashment already
     uses, so the two cannot disagree about what someone earns.
  3. **Recovered IN FULL on the very next payslip.** One deduction, one month.
     No instalments, so an advance can never quietly run on for months.

And a fourth, structural: `uniq_salary_advance_open_per_period` — a UNIQUE
INDEX, not a Python check — means one live advance per employee per recovery
period. Asking twice in one month cannot pay twice even if both requests are
in flight at the same moment. A declined or cancelled advance is excluded from
the index, so a person refused in error can be raised again.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel

ZERO = Decimal('0.00')

# CFO 13-Sep-2026: at most one third of monthly BASIC.
MAX_FRACTION_OF_SALARY = Decimal('1') / Decimal('3')


class SalaryAdvance(AuditableMixin, BaseModel):
    """One early salary advance: a receivable recovered from the next payslip."""

    class Status(models.TextChoices):
        PENDING_CFO = 'pending_cfo', 'Awaiting CFO approval'
        APPROVED    = 'approved',    'Approved by the CFO'
        DECLINED    = 'declined',    'Declined'
        CANCELLED   = 'cancelled',   'Cancelled'
        RECOVERED   = 'recovered',   'Recovered from payroll'

    # States that hold the employee's one slot for the recovery period.
    OPEN_STATUSES = (Status.PENDING_CFO, Status.APPROVED, Status.RECOVERED)

    employee = models.ForeignKey('payroll.Employee', on_delete=models.PROTECT,
                                 related_name='salary_advances')
    company  = models.ForeignKey('core.Company', null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name='salary_advances')
    # The payroll month the advance is RECOVERED in — "the very next payslip".
    recovery_period = models.CharField(
        max_length=10, db_index=True,
        help_text='Payroll period the full amount is deducted in, e.g. 2026-10.')

    amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO,
                                 help_text='BWP advanced — recovered in full.')
    # Valuation snapshot, server-computed at request time and never client input,
    # so a later salary change cannot retrospectively justify a bigger advance.
    basic_salary = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO,
        help_text='Monthly BASIC (BWP) from the latest payslip at request time.')
    basic_source = models.CharField(max_length=20, blank=True, default='',
                                    help_text='Period the BASIC snapshot came from.')
    max_allowed = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO,
        help_text='One third of basic_salary — the cap that applied on the day.')
    reason = models.TextField(blank=True, default='')

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PENDING_CFO, db_index=True)

    requested_by = models.ForeignKey(User, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name='+')
    # The CFO, personally. Never a role, never a delegate.
    cfo_approver    = models.ForeignKey(User, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name='+')
    cfo_approved_at = models.DateTimeField(null=True, blank=True)
    decision_notes  = models.TextField(blank=True, default='')

    # The payroll amendment this advance's recovery was fed into (idempotency
    # link — the same shape as IncentiveLine.payroll_amendment).
    payroll_amendment = models.ForeignKey(
        'payroll.PayrollAmendment', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='salary_advances')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'Salary Advance'
        verbose_name_plural = 'Salary Advances'
        constraints = [
            # DATABASE-LEVEL no-double-advance. Two requests for the same
            # person and the same recovery month cannot both exist while
            # either is live, however they arrive.
            models.UniqueConstraint(
                fields=['employee', 'recovery_period'],
                condition=models.Q(status__in=['pending_cfo', 'approved', 'recovered']),
                name='uniq_salary_advance_open_per_period'),
        ]

    def __str__(self):
        return (f'{self.employee.full_name} — advance {self.amount} '
                f'recovered {self.recovery_period} ({self.status})')


class SalaryAdvancePayout(BaseModel):
    """A record that Finance paid an approved advance out by hand.

    CFO decision, 13 September 2026: **Omni does not raise the payout.** Finance
    raise the payment themselves; Omni records the advance and its recovery.
    That leaves a gap nothing was watching — an advance approved and never paid,
    a payment made with no approved advance behind it, or a recovery deducted
    from somebody who never got the cash — so the payout is written down here
    and `payroll.advance_mismatch` reports the disagreements.

    This row is a RECORD of something that already happened at the bank. It
    raises nothing, releases nothing and posts no journal. Money leaves at FNB
    under a human's two-factor, never from Omni.

    `advance` is nullable on purpose: a payout keyed in with no approved advance
    behind it is exactly one of the three things the report exists to surface,
    and a mandatory link would make it impossible to record — and therefore
    impossible to see.
    """

    employee  = models.ForeignKey('payroll.Employee', on_delete=models.PROTECT,
                                  related_name='salary_advance_payouts')
    advance   = models.ForeignKey(SalaryAdvance, null=True, blank=True,
                                  on_delete=models.SET_NULL,
                                  related_name='payouts',
                                  help_text='The approved advance this settles. '
                                            'Blank = none was found.')
    amount    = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO,
                                    help_text='BWP paid out by Finance.')
    paid_on   = models.DateField(help_text='Date Finance paid it (Botswana time).')
    reference = models.CharField(max_length=120, blank=True, default='',
                                 help_text='Bank / payment reference, for the trail.')
    notes     = models.TextField(blank=True, default='')
    recorded_by = models.ForeignKey(User, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-paid_on', '-created_at']
        verbose_name = 'Salary Advance Payout'
        verbose_name_plural = 'Salary Advance Payouts'

    def __str__(self):
        return f'{self.employee.full_name} — payout {self.amount} on {self.paid_on}'
