"""
payroll/period_guard.py — the ONE rule that decides whether an automatic
payroll feed may write into a month.

CFO, 16-Sep-2026 (payroll.docx §3, plus his clarification the same afternoon:
*"it only works for the month of september, no going backward"*):

  1. **Current month only.** An automatic feed writes into the month we are
     actually in — never into an earlier one. Approving a June incentive today
     does not push it into June's payroll; it is recorded and waits for Finance
     on the reconciliation screen.
  2. **OPEN only.** Once Finance has locked, approved, posted or paid a month,
     no automatic line may appear behind them.

Why this lives in one file: `payroll.loan_service` already refused a non-OPEN
period on its own (`staff_loan.write_only_period_status`), while the incentive
and commission feeds refused nothing at all. Three feeds giving three different
answers is how a control ends up with a hole — the hole the CFO's instruction
was written to close.

Rule 2 applies to every feed that goes through `payroll.feed_common`. Rule 1 is
asked for by the two feeds that carry a backlog (incentives and commissions);
a leaver's severance or leave settlement is a single dated event, not a backlog,
so it is not back-dated by this rule.

A refusal is a sentence a person can act on, and it is written to the audit
trail — a silent skip reads as "nothing to do", which is how a blocked feed
sits unnoticed for a month.
"""
from __future__ import annotations

import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


class PeriodClosed(Exception):
    """An automatic feed tried to write into a month it may not touch."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def current_period_label() -> str:
    """The month we are in, in Gaborone time (Africa/Gaborone, CAT = UTC+2).

    `timezone.localtime` and not `date.today()`: on the server's UTC clock the
    last two hours of every month already read as the next one, which would let
    a feed write into a month Finance has not opened.
    """
    return timezone.localtime(timezone.now()).strftime('%Y-%m')


def refusal(period, *, source: str, current_month_only: bool = False) -> str:
    """Why `source` may NOT write into `period` — or `''` if it may.

    A sentence rather than a boolean so the API, the batch and the audit trail
    all say the same thing, and so a refused feed explains itself.
    """
    from payroll.models import PayrollPeriod

    if period is None:
        return f'{source}: there is no payroll period to write into.'

    if current_month_only:
        now_label = current_period_label()
        # Compare on start_date, not on the label. `period_name` is free text
        # ("e.g. 2026-05" is only a help_text), and a period labelled any other
        # way would fail to match the current month and refuse EVERY feed —
        # a control that blocks everything reads as "payroll is broken", which
        # is how a good control gets switched off. The date cannot drift.
        start = getattr(period, 'start_date', None)
        this_month = start.strftime('%Y-%m') if start else (period.period_name or '')
        if this_month != now_label:
            return (f'{source} may only feed the current month ({now_label}). '
                    f'{period.period_name} is an earlier month and an automatic '
                    f'feed never back-dates — Finance releases it from the '
                    f'payroll reconciliation screen instead.')

    if period.status != PayrollPeriod.Status.OPEN:
        return (f'{source} cannot write into {period.period_name}: the month is '
                f'{period.get_status_display()}, not Open. Finance must reopen it '
                f'deliberately before any automatic line can be added.')
    return ''


def record_refusal(period, *, source: str, reason: str, user=None) -> None:
    """Leave an audit row naming the source, the target month and the reason."""
    from core.models import AuditLog

    AuditLog.objects.create(
        table_name='payroll_payrollperiod',
        record_id=str(getattr(period, 'pk', '') or ''),
        action=AuditLog.Action.UPDATE,
        user=user if getattr(user, 'pk', None) else None,
        description=f'REFUSED: {reason}',
        new_values={
            'source': source,
            'target_period': getattr(period, 'period_name', None),
            'period_status': getattr(period, 'status', None),
            'current_month': current_period_label(),
            'reason': reason,
        },
    )
    logger.warning('period_guard refused %s for %s: %s', source,
                   getattr(period, 'period_name', '?'), reason)


def ensure_writable(period, *, source: str, user=None,
                    current_month_only: bool = False) -> None:
    """Raise `PeriodClosed` if the feed may not write into this month.

    The backstop. Every feed that builds its batch through
    `payroll.feed_common.canonical_batch` passes through here, so a service, an
    endpoint, a retry and a management command all meet the same refusal; there
    is no path that writes an automatic amendment without asking.

    🔴 IT DELIBERATELY DOES NOT WRITE THE AUDIT ROW. This runs INSIDE the
    caller's `transaction.atomic()` — the batch and its amendments are one unit
    of work. An AuditLog written here is rolled back the instant the exception
    leaves that block, so the refusal would record NOTHING while every test
    that exercises the early path still passed. Caught by DeepSeek in the
    16-Sep-2026 ship-gate, against an otherwise-passing panel; my own audit test
    only ever exercised the early check, which runs outside the transaction, so
    it proved the symptom and not the mechanism.

    The audit therefore belongs where the refusal is HANDLED, after the
    rollback: `record_refusal` is called from each feed's
    `except PeriodClosed` block, and from the early check.
    """
    why = refusal(period, source=source, current_month_only=current_month_only)
    if why:
        raise PeriodClosed(why)
