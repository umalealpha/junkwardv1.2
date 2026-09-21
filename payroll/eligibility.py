"""
payroll/eligibility.py — ONE rule for whether an employee is on the payroll
for a given period (Shared Contract v1 / Prompt 02, 2026-09-12).

A joiner hired mid-month must appear from that period. A leaver terminated
mid-month must appear for their final period only, then never again. Before
this module the inclusion/exclusion decision was scattered and inconsistent
(some paths read Employee.status, others is_archived, none read the actual
dates) — a leaver whose `status` was never flipped to TERMINATED kept
appearing indefinitely (payroll/archive_service.terminate_employee already
sets both status AND termination_date on exit, but this predicate reads the
DATES, not status, precisely so a stale/unflipped status can never cause a
lingering leaver again).

Date-driven, not status-driven, WHEN THE DATES ARE ON FILE:
  active for `period` when
    hire_date is set and hire_date <= period.end_date, AND
    (termination_date is null OR termination_date > period.start_date), AND
    not is_archived.

CFO decision 2026-09-12: someone terminated ON day 1 of a period is NOT
carried in as a full baseline copy — the old rule (restored here) is that a
leaver only gets that period's payslip when Finance raises an explicit
settlement amendment for what is actually owed, never an automatic full
month by default. So `termination_date == period.start_date` EXCLUDES, same
as a date strictly before it — only a termination STRICTLY AFTER the period
starts leaves someone active for that final period.

A record with NO hire_date (legacy employees predating the field — real in
this repo's own fixtures) is never guessed a date: it falls back to the OLD
signal (status != TERMINATED), so an untouched active legacy employee is
never silently dropped from a roll-forward for lack of a field nobody
back-filled. The moment a hire_date IS present, this predicate is fully
date-driven and status is not consulted at all — the fallback is a
narrow compatibility path, not a second competing rule.
"""
from __future__ import annotations


def is_active_for_period(employee, period) -> bool:
    """True when `employee` belongs on a payslip for `period`.

    `employee`: payroll.models.Employee. `period`: payroll.models.PayrollPeriod
    (only .start_date / .end_date are read, so a lightweight stand-in works
    in tests).
    """
    if employee is None or period is None:
        return False
    if getattr(employee, 'is_archived', False):
        return False
    hire_date = getattr(employee, 'hire_date', None)
    if not hire_date:
        # No hire date on file — compatibility fallback to the pre-Prompt-02
        # signal, never a guessed date. Real, terminated employees still get
        # excluded via status; an active legacy record is never dropped.
        status = getattr(employee, 'status', None)
        terminated_value = getattr(getattr(employee, 'Status', None), 'TERMINATED', 'terminated')
        if status == terminated_value:
            return False
        # ...and a termination DATE is just as positive a signal as the status
        # flip. A legacy row carrying a termination_date whose status was never
        # flipped, with no hire_date, used to read as active for ever - the
        # very bug this predicate exists to kill, surviving inside its own
        # compatibility branch (Fable, 2026-09-12).
        termination_date = getattr(employee, 'termination_date', None)
        if termination_date is not None and termination_date <= period.start_date:
            return False
        return True
    if hire_date > period.end_date:
        return False
    # A positive termination SIGNAL (status flipped) must never be ignored
    # even when termination_date itself is missing — the PATCH status=
    # terminated API (payroll/api_views.py) sets no date, and legacy imports
    # can set status with no dates at all. Without this, such a leaver reads
    # as active forever (the date-only checks below never fire with no
    # date), which is worse than the stale-status bug this predicate exists
    # to fix. Checked BEFORE reading termination_date so a missing date can
    # never quietly waive a real termination.
    status = getattr(employee, 'status', None)
    terminated_value = getattr(getattr(employee, 'Status', None), 'TERMINATED', 'terminated')
    termination_date = getattr(employee, 'termination_date', None)
    if status == terminated_value and termination_date is None:
        return False
    if termination_date is not None and termination_date <= period.start_date:
        return False
    return True


def exclusion_reason(employee, period) -> str:
    """Plain-English reason `is_active_for_period` returned False — for the
    inclusion/exclusion audit log. Empty string if the employee IS active."""
    if employee is None:
        return 'no employee'
    if getattr(employee, 'is_archived', False):
        return 'employee is archived'
    hire_date = getattr(employee, 'hire_date', None)
    if not hire_date:
        status = getattr(employee, 'status', None)
        terminated_value = getattr(getattr(employee, 'Status', None), 'TERMINATED', 'terminated')
        if status == terminated_value:
            return 'no hire date on file; status is terminated (compatibility fallback)'
        termination_date = getattr(employee, 'termination_date', None)
        if (period is not None and termination_date is not None
                and termination_date <= period.start_date):
            return (f'no hire date on file; termination date {termination_date} is on or '
                    f'before this period starts {period.start_date}')
        return ''  # no hire date — compatibility fallback treats them as active
    if period is not None and hire_date > period.end_date:
        return f'hire date {hire_date} is after this period ends {period.end_date}'
    status = getattr(employee, 'status', None)
    terminated_value = getattr(getattr(employee, 'Status', None), 'TERMINATED', 'terminated')
    termination_date = getattr(employee, 'termination_date', None)
    if status == terminated_value and termination_date is None:
        return 'status is terminated with no termination date on file'
    if (period is not None and termination_date is not None
            and termination_date <= period.start_date):
        return (f'terminated {termination_date}, on or before this period starts '
               f'{period.start_date} — no full baseline copy, needs a settlement amendment')
    return ''


def contract_for_period(employee, period):
    """The EmploymentContract active AS OF `period` (not real-world today —
    Employee.current_contract is "today" only, which is wrong for seeding a
    period that has not arrived yet in wall-clock time, e.g. a joiner hired
    next month in a test or a future payroll run prepared in advance)."""
    if employee is None or period is None:
        return None
    as_of = period.end_date
    qs = (employee.contracts
          .filter(status='active', start_date__lte=as_of)
          .filter(_end_ok_q(as_of))
          .order_by('-start_date'))
    return qs.first()


def _end_ok_q(as_of):
    from django.db.models import Q
    return Q(end_date__isnull=True) | Q(end_date__gte=as_of)


def working_days_in_service(employee, period, working_days_per_month: int) -> tuple[int, int]:
    """(days_in_service, working_days_per_month) for proration — a joiner or
    leaver's partial-month share of `working_days_per_month`. Whole month
    (== working_days_per_month) when the employee is active for the whole
    period. Never negative, never more than the month's total."""
    start = max(period.start_date, getattr(employee, 'hire_date', None) or period.start_date)
    term = getattr(employee, 'termination_date', None)
    end = min(period.end_date, term) if term else period.end_date
    if end < start:
        return 0, working_days_per_month
    span_days = (period.end_date - period.start_date).days + 1
    in_service_days = (end - start).days + 1
    if span_days <= 0:
        return working_days_per_month, working_days_per_month
    prorated = round(working_days_per_month * in_service_days / span_days)
    prorated = max(0, min(working_days_per_month, prorated))
    return prorated, working_days_per_month
