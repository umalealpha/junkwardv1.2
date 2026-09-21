"""
core/task_guard.py — who may put an Urgent / short-notice task on the CFO.

CFO directive 2026-07-27, verbatim: "people should not be able to create Urgent
tasks to CFO — usually urgent means 24 hours notice and not urgent 48 hours
notice; people's inefficiency should not be my problem."

Staff kept dumping "URGENT, due today" tasks on the CFO to cover their own late
work. So, for a task whose ASSIGNEE is the CFO, created by anyone who is NOT an
executive:

  * the priority may not be Urgent — Urgent-to-the-CFO is reserved; and
  * a non-urgent task must give at least 48 hours (2 clear days) notice on its
    due date.

Executives — the CFO himself, administrators, superusers, and C-suite (the
`executive` title) — bypass the rule entirely; they carry the authority to
decide what is genuinely urgent (the 24-hour urgent floor is theirs to judge).

Only tasks assigned TO the CFO are governed. Every other task (CFO → staff,
staff → staff) is untouched — this must NOT block the CFO handing out his own
urgent, due-today work.

Kept out of the view so the tests exercise the rule directly (mirrors
payroll/signoff_service.py).
"""
from __future__ import annotations

from datetime import timedelta

from core.models import OmniTask, UserProfile, get_user_profile

# due_at is a DATE, so notice is measured in whole calendar days.
# 48 hours = 2 clear days.
NORMAL_NOTICE_DAYS = 2

# Titles that may set an Urgent / short-notice task for the CFO without limit.
_EXEC_TITLES = frozenset({
    UserProfile.Title.CEO,
    UserProfile.Title.COO,
    UserProfile.Title.CFO,
    UserProfile.Title.EXECUTIVE,
})


def _title(user):
    prof = get_user_profile(user)
    if prof is None or not getattr(prof, "is_active", True):
        return None
    return prof.title


def assignee_is_cfo(user) -> bool:
    """True when the task's assignee holds the CFO title.

    Reads the title directly (not via _title's active gate): the create/patch
    endpoints already require an active assignee, and the rule must still bite
    even if the CFO's profile flag is momentarily off.
    """
    prof = get_user_profile(user)
    return bool(prof) and prof.title == UserProfile.Title.CFO


def creator_is_exec(user) -> bool:
    """CFO / C-suite / administrators / superusers bypass the notice rule."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    prof = get_user_profile(user)
    if prof is None:
        return False
    if getattr(prof, "is_administrator", False):
        return True
    return prof.title in _EXEC_TITLES


def cfo_task_notice_error(assigner, assignee, priority, due_at, today):
    """Return a plain-English error string if this task breaks the CFO notice
    rule, else None.

    `today` is the local date (Africa/Gaborone) the task is being raised on.
    """
    if not assignee_is_cfo(assignee):
        return None
    if creator_is_exec(assigner):
        return None

    if priority == OmniTask.Priority.URGENT:
        return (
            "Urgent tasks for the CFO are reserved for the executive team. "
            "For work you need actioned, set the priority to Normal and give "
            "the CFO at least 2 clear days (48 hours) notice on the due date."
        )

    # Non-urgent task for the CFO: it must carry a real due date at least two
    # clear days out. A missing date would otherwise sidestep the 48h rule.
    if due_at is None:
        return (
            "Tasks for the CFO need a due date at least 2 clear days (48 hours) "
            "ahead — please set one."
        )
    earliest = today + timedelta(days=NORMAL_NOTICE_DAYS)
    if due_at < earliest:
        return (
            "Tasks for the CFO need at least 2 clear days (48 hours) notice. "
            f"The earliest due date you can set is {earliest.isoformat()}."
        )
    return None
