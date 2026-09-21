"""
integrations/timedoctor_recon.py

Reconcile Time Doctor tracked hours against payroll, and surface the adoption
gap. Pure-ish (DB reads only) so it renders gracefully when there is little or
no Time Doctor data yet (it auto-fills as the daily pull stores snapshots).

What it reconciles
------------------
Payroll has NO timesheet — it pays a monthly salary (gross). So we compare each
employee's *tracked* hours (Time Doctor) to an *expected* standard month and to
the salary paid:
    utilization%      = tracked_hours / expected_hours
    cost_per_hour     = gross_paid / tracked_hours   (∞ / n/a if 0 tracked)
Matching: Time Doctor user ↔ payroll Employee by email (exact), else by
normalised full name (parenthetical tags like "(ExCo)" stripped).

PII: this module handles employee names server-side only. The DeepSeek insight
(deepseek_insight) ANONYMISES first — no names leave the box.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

# Botswana standard working month ≈ 22 days × 8h. Override via settings if needed.
EXPECTED_MONTHLY_HOURS = 176.0

# Exception-group thresholds (CFO/Manus "Daily Time Doctor Exceptions" playbook,
# 2026-06-19). Env-overridable without a deploy. Low-hours tiers key off
# utilization (tracked/expected) so they are window-independent — no fragile
# per-day divisor. Idle = neutral share of tracked; unproductive = rated-
# unproductive share of tracked.
import os as _os


def _envf(name: str, default: float) -> float:
    try:
        return float(_os.environ.get(name, '') or default)
    except (TypeError, ValueError):
        return default


TD_UTIL_CRITICAL = _envf('TD_UTIL_CRITICAL_PCT', 25.0)   # tracking but utilization < 25%
TD_UTIL_WARN     = _envf('TD_UTIL_WARN_PCT', 50.0)       # 25% ≤ utilization < 50%
TD_IDLE_PCT      = _envf('TD_IDLE_PCT', 35.0)            # idle (neutral) ≥ 35% of tracked
TD_UNPROD_PCT    = _envf('TD_UNPROD_PCT', 20.0)          # unproductive ≥ 20% of tracked


# The normaliser/tokeniser now live in integrations.td_matching — THE one
# shared TD↔payroll matcher (Fable review 2026-07-14). Kept as aliases so the
# existing imports (send_daily_brief and this module) stay stable.
# History that shaped them (2026-06-17, CFO caught it): exact-name matching
# found only 62/134 active employees; token-subset finds 101 ('Wangu W. Moses'~
# 'Wangu Moses', 'Refilwe Otukile'~'Caroline Refilwe Otukile'). TD stores
# Windows-SID emails (no real email), so name is the usual join key.
from integrations.td_matching import (  # noqa: E402
    norm_name as _norm_name, name_tokens as _name_tokens, canonical_identity,
)


def _tracked_hours_by_key(months: int):
    """Per Time Doctor user over the last `months`, sum tracked + productive
    hours from stored daily snapshots. Returns (per_user, snap_count); the
    payroll join (token-subset on name) happens in build_reconciliation."""
    from integrations.models import TimeDoctorDailySnapshot
    since = timezone.localtime().date() - timedelta(days=months * 31)
    per_user = {}
    snaps = TimeDoctorDailySnapshot.objects.filter(as_of__gte=since).order_by('as_of')
    snap_count = snaps.count()
    for snap in snaps:
        # Within ONE day, a person running two machines must not have both clocks
        # added together — that is what put the CFO at 21.64 h in a 24-hour day
        # (CFO 2026-07-29). Reduce each day to the person's BUSIEST machine first,
        # then accumulate across days. Historical snapshots still hold one row per
        # machine, so this read-time fold is what corrects the stored history too.
        day_best: dict = {}
        for m in (snap.payload or []):
            email = (m.get('email') or '').strip().lower()
            name = m.get('name') or ''
            c_uid, name = canonical_identity(m.get('user_id'), name)
            uid = c_uid or email or _norm_name(name)
            prev = day_best.get(uid)
            if prev is None or float(m.get('hours_tracked') or 0) > float(prev.get('hours_tracked') or 0):
                day_best[uid] = {**m, 'name': name, 'email': email}

        for uid, m in day_best.items():
            rec = per_user.setdefault(uid, {'name': m['name'], 'email': m['email'],
                                            'role': m.get('role') or '', 'hours': 0.0,
                                            'prod_hours': 0.0, 'unprod_hours': 0.0, 'idle_hours': 0.0,
                                            'productive_pct': m.get('productive_pct')})
            rec['hours'] += float(m.get('hours_tracked') or 0)
            rec['prod_hours'] += float(m.get('productive_hours') or 0)
            # Idle = neutral (unrated) time; unproductive = rated-unproductive.
            # Both as a share of tracked time feed the exception groups.
            rec['unprod_hours'] += float(m.get('unproductive_seconds') or 0) / 3600.0
            rec['idle_hours'] += float(m.get('neutral_seconds') or 0) / 3600.0
    return per_user, snap_count


def _ex_row(r: dict) -> dict:
    return {
        'employee': r['employee'], 'department': r.get('department') or '',
        'tracked_hours': r['tracked_hours'], 'expected_hours': r.get('expected_hours'),
        'utilization_pct': r.get('utilization_pct'),
        'idle_pct': r.get('idle_pct'), 'unproductive_pct': r.get('unproductive_pct'),
    }


def compute_exceptions(rows: list) -> dict:
    """Bucket reconciliation rows into the management exception groups
    (Manus "Daily Time Doctor Exceptions" playbook). A person may appear in more
    than one group; non-trackers are listed first. Pure — same function feeds the
    page and the daily email so they can never diverge.
    """
    tracking = [r for r in rows if r.get('is_tracking')]

    def u(r):
        return r.get('utilization_pct')

    not_tracking = sorted((r for r in rows if not r.get('is_tracking')), key=lambda r: r['employee'])
    critical = [r for r in tracking if u(r) is not None and u(r) < TD_UTIL_CRITICAL]
    warning = [r for r in tracking if u(r) is not None and TD_UTIL_CRITICAL <= u(r) < TD_UTIL_WARN]
    high_idle = [r for r in tracking if (r.get('idle_pct') or 0) >= TD_IDLE_PCT]
    high_unprod = [r for r in tracking if (r.get('unproductive_pct') or 0) >= TD_UNPROD_PCT]

    return {
        'thresholds': {
            'util_critical': TD_UTIL_CRITICAL, 'util_warn': TD_UTIL_WARN,
            'idle_pct': TD_IDLE_PCT, 'unproductive_pct': TD_UNPROD_PCT,
        },
        'groups': {
            'not_tracking':       [_ex_row(r) for r in not_tracking],
            'critical_low_hours': [_ex_row(r) for r in sorted(critical, key=lambda r: u(r))],
            'low_hours_warning':  [_ex_row(r) for r in sorted(warning, key=lambda r: u(r))],
            'high_idle':          [_ex_row(r) for r in sorted(high_idle, key=lambda r: -(r.get('idle_pct') or 0))],
            'high_unproductive':  [_ex_row(r) for r in sorted(high_unprod, key=lambda r: -(r.get('unproductive_pct') or 0))],
        },
        'counts': {
            'not_tracking': len(not_tracking), 'critical_low_hours': len(critical),
            'low_hours_warning': len(warning), 'high_idle': len(high_idle),
            'high_unproductive': len(high_unprod),
        },
    }


def monthly_rollup(months: int = 6) -> list:
    """Monthly Time Doctor trend from stored daily snapshots (CFO 2026-06-19:
    'monthly analysis should be there'). One row per calendar month."""
    from integrations.models import TimeDoctorDailySnapshot
    since = timezone.localtime().date() - timedelta(days=months * 31)
    snaps = TimeDoctorDailySnapshot.objects.filter(as_of__gte=since).order_by('as_of')
    buckets: dict = {}
    for s in snaps:
        key = s.as_of.strftime('%Y-%m')
        t = s.totals or {}
        b = buckets.setdefault(key, {'month': key, 'tracked_hours': 0.0,
                                     'productive_hours': 0.0, 'active_users': 0})
        b['tracked_hours'] += float(t.get('total_hours') or 0)
        b['productive_hours'] += float(t.get('productive_hours') or 0)
        b['active_users'] = max(b['active_users'], int(t.get('active_users') or 0))
    out = []
    for b in buckets.values():
        th = b['tracked_hours']
        b['tracked_hours'] = round(th, 1)
        b['productive_hours'] = round(b['productive_hours'], 1)
        b['productive_pct'] = round(100 * b['productive_hours'] / th, 1) if th else None
        out.append(b)
    return sorted(out, key=lambda x: x['month'])


def by_department(rows: list) -> list:
    """Payroll vs Time Doctor rolled up by department (CFO 2026-06-19)."""
    agg: dict = {}
    for r in rows:
        d = r.get('department') or '—'
        a = agg.setdefault(d, {'department': d, 'headcount': 0, 'tracking': 0,
                               'tracked_hours': 0.0, 'productive_hours': 0.0,
                               'gross_paid': 0.0, 'with_payroll': 0, '_utils': []})
        a['headcount'] += 1
        if r.get('is_tracking'):
            a['tracking'] += 1
            if r.get('utilization_pct') is not None:
                a['_utils'].append(r['utilization_pct'])
        a['tracked_hours'] += r.get('tracked_hours') or 0
        a['productive_hours'] += r.get('productive_hours') or 0
        a['gross_paid'] += r.get('gross_paid') or 0
        if r.get('gross_paid'):
            a['with_payroll'] += 1
    out = []
    for a in agg.values():
        th = a['tracked_hours']
        utils = a.pop('_utils')
        a['tracked_hours'] = round(th, 1)
        a['productive_hours'] = round(a['productive_hours'], 1)
        a['gross_paid'] = round(a['gross_paid'], 2)
        a['cost_per_tracked_hour'] = round(a['gross_paid'] / th, 2) if th else None
        a['utilization_pct'] = round(sum(utils) / len(utils), 1) if utils else None
        out.append(a)
    return sorted(out, key=lambda x: -x['tracked_hours'])


def payroll_coverage(rows: list) -> dict:
    """How much of the tracking workforce can actually be costed against payroll
    (CFO 2026-06-19: flag missing-entity payroll)."""
    tracking = [r for r in rows if r.get('is_tracking')]
    no_gross = [r for r in tracking if not r.get('gross_paid')]
    paid_not_tracking = [r for r in rows if not r.get('is_tracking') and r.get('gross_paid')]
    return {
        'trackers': len(tracking),
        'trackers_without_payroll': len(no_gross),
        'paid_not_tracking': len(paid_not_tracking),
        'pct_costed': round(100 * (len(tracking) - len(no_gross)) / len(tracking), 1) if tracking else None,
    }


def build_reconciliation(months: int = 3) -> dict:
    from payroll.models import Employee, Payslip, PayrollPeriod
    td_users, snap_count = _tracked_hours_by_key(months)

    # recent payroll periods + per-employee gross over the window
    since = timezone.localtime().date() - timedelta(days=months * 31)
    periods = list(PayrollPeriod.objects.filter(end_date__gte=since).order_by('-start_date'))
    period_ids = [p.id for p in periods]
    gross_by_emp, months_by_emp = defaultdict(Decimal), defaultdict(set)
    slips = Payslip.objects.filter(period_id__in=period_ids).select_related('employee', 'period')
    for s in slips:
        if not s.employee_id:
            continue
        gross_by_emp[s.employee_id] += (s.gross_amount or Decimal('0'))
        months_by_emp[s.employee_id].add(s.period_id)

    # Join TD trackers to employees through the ONE matcher (confirmed account
    # link first, then email/name) — never a bespoke name-only join. Ends the
    # name-mismatch miss where a person whose Time Doctor name differs from their
    # HR name showed as untracked on the dashboard / in payroll coverage.
    from integrations.td_matching import TDMatcher
    td_roster = [{'id': uid, 'name': rec.get('name') or '', 'email': rec.get('email') or ''}
                 for uid, rec in td_users.items()]
    active_emps = list(Employee.objects.filter(status='active').order_by('full_name'))
    matcher = TDMatcher(td_roster, active_emps)
    emp_keys = []
    for emp in active_emps:
        uid = matcher.uid_for_employee_id.get(emp.id)
        rec = td_users.get(uid) if uid else None
        emp_keys.append({
            'emp': emp, 'hit': rec is not None,
            'tracked': float(rec.get('hours') or 0.0) if rec else 0.0,
            'prod': float(rec.get('prod_hours') or 0.0) if rec else 0.0,
            'unprod': float(rec.get('unprod_hours') or 0.0) if rec else 0.0,
            'idle': float(rec.get('idle_hours') or 0.0) if rec else 0.0})

    rows, matched = [], 0
    for ek in emp_keys:
        emp = ek['emp']
        tracked, prod = ek['tracked'], ek['prod']
        if ek['hit']:
            matched += 1
        n_months = max(len(months_by_emp.get(emp.id, set())), 0)
        expected = EXPECTED_MONTHLY_HOURS * (n_months or months)
        gross = float(gross_by_emp.get(emp.id, 0) or 0)
        util = round(100 * tracked / expected, 1) if expected else None
        cph = round(gross / tracked, 2) if tracked else None
        idle, unprod = ek['idle'], ek['unprod']
        idle_pct = round(100 * idle / tracked, 1) if tracked else None
        unprod_pct = round(100 * unprod / tracked, 1) if tracked else None
        rows.append({
            'employee': emp.full_name,
            'department': emp.department or '',
            'job_title': emp.job_title or '',
            'tracked_hours': round(tracked, 1),
            'productive_hours': round(prod, 1),
            'expected_hours': round(expected, 0),
            'utilization_pct': util,
            'idle_pct': idle_pct,
            'unproductive_pct': unprod_pct,
            'gross_paid': round(gross, 2),
            'cost_per_tracked_hour': cph,
            'is_tracking': tracked > 0,
            'payroll_months': n_months,
        })

    rows.sort(key=lambda r: r['tracked_hours'], reverse=True)
    tracking = [r for r in rows if r['is_tracking']]
    totals = {
        'months': months,
        'snapshot_days': snap_count,
        'td_users_seen': len(td_users),
        'employees': len(rows),
        'employees_tracking': len(tracking),
        'idle_employees': len(rows) - len(tracking),
        'total_tracked_hours': round(sum(r['tracked_hours'] for r in rows), 1),
        'total_productive_hours': round(sum(r['productive_hours'] for r in rows), 1),
        'total_gross_paid': round(sum(r['gross_paid'] for r in rows), 2),
        'avg_utilization_pct': round(sum(r['utilization_pct'] for r in tracking) / len(tracking), 1) if tracking else None,
        'matched': matched,
    }
    return {'totals': totals, 'rows': rows}


def deepseek_insight(recon: dict) -> dict:
    """Anonymised commentary on the reconciliation. Names are stripped before
    anything leaves the box; the prompt is PII-checked first."""
    from core.ai_assist import reasoning_complete, is_safe_for_ai, DeepSeekUnavailable, GeminiUnavailable
    t = recon.get('totals', {})
    # anonymise: ranked rows as "Employee N", role/dept only, numbers only
    anon = []
    # Per-person GROSS PAY / cost-per-hour is NOT sent to the external AI — for a
    # unique role (e.g. the CFO) that de-anonymises salary, and is_safe_for_ai
    # does not redact plain pay figures (Fable review 2026-07-14). Only
    # utilisation leaves the box per person; pay stays as a company aggregate.
    for i, r in enumerate(recon.get('rows', [])[:40], start=1):
        anon.append(f"#{i} {r.get('department') or 'dept?'}/{r.get('job_title') or 'role?'}: "
                    f"tracked={r['tracked_hours']}h expected={r['expected_hours']}h "
                    f"util={r['utilization_pct']}%")
    summary = (
        f"Workforce time reconciliation (Time Doctor vs payroll), last {t.get('months')} months.\n"
        f"{t.get('employees')} active employees; {t.get('employees_tracking')} have ANY tracked time, "
        f"{t.get('idle_employees')} have none. Total tracked {t.get('total_tracked_hours')}h. "
        f"Avg utilization (of trackers) {t.get('avg_utilization_pct')}%.\n"
        f"Per-employee (anonymised — no names, no pay):\n" + "\n".join(anon)
    )
    prompt = (
        "You are a CFO's analyst. Given this ANONYMISED workforce time-tracking "
        "reconciliation, write 4-6 crisp bullet insights: adoption gap, utilisation outliers, "
        "and one concrete action. Be direct, no preamble. Do not invent names or salaries.\n\n"
        + summary
    )
    rep = is_safe_for_ai(prompt)
    if not getattr(rep, 'safe', True):
        return {'ok': False, 'reason': 'insight skipped: input failed PII safety check', 'text': ''}
    try:
        # send the redacted text (defence in depth) — names are already stripped above
        text = reasoning_complete(rep.redacted_text or prompt, timeout=30.0)
        return {'ok': True, 'text': (text or '').strip(), 'engine': 'deepseek/gemini'}
    except (DeepSeekUnavailable, GeminiUnavailable) as exc:
        return {'ok': False, 'reason': str(exc), 'text': ''}
    except Exception as exc:    # noqa: BLE001
        return {'ok': False, 'reason': f'insight error: {exc}', 'text': ''}
