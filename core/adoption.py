"""
core/adoption.py — Omni adoption scoreboard (CFO 2026-07-21).

The CFO builds Omni alone against staff who cling to manual Excel. This turns
"the system doesn't work" into facts: per person, are they ACTUALLY using Omni?
Signal = Django login recency + AuditLog activity (every write in Omni is stamped
with the user). Read-only management view; C-suite / HR gated.

Two DB queries total (no per-user N+1): one grouped AuditLog aggregate over the
window + the employee list; last_login is already on the user row.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth.models import User
from django.db.models import Count, Max
from django.utils import timezone

ACTIVE_DAYS = 30       # used Omni within this window = adopted
DORMANT_GRACE = 30     # beyond ACTIVE but has used it before = dormant (else never)


def _title(emp) -> str:
    return (getattr(emp, 'job_title', '') or '').strip()


def compute(days: int = ACTIVE_DAYS, company: str | None = None) -> dict:
    from core.models import AuditLog
    from payroll.models import Employee

    now = timezone.now()
    window = now - dt.timedelta(days=days)

    emps = (Employee.objects.exclude(status=Employee.Status.TERMINATED)
            .select_related('company', 'user'))
    if company:
        # ?company= arrives as the company's UUID id (apiFetch injects the
        # topbar selection) OR as its code from a hand-built URL. Filtering on
        # code alone matched neither and the scoreboard silently showed zero
        # staff — the intel-summary bug class, CFO 2026-07-28.
        from core.mixins import resolve_company
        co = resolve_company(company)
        # Unresolvable value → show nothing, never widen to every entity.
        emps = emps.filter(company_id=co.id) if co else emps.none()
    emps = list(emps)

    # user lookup by email (for employees whose .user FK isn't populated)
    users_by_email = {(u.email or '').lower(): u
                      for u in User.objects.exclude(email='').only('id', 'email', 'last_login')}

    # ONE grouped aggregate: actions + last action per user within the window
    agg = {r['user_id']: r for r in (AuditLog.objects
           .filter(created_at__gte=window).exclude(user=None)
           .values('user_id').annotate(cnt=Count('id'), last=Max('created_at')))}

    rows, active, dormant, never = [], 0, 0, 0
    for e in emps:
        u = e.user or users_by_email.get((e.email or '').lower())
        last_login = getattr(u, 'last_login', None) if u else None
        a = agg.get(u.id) if u else None
        actions = a['cnt'] if a else 0
        last_action = a['last'] if a else None
        seen = [d for d in (last_login, last_action) if d]
        last_seen = max(seen) if seen else None

        if not u or (last_login is None and actions == 0 and last_action is None):
            status = 'never'; never += 1
        elif last_seen is not None and last_seen >= window:
            status = 'active'; active += 1
        else:
            status = 'dormant'; dormant += 1

        rows.append({
            'name': e.full_name,
            'email': e.email or (u.email if u else ''),
            'company': e.company.code if e.company else '',
            'title': _title(e),
            'last_login': last_login.isoformat() if last_login else None,
            'last_action': last_action.isoformat() if last_action else None,
            'actions_window': actions,
            'status': status,
        })

    total = len(rows)
    order = {'never': 0, 'dormant': 1, 'active': 2}
    # worst first; within a bucket, quietest first
    rows.sort(key=lambda r: (order[r['status']], r['actions_window'], (r['last_login'] or '')))
    return {
        'window_days': days,
        'summary': {
            'total': total,
            'active': active,
            'dormant': dormant,
            'never': never,
            'adoption_pct': round(100 * active / total) if total else 0,
        },
        'rows': rows,
    }
