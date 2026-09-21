"""
hris/overdue_gate.py — the long-overdue-task gate (CFO 2026-08-07).

CFO directive: "an employee should not be able to apply for leave, loan or
incentive if they have a long overdue task — it should require additional
signatures, currently CEO or CFO."

Threshold agreed with the CFO: **2 days**. A task counts as LONG OVERDUE once
it is still open more than 2 calendar days past its deadline. The deadline is
`due_at` + `due_time` (Africa/Gaborone), matching core.models.OmniTask's own
definition — a task due at 16:00 is not overdue at 00:01 the same day.

What the gate does NOT do:
  * It never blocks the application outright. The person still applies; the
    application simply needs a CEO/CFO countersignature before the normal
    approver can approve it (see hris.exec_signoff_service).
  * It NEVER applies to sick, compassionate/bereavement, maternity, paternity
    or any other leave type flagged as exempt below. Nobody is made to chase a
    task before they can report being ill (CFO 2026-08-07).

Tasks with no due date are ignored — there is nothing to be late for.
"""
from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.utils import timezone

# Days past the deadline before a task is "long overdue". CFO 2026-08-07: 2.
DEFAULT_OVERDUE_DAYS = 2

# Leave types that can NEVER be gated, matched on LeaveType.code (case-folded).
# Illness and bereavement are not discretionary — see module docstring.
EXEMPT_LEAVE_CODES = frozenset({
    'sick', 'compassionate', 'bereavement', 'maternity', 'paternity',
    'family_responsibility', 'unpaid_sick', 'injury',
})


def overdue_days_threshold() -> int:
    """Settings-overridable so the CFO can retune without a code change."""
    try:
        return max(0, int(getattr(settings, 'HRIS_OVERDUE_BLOCK_DAYS', DEFAULT_OVERDUE_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_OVERDUE_DAYS


def task_deadline(task):
    """The moment a task is actually late — due_at at due_time, else end of day."""
    if not task.due_at:
        return None
    return dt.datetime.combine(
        task.due_at, task.due_time or dt.time(23, 59, 59),
        tzinfo=timezone.get_current_timezone(),
    )


def overdue_tasks(user, *, as_of=None, days: int | None = None) -> list:
    """Open OmniTasks assigned to `user` that are more than `days` past due.

    Newest deadline first. Returns [] for an anonymous / None user so callers
    never have to special-case it.
    """
    from core.models import OmniTask

    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    now = as_of or timezone.now()
    cutoff_days = overdue_days_threshold() if days is None else days
    cutoff = now - dt.timedelta(days=cutoff_days)

    qs = (OmniTask.objects
          .filter(assignee=user, due_at__isnull=False)
          .exclude(status__in=[OmniTask.Status.DONE, OmniTask.Status.CANCELLED])
          # Cheap date-level prefilter; the exact due_time check happens below.
          .filter(due_at__lte=cutoff.date())
          .order_by('due_at'))
    return [t for t in qs if (d := task_deadline(t)) is not None and d < cutoff]


def overdue_summary(user, *, as_of=None) -> dict:
    """{'count', 'days_threshold', 'tasks': [...]} — the payload the UI, the
    countersign record and the performance panel all share, so one definition
    of "overdue" drives every screen."""
    now = as_of or timezone.now()
    tasks = overdue_tasks(user, as_of=now)
    rows = []
    for t in tasks:
        deadline = task_deadline(t)
        rows.append({
            'id': str(t.id),
            'title': t.title,
            'due': t.due_at.isoformat(),
            'days_overdue': max(0, (now - deadline).days),
            'status': t.status,
        })
    return {
        'count': len(rows),
        'days_threshold': overdue_days_threshold(),
        'tasks': rows,
    }


def leave_type_is_exempt(leave_type) -> bool:
    """True when this leave type can never be gated (illness, bereavement …)."""
    if leave_type is None:
        return False
    code = (getattr(leave_type, 'code', '') or '').strip().lower()
    if code in EXEMPT_LEAVE_CODES:
        return True
    # A type that demands a medical certificate is by definition health leave.
    return bool(getattr(leave_type, 'requires_medical_cert', False))


def plain_reason(summary: dict) -> str:
    """One plain-English sentence for an email, a toast or an approval screen."""
    n = summary.get('count') or 0
    if not n:
        return ''
    days = summary.get('days_threshold', DEFAULT_OVERDUE_DAYS)
    word = 'task' if n == 1 else 'tasks'
    titles = ', '.join(t['title'] for t in summary.get('tasks', [])[:3])
    more = '' if n <= 3 else f' and {n - 3} more'
    return (f'{n} {word} still open more than {days} days past the due date: '
            f'{titles}{more}.')


def signoff_payload(signoff, summary: dict | None = None) -> dict:
    """The SAME "why you are blocked" block on every screen (CFO 2026-08-07).

    The first cut told people the rule but not the work: one line of text, no
    task names, and nothing at all on the loan and incentive screens. A person
    cannot clear what they have not been shown. Every submit response now
    carries this, and the UI renders it as a pop-up listing each task.

    `signoff` may be None (nothing overdue) — the caller can splat this into
    its response either way.
    """
    if signoff is None:
        return {'needs_exec_signoff': False, 'overdue_tasks': [],
                'overdue_count': 0, 'overdue_message': ''}
    snap = summary if summary is not None else (signoff.overdue_snapshot or {})
    tasks = snap.get('tasks') or []
    days = snap.get('days_threshold', DEFAULT_OVERDUE_DAYS)
    n = len(tasks)
    word = 'task' if n == 1 else 'tasks'

    # The fail-safe path has NO task list — the check could not run. Reusing the
    # normal wording here told the person "you have 0 tasks more than 2 days
    # past the due date" over an empty list, which is simply untrue (Fable
    # review 2026-08-07). Say what actually happened.
    if snap.get('check_failed'):
        return {
            'needs_exec_signoff': True,
            'overdue_count': 0,
            'overdue_days_threshold': days,
            'overdue_tasks': [],
            'check_failed': True,
            'overdue_message': (
                'We could not check your outstanding work just now, so this has been '
                'sent for a quick executive look before it is approved. Nothing is '
                'wrong on your side and you do not need to do anything.'),
        }
    # Discretionary leave (CFO 2026-09-10): the CFO signature has nothing to do
    # with overdue work, so the overdue wording must not be shown. Without this
    # branch the applicant reads "You have 0 tasks more than 2 days past the due
    # date" over an empty list — the same untruth the check_failed branch above
    # exists to prevent.
    if snap.get('leave_policy') and not tasks:
        return {
            'needs_exec_signoff': True,
            'overdue_count': 0,
            'overdue_days_threshold': days,
            'overdue_tasks': [],
            'leave_policy': True,
            'overdue_message': (
                'This leave is granted at the company\'s discretion, so the CFO '
                'signs it off after your manager. Your request has been sent. '
                'You must be at work until you are told it is approved.'),
        }
    return {
        'needs_exec_signoff': True,
        'overdue_count': n,
        'overdue_days_threshold': days,
        'overdue_tasks': tasks,
        'overdue_message': (
            f'You have {n} {word} more than {days} days past the due date. '
            f'Your request has been sent, but the CEO or CFO has to sign it off '
            f'before it can be approved. Finish the work below — or agree a new '
            f'date with whoever set it — and this stops happening.'),
    }
