"""payroll/advance_mismatch.py — the three ways a salary advance and its payout
can disagree.

WHY THIS EXISTS (CFO decision, 13 September 2026). Omni does NOT raise the
payout for an early salary advance. Finance raise the payment by hand; Omni
records the advance, the payout Finance tell it about, and the recovery that
comes off the next payslip. The CFO asked for a WARNING, not automation — so
this module only looks, and the three disagreements it looks for are:

  1. **Approved, never paid out.** The CFO signed it and nothing reached the
     employee. Left alone it gets worse, not better: the recovery feed still
     deducts the full amount from the next payslip, so somebody who never got
     the cash has it taken off their pay.
  2. **A payout with no approved advance behind it.** A payment was made
     against an advance Omni cannot find, or against one that was declined or
     cancelled.
  3. **A recovery raised while the advance is not marked as paid out.** The
     deduction is on a payroll batch and Omni has no record that the person
     ever received the advance.

This module READS. It raises nothing, changes nothing, posts no journal and
moves no money.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal('0.00')

#: Statuses that mean the CFO said yes and the advance is live.
LIVE_STATUSES = ('approved', 'recovered')
#: Statuses a payout should never be sitting behind.
DEAD_STATUSES = ('declined', 'cancelled')

#: Days of grace before an approved-but-unpaid advance is worth raising. An
#: advance approved this morning and settled this afternoon is not a mismatch,
#: and a report that flags it teaches the reader to ignore the rest.
DEFAULT_GRACE_DAYS = 2


def _paid_total(advance) -> Decimal:
    agg = advance.payouts.aggregate(t=Sum('amount'))
    return Decimal(agg['t'] or 0)


def approved_not_paid(grace_days: int = DEFAULT_GRACE_DAYS,
                      today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """Advances the CFO approved that Omni has no payout for."""
    from payroll.salary_advance_models import SalaryAdvance

    today = today or timezone.localdate()
    cutoff = today - datetime.timedelta(days=max(0, int(grace_days)))
    out: List[Dict[str, Any]] = []
    # `payroll_amendment__isnull=True` keeps the three lists DISJOINT. An
    # advance with a recovery already raised and no payout is the same fact,
    # and it is reported by `recovery_without_payout` — the more urgent
    # wording, because that deduction is already sitting on a batch. Without
    # this the row appears twice and the headline "value in question" is
    # double what is actually in question, which is the fastest way to lose
    # the reader of a finance report.
    rows = (SalaryAdvance.objects
            .select_related('employee', 'company')
            .filter(status__in=LIVE_STATUSES, payouts__isnull=True,
                    payroll_amendment__isnull=True))
    for adv in rows:
        approved_on = (timezone.localtime(adv.cfo_approved_at).date()
                       if adv.cfo_approved_at else None)
        if approved_on is not None and approved_on > cutoff:
            continue                      # still inside the grace window
        out.append({
            'employee': adv.employee.full_name,
            'employee_number': adv.employee.employee_number or '',
            'entity': getattr(adv.company, 'code', '') or '',
            'approved_on': approved_on.isoformat() if approved_on else '',
            'amount': Decimal(adv.amount or 0),
            'recovery_period': adv.recovery_period,
            'status': adv.get_status_display(),
            'issue': 'Approved, no payout recorded',
            'what_to_do': ('Confirm with Finance whether it was paid. If it was, '
                           'record the payout in Omni. If it was not, the '
                           'recovery must not be deducted.'),
        })
    out.sort(key=lambda r: (-r['amount'], r['employee']))
    return out


def payout_without_advance(today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """Payouts with no approved advance behind them."""
    from payroll.salary_advance_models import SalaryAdvancePayout

    out: List[Dict[str, Any]] = []
    rows = (SalaryAdvancePayout.objects
            .select_related('employee', 'employee__company', 'advance')
            .order_by('-paid_on'))
    for pay in rows:
        adv = pay.advance
        if adv is not None and adv.status in LIVE_STATUSES:
            continue
        if adv is None:
            why = 'No advance record is linked to this payout'
        elif adv.status in DEAD_STATUSES:
            why = f'The linked advance is {adv.get_status_display().lower()}'
        else:
            why = f'The linked advance is still {adv.get_status_display().lower()}'
        out.append({
            'employee': pay.employee.full_name,
            'employee_number': pay.employee.employee_number or '',
            'entity': getattr(pay.employee.company, 'code', '') or '',
            'paid_on': pay.paid_on.isoformat(),
            'amount': Decimal(pay.amount or 0),
            'reference': pay.reference,
            'issue': 'Payout with no approved advance',
            'what_to_do': why + '. Find the approval, or raise the advance so '
                                'the amount is on record and gets recovered.',
        })
    out.sort(key=lambda r: (-r['amount'], r['employee']))
    return out


def recovery_without_payout(today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """Recoveries raised on payroll where the advance is not marked paid out.

    This is the one that costs an employee: the deduction is already on a
    payroll batch, and nothing says they ever received the advance.
    """
    from payroll.salary_advance_models import SalaryAdvance

    out: List[Dict[str, Any]] = []
    rows = (SalaryAdvance.objects
            .select_related('employee', 'company', 'payroll_amendment')
            .filter(payroll_amendment__isnull=False, payouts__isnull=True))
    for adv in rows:
        amd = adv.payroll_amendment
        out.append({
            'employee': adv.employee.full_name,
            'employee_number': adv.employee.employee_number or '',
            'entity': getattr(adv.company, 'code', '') or '',
            'recovery_period': adv.recovery_period,
            'amount': Decimal(adv.amount or 0),
            'applied': 'Yes' if getattr(amd, 'applied', False) else 'Not yet',
            'issue': 'Recovery raised, advance not marked as paid out',
            'what_to_do': ('Hold this deduction until the payout is confirmed. '
                           'Record the payout, or take the recovery off the '
                           'batch.'),
        })
    out.sort(key=lambda r: (-r['amount'], r['employee']))
    return out


def find_mismatches(grace_days: int = DEFAULT_GRACE_DAYS,
                    today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """All three lists plus the headline counts. Read-only."""
    today = today or timezone.localdate()
    unpaid = approved_not_paid(grace_days, today)
    orphan = payout_without_advance(today)
    recovered_unpaid = recovery_without_payout(today)
    value = (sum((r['amount'] for r in unpaid), ZERO)
             + sum((r['amount'] for r in orphan), ZERO)
             + sum((r['amount'] for r in recovered_unpaid), ZERO))
    return {
        'as_at': today,
        'grace_days': int(grace_days),
        'approved_not_paid': unpaid,
        'payout_without_advance': orphan,
        'recovery_without_payout': recovered_unpaid,
        'count': len(unpaid) + len(orphan) + len(recovered_unpaid),
        'value': value,
    }
