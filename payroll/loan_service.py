"""
payroll/loan_service.py

CFO directive 2026-05-24 — Payroll + HR upgrades pass, item #2.

`apply_loan_repayments(period)` walks every ACTIVE EmployeeLoan whose
`start_period.start_date` is on or before `period.start_date`, computes the
monthly amortisation (straight-line when annual_rate_pct == 0, otherwise
the standard PMT formula), and:

  1. Inserts (or updates) a LoanRepayment row for (loan, period) — idempotent.
  2. Materialises a PayslipLine on the employee's Payslip for `period`
     against component_code = 'LOAN_REPAYMENT' (created if missing).
  3. Decrements `loan.outstanding`; flips `status='paid'` when balance hits 0.

Idempotent — re-running with the same period is a no-op (the existing
LoanRepayment row short-circuits the loop). No GL writes happen here;
the existing GL posting service picks the LOAN_REPAYMENT PayslipLine up
like any other line on its next post.

Bible-check guard: payroll/ only. No ledger / reporting / billing imports.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone


logger = logging.getLogger(__name__)

ZERO       = Decimal('0.00')
TWO_PLACES = Decimal('0.01')

LOAN_REPAYMENT_CODE = 'LOAN_REPAYMENT'


def _q(amount: Decimal) -> Decimal:
    """Round to 2 dp half-up (BWP convention)."""
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _monthly_instalment(loan) -> Decimal:
    """Standard amortising payment per month.

    Straight-line when annual_rate_pct == 0:
        instalment = principal / term_months
    Otherwise standard PMT:
        i = rate/12; n = term_months
        instalment = principal * i / (1 - (1+i)^-n)
    """
    p   = Decimal(loan.principal or ZERO)
    n   = int(loan.term_months or 0)
    if n <= 0 or p <= 0:
        return ZERO

    apr = Decimal(loan.annual_rate_pct or ZERO)
    if apr == 0:
        return _q(p / Decimal(n))

    i = apr / Decimal('100') / Decimal('12')
    # (1+i)^-n via Decimal
    one_plus_i = Decimal('1') + i
    factor = one_plus_i ** (-n)
    instalment = p * i / (Decimal('1') - factor)
    return _q(instalment)


def _split_principal_interest(loan, instalment: Decimal) -> tuple[Decimal, Decimal]:
    """For a given instalment, split into (principal, interest) for THIS period."""
    apr = Decimal(loan.annual_rate_pct or ZERO)
    outstanding = Decimal(loan.outstanding or ZERO)
    if apr == 0:
        return (_q(min(instalment, outstanding)), ZERO)
    monthly_rate = apr / Decimal('100') / Decimal('12')
    interest_due = _q(outstanding * monthly_rate)
    principal_part = _q(instalment - interest_due)
    if principal_part > outstanding:
        principal_part = _q(outstanding)
    return (principal_part, interest_due)


def _ensure_loan_repayment_component():
    """Make sure a PayslipComponent with code=LOAN_REPAYMENT exists, and its
    GL posting account is bound. Delegates the binding to the single shared
    helper (bind_loan_repayment_account, below) — never duplicates it."""
    from .models import PayslipComponent
    comp, _ = PayslipComponent.objects.get_or_create(
        code=LOAN_REPAYMENT_CODE,
        defaults={
            'name':       'Loan Repayment',
            'kind':       PayslipComponent.Kind.EMPLOYEE_DEDUCTION,
            'sort_order': 80,
            'is_active':  True,
            'is_taxable': False,
        },
    )
    bind_loan_repayment_account(comp)
    return comp


def bind_loan_repayment_account(component=None):
    """The ONE place that binds the LOAN_REPAYMENT component's GL posting
    account (Prompt 01, Shared Contract v1, 2026-09-12).

    Before this fix, binding was split: staff_loans.services.
    ensure_loan_repayment_component() bound it only on the disburse() path.
    An opening-balance loan (staff_loan_opening, no prior disbursement) or a
    repayment run that happened to touch LOAN_REPAYMENT before any
    disbursement ever had left the component unbound — posting the period
    then failed with "missing posting_account_code". This helper is now
    called from every path that can be first: disburse() (via
    staff_loans.services, which already has the cross-company mismatch
    guard), staff_loan_opening.create_opening_balances(), and
    apply_loan_repayments() below — reusing the SAME resolver, never a
    second implementation.

    Fails loudly (raises) rather than posting to a wrong/blank account:
    staff_loans.services.ensure_loan_repayment_component already refuses a
    mismatched company's receivable code.
    """
    from . import config as payroll_config
    from .models import PayslipComponent

    if component is None:
        component, _ = PayslipComponent.objects.get_or_create(
            code=LOAN_REPAYMENT_CODE,
            defaults={
                'name': 'Loan Repayment',
                'kind': PayslipComponent.Kind.EMPLOYEE_DEDUCTION,
                'sort_order': 80,
                'is_active': True,
                'is_taxable': False,
            },
        )
    if (component.posting_account_code or '').strip():
        return component  # already bound — never overwrite a live binding here

    receivable_code = payroll_config.get_setting('staff_loan.receivable_account_code')
    if not receivable_code:
        # Nothing configured — leave unbound. Posting will fail loudly
        # downstream rather than guess an account (never a wrong/blank post).
        return component

    from staff_loans.services import ensure_loan_repayment_component as _bind_via_staff_loans
    return _bind_via_staff_loans(receivable_code)


def _ensure_payslip(employee, period):
    """Get or create the (employee, period) payslip — DRAFT status."""
    from .models import Payslip
    payslip, _ = Payslip.objects.get_or_create(
        employee=employee, period=period,
        defaults={'company': getattr(employee, 'company', None)},
    )
    return payslip


@transaction.atomic
def apply_loan_repayments(period):
    """Materialise loan deductions for every eligible active loan.

    Returns a summary dict {created, skipped, paid_off, total_principal,
    total_interest}.

    Idempotent: a LoanRepayment already on (loan, period) short-circuits
    that loan (no second deduction, no double-decrement of outstanding).
    """
    from . import config as payroll_config
    from .models import (
        EmployeeLoan, LoanRepayment, PayslipLine,
    )

    # Defense in depth (Prompt 01): the management command already refuses a
    # non-OPEN period before calling this function — keep that guard AND
    # enforce it here too, so a deduction can never be written into an
    # already-calculated/posted period no matter what calls this directly.
    write_only_status = payroll_config.get_setting('staff_loan.write_only_period_status', 'open')
    if period.status != write_only_status:
        raise ValueError(
            f'{period.period_name} is not {write_only_status} — refusing to write a '
            'loan deduction into it (would corrupt an already-calculated/posted period).'
        )

    component = _ensure_loan_repayment_component()

    summary = {
        'created':         0,
        'skipped':         0,
        'paid_off':        0,
        'total_principal': ZERO,
        'total_interest':  ZERO,
    }

    eligible = (
        EmployeeLoan.objects
        .filter(status=EmployeeLoan.Status.ACTIVE)
        .filter(outstanding__gt=0)
        .select_related('employee', 'start_period')
    )

    recompute_on_apply = payroll_config.get_bool('payroll.loan_recompute_on_apply', True)
    touched_payslip_ids: set = set()

    for loan in eligible:
        # Loan must have started on or before the target period.
        if loan.start_period_id:
            if loan.start_period.start_date > period.start_date:
                continue
        # Idempotent guard.
        if LoanRepayment.objects.filter(loan=loan, period=period).exists():
            summary['skipped'] += 1
            continue

        instalment = _monthly_instalment(loan)
        if instalment <= 0:
            summary['skipped'] += 1
            continue

        principal_part, interest_part = _split_principal_interest(loan, instalment)
        instalment_actual = _q(principal_part + interest_part)
        if instalment_actual <= 0:
            summary['skipped'] += 1
            continue

        # 1. LoanRepayment ledger row.
        LoanRepayment.objects.create(
            loan=loan,
            period=period,
            principal=principal_part,
            interest=interest_part,
            posted_at=timezone.now(),
        )

        # 2. PayslipLine on the employee's payslip.
        payslip = _ensure_payslip(loan.employee, period)
        line, created = PayslipLine.objects.get_or_create(
            payslip=payslip, component=component,
            defaults={
                'amount': instalment_actual,
                'notes':  f'Auto-loan repayment for loan {loan.id}',
            },
        )
        if not created:
            # Multiple loans for the same employee — accumulate the line.
            line.amount = _q(Decimal(line.amount or ZERO) + instalment_actual)
            line.save(update_fields=['amount', 'updated_at'])
        touched_payslip_ids.add(payslip.pk)

        # 3. Decrement outstanding, flip status if fully paid.
        loan.outstanding = _q(Decimal(loan.outstanding or ZERO) - principal_part)
        update_fields = ['outstanding', 'updated_at']
        if loan.outstanding <= 0:
            loan.outstanding = ZERO
            loan.status = EmployeeLoan.Status.PAID
            update_fields.append('status')
            summary['paid_off'] += 1
        loan.save(update_fields=update_fields)

        summary['created']         += 1
        summary['total_principal'] += principal_part
        summary['total_interest']  += interest_part

    # Before recomputing: make the payslip agree with the loan ledger. A row
    # whose line was destroyed by an amendment rebuild is repaired here rather
    # than skipped, which is what left the deduction permanently missing.
    summary['repaired'], summary['mismatched'] = _reconcile_loan_lines(
        period, component, touched_payslip_ids)

    # Recompute + save every payslip this run touched (Prompt 01 — the actual
    # defect being fixed). Previously the LOAN_REPAYMENT line was materialised
    # but Payslip.{gross,paye,net,ctc}_amount were never refreshed, so
    # net_amount stayed stale: a loan-only employee (a deduction with no other
    # amendment this period) ended the run with a net figure that did NOT
    # include their own deduction, and payroll/services._net_payable_total()
    # (which reads the STORED net_amount, not the live lines) would then
    # either fail to balance the period JE or overstate net pay.
    if recompute_on_apply and touched_payslip_ids:
        from .models import Payslip
        for ps in Payslip.objects.filter(pk__in=touched_payslip_ids):
            ps.recompute_totals()
            ps.save(update_fields=['gross_amount', 'paye_amount', 'net_amount', 'ctc_amount', 'updated_at'])

    logger.info('apply_loan_repayments(%s) → %s', period.period_name, summary)
    return summary


def _reconcile_loan_lines(period, component, touched_payslip_ids):
    """Every LoanRepayment row for this period must show on the payslip once.

    The guard above is keyed on the ROW, which is right for not charging the
    balance twice but says nothing about the LINE. An amendment batch applied
    afterwards rebuilds the payslip and deletes the line while the row
    survives, so the old code skipped and the deduction stayed gone.

    This reconstructs the line DETERMINISTICALLY from the rows for THIS period
    only - never by copying a prior month's deduction - and never touches
    outstanding, which those rows already charged.
    """
    from django.db.models import Sum
    from .contract_models import LoanRepayment
    from .models import Payslip, PayslipLine

    repaired = 0
    mismatched = 0
    rows = (LoanRepayment.objects.filter(period=period)
            .values('loan__employee_id')
            .annotate(p=Sum('principal'), i=Sum('interest')))
    for r in rows:
        due = _q(Decimal(r['p'] or ZERO) + Decimal(r['i'] or ZERO))
        if due <= 0:
            continue
        payslip = Payslip.objects.filter(
            employee_id=r['loan__employee_id'], period=period).first()
        if payslip is None:
            continue
        line = PayslipLine.objects.filter(
            payslip=payslip, component=component).first()
        if line is None:
            PayslipLine.objects.create(
                payslip=payslip, component=component, amount=due,
                notes='Auto-loan repayment (line restored to match the ledger)',
            )
            touched_payslip_ids.add(payslip.pk)
            repaired += 1
        elif _q(Decimal(line.amount or ZERO)) != due:
            # NEVER overwrite a value another writer set. Salary-advance
            # recovery posts to this SAME component (salary_advance_service
            # raises a DEDUCTION_ADD amendment on LOAN_REPAYMENT), and Finance
            # may key a figure by hand. Forcing the line to the loan ledger
            # makes the two engines flip-flop - whichever ran last wins,
            # silently, every run (Fable, 16-Sep-2026, proved by running it).
            # A reconciler may CREATE what is missing; it may never REWRITE.
            mismatched += 1
            logger.warning(
                'payslip %s LOAN_REPAYMENT is %s but the loan ledger says %s '
                '- left alone, another writer owns this line (salary advance '
                'recovery, or a manual Finance edit)',
                payslip.pk, line.amount, due)
    return repaired, mismatched
