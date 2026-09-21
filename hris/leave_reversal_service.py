"""hris/leave_reversal_service.py — raise a claim, manager decides.

Kept deliberately small (CFO 2026-08-10). The only rules in code are the ones a
manager cannot reasonably be asked to police by hand: you may not claim more days
than the leave holds, you may not claim somebody else's leave, and a refusal must
carry a reason. Everything else — whether the person really did work those days —
is the manager's call.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from hris.leave_reversal_models import MIN_REASON_CHARS, LeaveReversal
from hris.models import LeaveRequest

log = logging.getLogger(__name__)
ZERO = Decimal('0.00')
HALF = Decimal('0.5')


def _as_days(raw) -> Decimal:
    try:
        days = Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        raise ValidationError('Enter the number of days you worked, e.g. 0.5, 1 or 5.')
    if days < HALF:
        raise ValidationError('The smallest reversal is half a day.')
    # Half-day granularity: leave itself is only ever whole or half days, so 0.7
    # would credit back something that was never taken.
    if (days * 2) != (days * 2).to_integral_value():
        raise ValidationError('Days must be a whole or half day — 0.5, 1, 1.5, 2 …')
    return days


def days_still_reversible(lr: LeaveRequest) -> Decimal:
    """What is left to claim: the leave's days, less anything already claimed.

    Pending claims count. Two claims for the same day, each individually valid,
    must not both be approvable.
    """
    taken = sum(
        (r.days or ZERO) for r in lr.reversals.filter(
            status__in=[LeaveReversal.Status.PENDING, LeaveReversal.Status.APPROVED])
    ) or ZERO
    return max(ZERO, (lr.days or ZERO) - Decimal(taken))


def request_reversal(*, leave_request: LeaveRequest, user, days, reason: str,
                     attachment=None) -> LeaveReversal:
    """The employee claims they worked *days* of their own approved leave."""
    reason = (reason or '').strip()
    if len(reason) < MIN_REASON_CHARS:
        raise ValidationError(
            f'Please say why you worked these days, in at least {MIN_REASON_CHARS} '
            'characters. Your manager reads this.')

    if leave_request.status != LeaveRequest.Status.APPROVED:
        raise ValidationError(
            'Only approved leave can be reversed. This request is '
            f'{leave_request.get_status_display().lower()}.')

    # Employee initiates only.
    from hris.feature_views import _profile_for
    if leave_request.profile_id != getattr(_profile_for(user), 'id', None):
        raise ValidationError(
            'Only the employee who took the leave can raise a reversal. '
            'If they cannot, HR corrects the record instead.')

    days = _as_days(days)
    available = days_still_reversible(leave_request)
    if days > available:
        if available <= ZERO:
            raise ValidationError(
                'Every day of this leave has already been claimed back.')
        raise ValidationError(
            f'This leave has {available} day(s) left to claim, so you cannot '
            f'reverse {days}.')

    with transaction.atomic():
        rev = LeaveReversal.objects.create(
            leave_request=leave_request,
            profile_id=leave_request.profile_id,
            days=days,
            reason=reason,
            attachment=attachment or None,
            original_days=leave_request.days or ZERO,
            original_start_date=leave_request.start_date,
            original_end_date=leave_request.end_date,
            original_status=leave_request.status,
            requested_by=user,
        )
        rev.save(audit_user=user,
                 audit_description=f'Leave reversal raised for {days}d on '
                                   f'leave {leave_request.pk}')
    return rev


#: Hours in a day below which we would not call it "worked". A person who logged
#: twenty minutes was not at work; a person who logged four hours probably was.
WORKED_HOURS_THRESHOLD = Decimal('2')


def timedoctor_evidence(lr: LeaveRequest) -> dict:
    """What Time Doctor logged across the days this leave covered.

    CFO 2026-08-10: *"it can also compare with timedoctor hours and advise
    manager."* The manager still decides — this only puts the tracked hours next to
    the claim so the decision is made on evidence instead of on the employee's word.

    Read from WorkdayJustification.tracked_hours, which is the ALREADY-MERGED
    per-day figure the workforce brief uses. Never re-derive it from the Time
    Doctor payload: desktop and laptop are one merged figure and must stay merged.
    """
    out = {'available': False, 'days': [], 'days_with_hours': 0.0,
           'total_hours': 0.0, 'advice': ''}
    if not (lr.start_date and lr.end_date):
        return out
    try:
        from hris.models import WorkdayJustification
        rows = (WorkdayJustification.objects
                .filter(profile_id=lr.profile_id,
                        work_date__gte=lr.start_date, work_date__lte=lr.end_date)
                .order_by('work_date'))
        days, worked, total = [], 0, Decimal('0')
        for r in rows:
            tracked = Decimal(r.tracked_hours or 0)
            total += tracked
            counts = tracked >= WORKED_HOURS_THRESHOLD
            if counts:
                worked += 1
            days.append({'date': r.work_date.isoformat(),
                         'tracked_hours': float(round(tracked, 2)),
                         'required_hours': float(round(Decimal(r.required_hours or 0), 2)),
                         'looks_worked': counts})
        out['days'] = days
        out['days_with_hours'] = float(worked)
        out['total_hours'] = float(round(total, 2))
        out['available'] = bool(days)
    except Exception:      # noqa: BLE001 — evidence is a nicety, never a blocker
        log.exception('could not read Time Doctor hours for leave %s', lr.pk)
        return out

    out['advice'] = _advise(out['available'], Decimal(str(out['days_with_hours'])))
    return out


def _advise(available: bool, days_with_hours: Decimal) -> str:
    """A plain sentence for the manager. Never a decision — only what the data says."""
    if not available:
        return ('Time Doctor has no record for these dates, so there is nothing to '
                'check the claim against. Decide on what you know.')
    if days_with_hours == 0:
        return (f'Time Doctor shows no day with {WORKED_HOURS_THRESHOLD}+ hours '
                'across this leave. Nothing here supports the claim — ask before '
                'you approve.')
    return (f'Time Doctor shows {days_with_hours:g} day(s) with '
            f'{WORKED_HOURS_THRESHOLD}+ hours logged during this leave. '
            'Compare that with the days claimed.')


def evidence_vs_claim(lr: LeaveRequest, claimed_days) -> str:
    """One line comparing the claim with the hours. Advisory only."""
    ev = timedoctor_evidence(lr)
    if not ev['available']:
        return ev['advice']
    claimed = Decimal(str(claimed_days or 0))
    logged = Decimal(str(ev['days_with_hours']))
    if claimed <= logged:
        return (f'{ev["advice"]} The claim of {claimed:g} day(s) is within that.')
    return (f'{ev["advice"]} The claim of {claimed:g} day(s) is MORE than the '
            f'{logged:g} day(s) Time Doctor shows — worth asking about.')


def approver_for(lr: LeaveRequest):
    """The existing leave approver hierarchy — whoever decided the original leave,
    then the employee's line manager, then the EXCO catch-all. No new hierarchy."""
    if lr.approver_id:
        return lr.approver
    # HRISProfile.manager points at payroll.Employee, and payroll.Employee.user is
    # the login (related_name='employee_record') — the link feature_views uses.
    mgr = getattr(lr.profile, 'manager', None)
    mgr_user = getattr(mgr, 'user', None) if mgr is not None else None
    if mgr_user is not None:
        return mgr_user
    from hris.leave_email import _fallback_approver
    user, _addr = _fallback_approver()
    return user


def decide_reversal(*, reversal: LeaveReversal, user, approve: bool,
                    notes: str = '') -> LeaveReversal:
    """Approve or decline. Approving credits the days back and shortens the leave."""
    notes = (notes or '').strip()
    if not approve and not notes:
        raise ValidationError('Give the employee a reason for declining. '
                              'They are shown exactly what you write here.')

    with transaction.atomic():
        # Lock: without it a double-click credits the same days twice and the
        # balance is simply wrong.
        locked = (LeaveReversal.objects.select_for_update()
                  .filter(pk=reversal.pk).first())
        if locked is None:
            raise ValidationError('Reversal not found.')
        if locked.status != LeaveReversal.Status.PENDING:
            raise ValidationError(
                f'This reversal has already been {locked.get_status_display().lower()}.')

        lr = LeaveRequest.objects.select_for_update().get(pk=locked.leave_request_id)
        locked.approver = user
        locked.decided_at = timezone.now()
        locked.decision_notes = notes
        locked.status = (LeaveReversal.Status.APPROVED if approve
                         else LeaveReversal.Status.DECLINED)
        if approve:
            _credit_back(locked, lr, user)

        locked.save(audit_user=user,
                    audit_description=('Leave reversal approved' if approve
                                       else 'Leave reversal declined'),
                    update_fields=['status', 'approver', 'decided_at',
                                   'decision_notes', 'updated_at'])
    return locked


def _log_leave_shortened(lr: LeaveRequest, rev: LeaveReversal, user) -> None:
    """Write the leave's own audit entry.

    The column is written with .update() to preserve the recompute-on-save guard,
    which means AuditableMixin does not fire for the LeaveRequest. Logging it by
    hand keeps "who shortened this leave, and by how much" answerable from the
    audit trail rather than only from the reversal table.
    """
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            table_name='hris_leaverequest', record_id=str(lr.pk),
            action=AuditLog.Action.UPDATE, user=user,
            old_values={'days': str(rev.original_days),
                        'status': rev.original_status},
            new_values={'days': str(lr.days), 'status': lr.status},
            description=(f'Leave shortened by {rev.days} day(s) — post-leave '
                         f'reversal {rev.pk} approved'))
    except Exception:      # noqa: BLE001 — the credit itself must not fail on this
        log.exception('could not write the audit entry for reversal %s', rev.pk)


def _credit_back(rev: LeaveReversal, lr: LeaveRequest, user) -> None:
    """Shorten the leave by the approved days.

    The balance engine counts APPROVED + PENDING leave by `days`, so reducing
    `days` restores the balance with no second place to keep in step — the same
    single-source reason the accrual fix routed the apply gate through
    balances_for_profile.
    """
    remaining = (lr.days or ZERO) - (rev.days or ZERO)
    if remaining < ZERO:
        # days_still_reversible should make this unreachable, but a negative day
        # count silently GRANTS leave, so refuse loudly rather than clamp.
        raise ValidationError(
            'That would reverse more days than the leave request holds. '
            'Ask HR to check this record.')

    # LeaveRequest.save() RECOMPUTES days from the dates on every save, on purpose:
    # "so frontend cannot fake a smaller-than-actual leave value to evade balance
    # limits". That guard protects the API create/update path and must stay. An
    # approved reversal is the one legitimate way days may differ from the date
    # span, so it is written straight to the column instead of weakening the guard
    # for everybody. The change is still traceable — the LeaveReversal row carries
    # who claimed it, who approved it, when, and what the leave said beforehand.
    changes = {'days': remaining, 'updated_at': timezone.now()}
    if remaining == ZERO:
        # Nothing left of it — the leave was in effect never taken. Cancelled, not
        # deleted: the record and its reversal are the audit trail.
        changes['status'] = LeaveRequest.Status.CANCELLED
        changes['decision_notes'] = (
            (lr.decision_notes or '') +
            f'\nFully reversed on {timezone.localdate().isoformat()} — the employee '
            'worked every day of this leave.').strip()
    LeaveRequest.objects.filter(pk=lr.pk).update(**changes)
    lr.refresh_from_db()

    _log_leave_shortened(lr, rev, user)

    # Only when the WHOLE leave is gone can the attendance record be un-stamped
    # safely: a partial claim does not say which days were worked, and guessing
    # would either excuse a real absence or chase a day the person did take off.
    if remaining == ZERO:
        try:
            from hris.leave_backfill import unbackfill_workdays_for_leave
            unbackfill_workdays_for_leave(lr)
        except Exception:   # noqa: BLE001 — never block the credit itself
            log.exception('un-stamping attendance failed for reversal %s', rev.pk)
