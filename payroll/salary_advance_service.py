"""
payroll/salary_advance_service.py — the three controls on an Early Salary
Advance (Build Spec B12), and the feed that recovers it. Marker
`AUTO-ADVANCE`.

CFO decision, 13 September 2026:

  1. the CFO approves every advance individually;
  2. the maximum is one third of the person's salary;
  3. it is recovered in full on the very next payslip.

An advance is a RECEIVABLE, never a payment. It reaches payroll as a
LOAN_REPAYMENT deduction — the staff-loan machinery that already exists — and
`staff_loans.services.ensure_loan_repayment_component` remains the ONE place
the GL account is bound. The account code itself comes from
`payroll.config.get_setting('staff_loan.receivable_account_code', '121010')`;
121010 is the account named "Staff Loan". 121000 is "Provision for Bad Debts -
ECL" and must never be used for this. No new constant is introduced here.

Nothing in this module posts a journal or changes a GL mapping.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from payroll import feed_common
from payroll.salary_advance_models import MAX_FRACTION_OF_SALARY, SalaryAdvance

AUTO_BATCH_MARKER = 'AUTO-ADVANCE'
TWO_PLACES = Decimal('0.01')

BATCH_NOTES = ('Auto-generated recovery of CFO-approved early salary advances '
               '(Build Spec B12). A deduction recovering a receivable in full '
               'on the next payslip — pending, reviewed and applied at the '
               'monthly payroll close. Omni never moves money.')


# ─── The controls ─────────────────────────────────────────────────────────────

def cap_for(basic: Decimal) -> Decimal:
    """One third of monthly BASIC, rounded HALF UP to the thebe."""
    return (Decimal(basic or 0) * MAX_FRACTION_OF_SALARY).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP)


def next_period_after(period_label: str) -> str | None:
    """The payroll period immediately AFTER `period_label` — "the very next
    payslip". None when that period does not exist yet."""
    from payroll.models import PayrollPeriod
    current = PayrollPeriod.objects.filter(period_name=period_label).first()
    if current is None:
        return None
    nxt = (PayrollPeriod.objects.filter(start_date__gt=current.start_date)
           .order_by('start_date', 'created_at').first())
    return nxt.period_name if nxt else None


def period_containing(day=None) -> str | None:
    """The payroll period whose dates cover `day` (Botswana today by default)."""
    from payroll.models import PayrollPeriod
    day = day or timezone.localdate()
    p = (PayrollPeriod.objects.filter(start_date__lte=day, end_date__gte=day)
         .order_by('start_date', 'created_at').first())
    return p.period_name if p else None


@transaction.atomic
def request_advance(*, employee, amount, reason: str = '', user=None,
                    recovery_period: str | None = None,
                    taken_in_period: str | None = None) -> SalaryAdvance:
    """Raise an advance request. Refuses anything over one third of BASIC, and
    refuses a second live advance for the same recovery month — the second
    refusal is the DATABASE's, not this function's."""
    from hris.leave_encash_service import latest_basic_for

    amount = Decimal(amount or 0).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValidationError({'amount': 'An advance must be more than zero.'})

    basic, basic_source = latest_basic_for(employee)
    if basic <= 0:
        raise ValidationError({'amount': (
            'No basic salary on file for this employee, so the one-third cap '
            'cannot be applied. Run a payslip for them first.')})
    cap = cap_for(basic)
    if amount > cap:
        raise ValidationError({'amount': (
            f'The most this employee may take is {cap} — one third of their '
            f'monthly basic {basic} (CFO 13-Sep-2026). Requested {amount}.')})

    if recovery_period is None:
        raise ValidationError({'recovery_period': (
            'Say which payroll period recovers this advance. It must be the '
            'very next payslip.')})

    # CFO control 3 — "recovered in full on the very next payslip" — ENFORCED,
    # not described. `next_period_after` existed and was tested from the day it
    # was written and was never once called, so any recovery month the caller
    # chose was accepted: an advance taken in September could be set to recover
    # in December and nothing objected. The month the advance is TAKEN in is
    # the one covering Botswana today unless the caller names it.
    taken = taken_in_period or period_containing()
    if taken is None:
        raise ValidationError({'recovery_period': (
            'No payroll period covers today, so "the very next payslip" cannot '
            'be worked out. Create the current payroll period first.')})
    expected = next_period_after(taken)
    if expected is None:
        raise ValidationError({'recovery_period': (
            f'The payroll period after {taken} does not exist yet, so there is '
            f'no "very next payslip" to recover this advance on. Create it '
            f'first.')})
    if recovery_period != expected:
        raise ValidationError({'recovery_period': (
            f'An advance taken in {taken} is recovered in full on the very '
            f'next payslip, {expected} — not {recovery_period} (CFO '
            f'13-Sep-2026).')})

    try:
        with transaction.atomic():
            return SalaryAdvance.objects.create(
                employee=employee, company=employee.company,
                recovery_period=recovery_period, amount=amount,
                basic_salary=basic, basic_source=basic_source,
                max_allowed=cap, reason=reason, requested_by=user,
                status=SalaryAdvance.Status.PENDING_CFO)
    except IntegrityError as exc:
        # uniq_salary_advance_open_per_period — the database refused it.
        raise ValidationError({'employee': (
            f'{employee.full_name} already has a live salary advance recovered '
            f'in {recovery_period}. One advance per person per month.')}) from exc


def approve(advance: SalaryAdvance, user) -> SalaryAdvance:
    """The CFO, personally, approves one advance. Nobody else, no threshold."""
    from hris.leave_encash_service import is_cfo
    if not is_cfo(user):
        raise ValidationError(
            'Only the CFO approves a salary advance, and every advance is '
            'approved individually (CFO 13-Sep-2026).')
    if advance.status != SalaryAdvance.Status.PENDING_CFO:
        raise ValidationError(f'This advance is already {advance.get_status_display()}.')
    advance.status = SalaryAdvance.Status.APPROVED
    advance.cfo_approver = user
    advance.cfo_approved_at = timezone.now()
    advance.save(update_fields=['status', 'cfo_approver', 'cfo_approved_at',
                                'updated_at'])
    return advance


def decline(advance: SalaryAdvance, user, notes: str = '') -> SalaryAdvance:
    from hris.leave_encash_service import is_cfo
    if not is_cfo(user):
        raise ValidationError('Only the CFO decides a salary advance.')
    advance.status = SalaryAdvance.Status.DECLINED
    advance.cfo_approver = user
    advance.cfo_approved_at = timezone.now()
    advance.decision_notes = notes
    advance.save(update_fields=['status', 'cfo_approver', 'cfo_approved_at',
                                'decision_notes', 'updated_at'])
    return advance


# ─── The feed ─────────────────────────────────────────────────────────────────

def _repayment_component():
    """The LOAN_REPAYMENT deduction, with its GL account bound in the ONE place
    that is allowed to bind it."""
    from staff_loans.services import _receivable_account_code, ensure_loan_repayment_component
    return ensure_loan_repayment_component(_receivable_account_code())


def feed_period(*, period_label: str, user=None, company=None) -> dict[str, Any]:
    """Recover every CFO-approved advance due in `period_label`, in full, as a
    deduction on that month's payroll batch. Idempotent."""
    from payroll.models import PayrollAmendment
    from payroll.eligibility import is_active_for_period, exclusion_reason

    target = feed_common.target_period(period_label)
    if target is None:
        return {'status': 'no_period', 'pushed': 0, 'skipped': 0}
    baseline = feed_common.baseline_for(target)
    if baseline is None:
        return {'status': 'no_baseline', 'pushed': 0, 'skipped': 0}

    due = (SalaryAdvance.objects
           .select_related('employee', 'employee__company')
           .filter(recovery_period=period_label,
                   status__in=[SalaryAdvance.Status.APPROVED,
                               SalaryAdvance.Status.RECOVERED]))
    if company is not None:
        due = due.filter(employee__company=company)

    pushed, skipped = 0, []
    batches: dict[Any, Any] = {}
    comp = None
    with transaction.atomic():
        for adv in due:
            emp = adv.employee
            if emp is None or emp.company_id is None:
                skipped.append({'id': str(adv.pk), 'name': getattr(emp, 'full_name', '?'),
                                'reason': 'employee has no entity'})
                continue
            if not is_active_for_period(emp, target):
                skipped.append({'id': str(adv.pk), 'name': emp.full_name,
                                'reason': (exclusion_reason(emp, target) or
                                           'not on the payroll for this period')
                                + ' — the advance is still owed; Finance must recover it'})
                continue
            if comp is None:
                comp = _repayment_component()
            batch = batches.get(emp.company_id)
            if batch is None:
                batch = feed_common.canonical_batch(
                    target=target, baseline=baseline, company=emp.company,
                    user=user, marker=AUTO_BATCH_MARKER, notes=BATCH_NOTES)
                batches[emp.company_id] = batch
            reason = (f'Early salary advance recovered in full {period_label} '
                      f'(auto) — a receivable, not a payment.')
            if adv.cfo_approved_at:
                approved_on = timezone.localtime(adv.cfo_approved_at).date()
                reason += f' CFO-approved {approved_on}.'
            amd, _created = feed_common.upsert_amendment(
                batch=batch, employee=emp,
                kind=PayrollAmendment.Kind.DEDUCTION_ADD, component=comp,
                amount=adv.amount, reason=reason,
                approver='Auto (CFO-approved salary advance)',
                marker=AUTO_BATCH_MARKER, period_label=period_label)
            fields = []
            if adv.payroll_amendment_id != amd.pk:
                adv.payroll_amendment = amd
                fields.append('payroll_amendment')
            if adv.status != SalaryAdvance.Status.RECOVERED:
                adv.status = SalaryAdvance.Status.RECOVERED
                fields.append('status')
            if fields:
                adv.save(update_fields=fields + ['updated_at'])
            pushed += 1

        for batch in batches.values():
            for sk in skipped:
                feed_common.record_skip(
                    batch=batch, name=sk['name'],
                    reason=f'Advance not recovered: {sk["reason"]}',
                    marker=AUTO_BATCH_MARKER, period_label=period_label,
                    discriminator=sk['id'])
            feed_common.close_batch(batch)

    if not pushed and not batches:
        return {'status': 'nothing_to_push', 'pushed': 0, 'skipped': len(skipped),
                'skipped_detail': skipped, 'period': period_label}
    return {'status': 'pushed', 'pushed': pushed, 'skipped': len(skipped),
            'skipped_detail': skipped, 'period': period_label,
            'batch_ids': [str(b.pk) for b in batches.values()]}
