"""core/stuck_work.py — chase work that is SITTING with somebody.

CFO 2026-08-20, on an August commission still unapproved two days after it was
submitted, with 42 unapproved June + July submissions behind it: *"you have not
put tasks for them thats why they were sitting on the commissions, can we create
auto reminders and tasks if someone is sitting on something"*. He is right — a
queue nobody is TASKED with is a queue nobody clears. The commissions review
screen worked; it simply never appeared on anybody's to-do list.

This is the generic engine. A `Watcher` reports what is pending and WHO can clear
it; the sweep keeps ONE rolling OmniTask per (person, queue) — the same pattern
already proven by core/management/commands/helpdesk_pending_reminder.py — so:

  * the 06:30 task digest (taskboard.services.email_open_reminders) emails the
    person every morning while it is open — no new reminder engine;
  * the deadline is when the item BECAME due (landed + sla_days), not today, so
    the task goes genuinely overdue and the 2-day overdue gate
    (hris/overdue_gate.py — leave / loan / incentive need a CEO/CFO countersign)
    and the monthly-feedback rating cap both bite;
  * anything older than `escalate_days` raises ONE escalation email a day to the
    CFO naming who is sitting on what.

Add a queue by appending a Watcher to WATCHERS. Nothing else changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, time, timedelta
from typing import Callable, Iterable, Sequence

from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.html import escape

log = logging.getLogger(__name__)

_OPEN = ('pending', 'in_progress')
# CFO 2026-07-13 — "all tasks due at 4pm".
_DUE_TIME = time(16, 0)


@dataclass(frozen=True)
class PendingItem:
    """One thing sitting in somebody's lap."""
    ref: str                    # what it is, in human words
    detail: str                 # one supporting line (amount / stage)
    since: date                 # the day it landed with them
    owed_by: tuple              # the User objects who can clear it

    def age(self, today: date) -> int:
        return max(0, (today - self.since).days)


@dataclass(frozen=True)
class Watcher:
    key: str                    # stable slug, lands in OmniTask.source
    label: str                  # "Commission approvals"
    task_title: str             # the rolling task's title (also its dedupe key)
    url: str                    # where to go and clear it
    sla_days: int               # days allowed before it is late
    escalate_days: int          # days before the CFO is told
    pending: Callable[[], Sequence[PendingItem]]

    @property
    def source(self) -> str:
        return f'stuck:{self.key}'


def _system_assigner():
    """Tasks are handed out by the CFO — he is the one who wants them cleared."""
    return (User.objects.filter(email__iexact='pganesharajah@alphadirect.co.bw').first()
            or User.objects.filter(is_superuser=True).order_by('id').first())


def _priority(worst_age: int, w: Watcher) -> str:
    if worst_age >= w.escalate_days:
        return 'urgent'
    if worst_age > w.sla_days:
        return 'high'
    return 'normal'


def _body(items: Sequence[PendingItem], w: Watcher, today: date) -> str:
    lines = [f'{w.label} waiting on you. Nothing moves until you approve or reject it.', '']
    for it in sorted(items, key=lambda i: i.since):
        lines.append(f'- {it.ref} — {it.detail} — waiting {it.age(today)} days')
    lines += ['', f'Open: {w.url}']
    return '\n'.join(lines)


def _upsert_task(user, items, w: Watcher, today: date, dry_run: bool) -> str:
    """One rolling task per (person, queue). Returns 'created' or 'refreshed'."""
    from core.models import OmniTask

    worst = max(it.age(today) for it in items)
    # The deadline of the OLDEST item — so the task is as late as the queue is.
    deadline = min(it.since for it in items) + timedelta(days=w.sla_days)
    body, priority = _body(items, w, today), _priority(worst, w)

    existing = (OmniTask.objects
                .filter(assignee=user, title=w.task_title, source=w.source,
                        status__in=_OPEN)
                .order_by('-created_at').first())
    if existing:
        if not dry_run:
            existing.body = body
            existing.priority = priority
            existing.due_at = deadline
            existing.due_time = _DUE_TIME
            existing.source = w.source
            existing.save(update_fields=['body', 'priority', 'due_at', 'due_time',
                                         'source', 'updated_at'])
        return 'refreshed'

    if not dry_run:
        OmniTask.objects.create(
            assigner=_system_assigner(), assignee=user, title=w.task_title,
            body=body, priority=priority, due_at=deadline, due_time=_DUE_TIME,
            status=OmniTask.Status.PENDING, source=w.source)
    return 'created'


def _close_cleared(w: Watcher, still_owing_ids: set, dry_run: bool) -> int:
    """Close this watcher's rolling tasks for anyone who owes nothing now.
    Scoped by title AND source so a person's other work is never touched."""
    from core.models import OmniTask

    stale = (OmniTask.objects
             .filter(title=w.task_title, source=w.source, status__in=_OPEN)
             .exclude(assignee_id__in=still_owing_ids))
    n = stale.count()
    if n and not dry_run:
        now = timezone.now()
        for t in stale:
            t.status = OmniTask.Status.DONE
            t.completed_at = now
            t.save(update_fields=['status', 'completed_at', 'updated_at'])
    return n


def _escalate(w: Watcher, items: Sequence[PendingItem], today: date, dry_run: bool) -> bool:
    """One email a day to the CFO listing what is past `escalate_days` and who is
    sitting on it.

    Idempotent per (watcher, day) through StuckWorkEscalation — the same claim-row
    trick TaskReminderEmailLog uses for the morning digest, and for the same
    reason: a duplicate cron entry or a manual re-run must not re-alarm. The
    OutboundEmailLog is NOT usable as the guard — Django swaps the email backend
    for locmem under test, so nothing would be logged and the guard would only
    look like it worked.
    """
    from core.models import StuckWorkEscalation
    from core.notifications import send_html_with_cfo_cc

    late = [i for i in items if i.age(today) >= w.escalate_days]
    if not late:
        return False
    if dry_run:
        return True

    _, fresh = StuckWorkEscalation.objects.get_or_create(watcher_key=w.key, sent_on=today)
    if not fresh:
        return False        # already raised today

    subject = (f'{w.label} overdue — {len(late)} item(s) past '
               f'{w.escalate_days} days [{today:%d %b %Y}]')

    rows = []
    for it in sorted(late, key=lambda i: i.since):
        who = ', '.join(sorted(
            (u.get_full_name() or u.username) for u in it.owed_by)) or 'nobody assigned'
        rows.append(
            f'<tr><td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">'
            f'<b>{escape(it.ref)}</b><div style="color:#6B7280;font-size:12px;">'
            f'{escape(it.detail)}</div></td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{escape(who)}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">'
            f'{it.age(today)} days</td></tr>')
    html = (
        f'<p>{escape(w.label)} has been sitting past the {w.escalate_days}-day limit. '
        f'The people below have an open Omni task for it and are being reminded daily.</p>'
        '<table style="border-collapse:collapse;width:100%;font-size:14px;">'
        '<tr style="background:#0D1B2A;color:#fff;text-align:left;">'
        '<th style="padding:6px 10px;">Item</th><th style="padding:6px 10px;">Sitting with</th>'
        '<th style="padding:6px 10px;">Waiting</th></tr>'
        + ''.join(rows) + '</table>'
        f'<p style="margin-top:14px;">Open: {escape(w.url)}</p>')
    try:
        send_html_with_cfo_cc(subject, html, ['pganesharajah@alphadirect.co.bw'],
                              text_fallback=f'{len(late)} {w.label} item(s) overdue.')
        return True
    except Exception:   # noqa: BLE001 — an email problem must not lose the tasks
        # Release the day's claim, or a transient SMTP failure would silence the
        # escalation for the whole day (TaskReminderEmailLog does the same).
        StuckWorkEscalation.objects.filter(watcher_key=w.key, sent_on=today).delete()
        log.exception('stuck_work escalation email failed for %s', w.key)
        return False


def sweep(watchers: Iterable[Watcher] | None = None,
          today: date | None = None,
          dry_run: bool = False) -> dict:
    """Raise / refresh / close the rolling tasks, then escalate what is late."""
    today = today or timezone.localdate()
    result = {'queues': 0, 'items': 0, 'tasks_created': 0, 'tasks_refreshed': 0,
              'tasks_closed': 0, 'escalated': 0}

    for w in (watchers if watchers is not None else WATCHERS):
        try:
            items = list(w.pending())
        except Exception:   # noqa: BLE001 — one broken queue must not kill the rest
            log.exception('stuck_work watcher %s failed', w.key)
            continue

        result['queues'] += 1
        result['items'] += len(items)

        by_user: dict[int, list] = {}
        users: dict[int, User] = {}
        for it in items:
            for u in it.owed_by:
                if not u or not (getattr(u, 'email', '') or '').strip():
                    continue    # nobody to remind — skip, never crash
                by_user.setdefault(u.id, []).append(it)
                users[u.id] = u

        for uid, mine in by_user.items():
            result['tasks_' + _upsert_task(users[uid], mine, w, today, dry_run)] += 1

        result['tasks_closed'] += _close_cleared(w, set(by_user), dry_run)
        if _escalate(w, items, today, dry_run):
            result['escalated'] += 1

    return result


def _load_watchers() -> list[Watcher]:
    from commissions.stuck import COMMISSIONS_WATCHER
    from fx.stuck import FX_WATCHER
    return [COMMISSIONS_WATCHER, FX_WATCHER]


WATCHERS: list[Watcher] = _load_watchers()
