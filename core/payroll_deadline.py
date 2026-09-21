"""
core/payroll_deadline.py — the monthly submission deadline for commissions &
incentives (CFO directive 2026-08-28).

Rule: staff may submit/upload commissions AND incentives only up to the 16th of
the month (a FIXED calendar day — no shift for weekends or holidays). After the
16th staff are hard-blocked; only the CFO may still load a late item. The
payroll close deadline is the 23rd (kept here as context; not enforced by this
module).

ONE rule, ONE place. Both commissions/api_views.py and hris/incentive_service.py
call assert_can_submit(), so the deadline can never drift between the two paths.
"Who is the CFO" is likewise resolved by the SINGLE predicate that owns the
commission final-approver roster (commissions.access.can_review_stage) — never a
second parser of the same env/name list (the H55 two-parsers trap).
"""
from __future__ import annotations

SUBMISSION_DEADLINE_DAY = 16   # inclusive — the 16th itself is still open
PAYROLL_CLOSE_DAY = 23         # context only; not enforced here
DEADLINE_MESSAGE = "Ask CFO to upload — you missed the deadline."


class SubmissionDeadlinePassed(Exception):
    """A non-CFO tried to submit/upload after the 16th."""

    def __init__(self, message: str = DEADLINE_MESSAGE):
        self.message = message
        super().__init__(message)


def _today():
    from django.utils import timezone
    return timezone.localdate()


def window_open(today=None) -> bool:
    """True while the submission window is open (on or before the 16th)."""
    d = today or _today()
    return d.day <= SUBMISSION_DEADLINE_DAY


def is_cfo(user) -> bool:
    """Only the CFO (or a superuser) may load after the deadline.

    Delegate to the ONE predicate that owns the commission final-approver
    roster; do not re-parse COMMISSIONS_FINAL_EMAILS or the name list here (a
    second parser silently drifts — the H55 P90k lesson). Lazy import: access.py
    imports nothing from core, so there is no cycle.
    """
    if getattr(user, "is_superuser", False):
        return True
    from commissions.access import FINAL, can_review_stage
    return can_review_stage(user, FINAL)


def deadline_enforced() -> bool:
    """The 16th-of-month lock is a switch, OFF by default (CFO 19-Sep-2026:
    "unblock the incentive and commission block so people can do it
    themselves"). Set SUBMISSION_DEADLINE_ENFORCED=true in the env to turn it
    back on — no rebuild needed. Reviews and approvals are unaffected."""
    from django.conf import settings
    return bool(getattr(settings, 'SUBMISSION_DEADLINE_ENFORCED', False))


def assert_can_submit(user, today=None) -> None:
    """Gate a commission/incentive submission or upload.

    When the lock is switched on, the CFO overrides at any time and everyone
    else is blocked once the calendar day is past the 16th. Raises
    SubmissionDeadlinePassed when blocked.
    """
    if not deadline_enforced():
        return
    if is_cfo(user):
        return
    if not window_open(today):
        raise SubmissionDeadlinePassed()
