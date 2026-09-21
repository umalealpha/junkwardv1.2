"""
hris/leave_excuse_service.py

Builds the Leave Excuse Response dashboard for one day (CFO 2026-07-22).

Who appears: tracking-eligible payroll staff (hris.eligibility.tracking_profiles)
whose PRODUCTIVE hours for the day are LOW (< env LEAVE_EXCUSE_LOW_HOURS, default
4.0) or NO (0 / not tracked at all). People who cleared the bar are not listed —
this dashboard is only the people who owe an explanation.

Where the numbers come from: the latest TimeDoctorDailySnapshot for the day
(integrations.models), matched to payroll via THE shared TD↔payroll matcher
(integrations.td_matching — canonical_identity folds a person's multiple machines
onto one identity, then TDMatcher links to the payroll Employee). Their own words
come from a left-join on WorkdayJustification (profile, work_date).

This module is READ-ONLY: it computes + classifies, it deducts nothing and sends
nothing. The auto-email side lives in the rules module + the process_leave_excuses
command, both behind the LEAVE_EXCUSE_AUTOSEND gate.
"""

from __future__ import annotations

import os


def low_hours_threshold() -> float:
    """Productive-hours line below which a tracked day counts as LOW.
    env LEAVE_EXCUSE_LOW_HOURS, default 4.0."""
    try:
        return float(os.environ.get('LEAVE_EXCUSE_LOW_HOURS', '4.0') or 4.0)
    except (TypeError, ValueError):
        return 4.0


def _latest_snapshot_date():
    """The most recent day we have any Time Doctor snapshot for (a date, or None)."""
    from integrations.models import TimeDoctorDailySnapshot
    return (TimeDoctorDailySnapshot.objects
            .order_by('-as_of').values_list('as_of', flat=True).first())


def build_day(date=None) -> dict:
    """Rows for the low/no productive-hours people on `date` (default: the latest
    snapshot day). See module docstring. Returns:

        {date, snapshot, low_hours, rows: [row, ...]}

    row = {employee, employee_id, profile_id, date, productive_hours, band,
           explained, explanation, reason, status, flagged, flag_terms, auto_action}
    status ∈ explained | no explanation | on leave | not accepted | appealed
    """
    from integrations.models import TimeDoctorDailySnapshot
    from integrations.td_matching import TDMatcher, canonical_identity
    from hris import eligibility
    from hris import leave_accountability as la
    from hris import leave_excuse_rules as rules
    from hris.models import WorkdayJustification

    threshold = low_hours_threshold()
    d = date or _latest_snapshot_date()
    if d is None:
        return {'date': None, 'snapshot': False, 'low_hours': threshold, 'rows': []}

    snap = (TimeDoctorDailySnapshot.objects
            .filter(as_of=d).order_by('-updated_at').first())

    # Read Time Doctor's CURRENT view of the day, not the frozen 06:30 snapshot
    # (checklist L27). This feed shows a manager who was short and drives the
    # excuse chase, so a late-synced day must not read as a shortfall. It reads
    # PRODUCTIVE hours, which is why it cannot use hours_for_day.floored — the
    # permanent record holds TRACKED hours and flooring productive with tracked
    # would silently inflate it. One day, one call, snapshot fallback built in.
    # The live Time Doctor read is the only slow part of this function (its
    # client allows 45s per call, three calls deep) and it sits inside a page
    # load, so it is cached for ten minutes — but ONLY it. Caching the whole day
    # froze the dashboard's own Accept/Reject decisions (Fable review
    # 2026-09-09). `d` is already resolved here, so there is no date-less key
    # that could serve yesterday's feed across a day boundary.
    from django.core.cache import cache
    from hris import hours_for_day
    _ck = f'td_live_rows:{d.isoformat()}'
    td_rows = cache.get(_ck)
    if td_rows is None:
        td_rows, _src = hours_for_day.live(d, (snap.payload or []) if snap else [])
        cache.set(_ck, td_rows, 600)

    # Aggregate productive hours per canonical Time Doctor identity (fold a
    # person's multiple machines onto one identity — CFO 2026-07-22).
    agg: dict = {}
    for m in td_rows:
        uid, name = canonical_identity(m.get('user_id'), m.get('name') or '')
        key = str(uid) if uid else ((m.get('email') or '').strip().lower() or name)
        if not key:
            continue
        rec = agg.setdefault(key, {'id': key, 'name': name,
                                   'email': (m.get('email') or '').strip().lower(),
                                   'productive_hours': 0.0, 'hours_tracked': 0.0})
        rec['productive_hours'] += float(m.get('productive_hours') or 0)
        rec['hours_tracked'] += float(m.get('hours_tracked') or 0)

    profs = eligibility.tracking_profiles()
    matcher = TDMatcher(list(agg.values()), [p.employee for p in profs])
    prod_by_emp: dict = {}
    for uid, emp in matcher.employee_for_uid.items():
        rec = agg.get(str(uid)) or agg.get(uid)
        if rec is not None:
            prod_by_emp[emp.id] = rec['productive_hours']

    # One query for the day's justifications, keyed by profile.
    wj_by_prof = {
        w.profile_id: w for w in
        (WorkdayJustification.objects
         .filter(work_date=d, profile__in=profs)
         .select_related('linked_leave'))
    }

    rows = []
    for p in profs:
        emp = p.employee
        prod = prod_by_emp.get(emp.id)          # None = no matched TD account (untracked)
        prod_val = round(float(prod or 0.0), 2)
        is_no = prod is None or prod_val <= 0.0
        is_low = (not is_no) and prod_val < threshold
        if not (is_no or is_low):
            continue                            # cleared the bar — not on this dashboard

        wj = wj_by_prof.get(p.id)
        text = (getattr(wj, 'justification', '') or '')
        reason = (getattr(wj, 'reason', '') or '')
        wj_status = (getattr(wj, 'status', '') or '')
        on_leave = bool(getattr(wj, 'linked_leave_id', None)) or reason == 'on_leave'

        verdict = rules.evaluate(text, reason)
        flags = la.flag_excuse(text)
        reviewed = bool(getattr(wj, 'reviewed_by_id', None))

        if reviewed and wj_status == 'justified' and not on_leave:
            # The CFO/HR accepted this explanation on the dashboard.
            status = 'accepted'
        elif reviewed and wj_status == 'unjustified':
            # The CFO/HR rejected it (see review_note).
            status = 'rejected'
        elif on_leave:
            status = 'on leave'
        elif verdict.rejected:
            status = 'not accepted'
        elif wj is not None and wj_status == 'unjustified' and text:
            # A rejected explanation that no auto-rule caught — on record, contested.
            status = 'appealed'
        elif text or reason not in ('', 'none'):
            status = 'explained'
        else:
            status = 'no explanation'

        rows.append({
            'employee': emp.full_name,
            'employee_id': str(emp.id),
            'emp_id': emp.id,               # internal — popped before returning

            'profile_id': str(p.id),
            'date': d.isoformat(),
            'productive_hours': prod_val,
            'band': 'no' if is_no else 'low',
            'explained': status != 'no explanation',
            'explanation': text,
            'reason': reason,
            'status': status,
            'flagged': bool(flags),
            'flag_terms': flags,
            'auto_action': verdict.label if verdict.rejected else '',
            'decided': reviewed,
            'review_note': (getattr(wj, 'review_note', '') or ''),
        })

    # PEOPLE-DATA GUARDRAIL (CFO 2026-08-01): this feed drives the chaser emails
    # and leave auto-apply, so an unsettled zero (late Time Doctor upload) must
    # never be chased. The row STAYS on the CFO's dashboard — hiding it would
    # read as "all clear" — but carries held=True, and every job that writes to
    # or emails the person skips a held row. Someone with no matched TD account
    # has no data to settle, so the guard can't speak for them; they behave
    # exactly as before.
    held_names: list = []
    uid_by_emp = {emp.id: uid for uid, emp in matcher.employee_for_uid.items()}
    zero_pairs = [(uid_by_emp[r['emp_id']], r['employee'])
                  for r in rows if r['band'] == 'no' and r['emp_id'] in uid_by_emp
                  and not r['explained']]
    held_emp_ids: set = set()
    if zero_pairs:
        from hris import people_data_guard as pdg
        held = pdg.held_uids(zero_pairs, d)
        if held:
            held_emp_ids = {e for e, u in uid_by_emp.items() if u in held}
            held_names = sorted(r['employee'] for r in rows if r['emp_id'] in held_emp_ids)
    for r in rows:
        r['held'] = r.pop('emp_id', None) in held_emp_ids
        if r['held']:
            r['status'] = 'hours still arriving'

    # "Not accepted" first, then lowest productive hours first — worst at the top.
    rows.sort(key=lambda r: (r['status'] != 'not accepted', r['productive_hours']))
    return {'date': d.isoformat(), 'snapshot': bool(snap),
            'low_hours': threshold, 'rows': rows,
            'held_names': held_names, 'held_count': len(held_names)}
