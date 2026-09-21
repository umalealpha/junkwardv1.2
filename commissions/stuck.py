"""commissions/stuck.py — the commission-approval queue, as a stuck-work watcher.

CFO 2026-08-20: an August commission sat two days at the first review and June
(22) + July (20) had never been approved at all. The review screen worked —
it was simply on nobody's to-do list. Registering the queue here gives every
pending submission a named owner, a daily reminder and a deadline.

Owed by = the roster of the stage the submission is actually waiting at, resolved
through access.stage_users() so this and the review permission never disagree.
"""
from __future__ import annotations

from django.utils import timezone

from core.stuck_work import PendingItem, Watcher

# 2 working days to review, and the CFO hears about anything past 5.
SLA_DAYS = 2
ESCALATE_DAYS = 5

# The stamp that tells us when the submission landed at its CURRENT stage — so a
# reviewer is only ever chased for the time it has been with THEM.
_LANDED_AT = {
    'submitted':     'submitted_at',
    'second_review': 'first_reviewed_at',
    'final_review':  'second_reviewed_at',
}


def _pending() -> list[PendingItem]:
    from .access import STATUS_STAGE, stage_users
    from .models import CommissionSubmission

    items: list[PendingItem] = []
    rosters: dict[str, tuple] = {}
    qs = (CommissionSubmission.objects
          .filter(status__in=list(_LANDED_AT))
          .select_related('agent', 'group'))
    for sub in qs:
        stage = STATUS_STAGE[sub.status]
        if stage not in rosters:
            rosters[stage] = tuple(stage_users(stage))
        landed = getattr(sub, _LANDED_AT[sub.status], None) or sub.updated_at
        items.append(PendingItem(
            ref=f'{sub.agent.name} — {sub.period_label}',
            detail=f'P{sub.gross_commission:,.2f} · {sub.get_status_display()}',
            since=timezone.localtime(landed).date(),
            owed_by=rosters[stage],
        ))
    return items


COMMISSIONS_WATCHER = Watcher(
    key='commission_approvals',
    label='Commission approvals',
    task_title='Approve the commissions waiting with you',
    url='/commissions',
    sla_days=SLA_DAYS,
    escalate_days=ESCALATE_DAYS,
    pending=_pending,
)
