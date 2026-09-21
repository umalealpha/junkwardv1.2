"""
hris/exceptions_report.py

The manager "Time Doctor Daily Exceptions" report (CFO 2026-07-14) — the email
that used to go out via Manus, rebuilt in omni as rich HTML for the manager
group. Aggregates only (no window/app titles, AD-POL-AI-GOV-001).

Sections: management summary · did-not-track (payroll-flagged) · 3+ consecutive
no-track alarm · critical/low/below-6h buckets · late starters · high
unproductive · payroll ghost list. Idle% is omitted (not in the worklog feed).

Identity layer (Fable review 2026-07-14): TD-ACCOUNT-CENTRIC via the ONE shared
matcher (integrations.td_matching). Only payroll staff holding a matched Time
Doctor account are reported; "did not track" = matched account + zero hours,
NOTHING else. Payroll staff with no TD account are the informational ghost
list, never conflated with did-not-track. A circuit breaker stops the send
entirely when the numbers look like an outage / expired key / matching bug.
"""
from __future__ import annotations

import datetime
import html as _html
import logging
from decimal import Decimal

from integrations.timedoctor import aggregate
from integrations.td_matching import TDMatcher, active_td_users, collapse_users, fold_uid
from hris import workforce_pulse

NAVY, ORANGE, INK, MUT = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280'
# Buckets in the "unexplained" block that are NOT a manager's action item: nobody
# is assigned, or the person is an executive (never filed under a staff manager —
# see _unexplained_by_manager).
NO_MANAGER_KEY = 'No manager on file'
# The CEO does not report to an employee (CFO 2026-07-30: "no ceo dosnt report to
# unami, just say dear board of Alpha Direct for ceo to make it fun"). Executives
# are addressed to the BOARD, which is who they actually answer to.
EXEC_KEY = 'Dear Board of Alpha Direct'
NON_MANAGER_KEYS = (NO_MANAGER_KEY, EXEC_KEY)
RED, AMBER, GREEN = '#DC2626', '#B45309', '#059669'
LOCAL_OFFSET = datetime.timedelta(hours=2)   # Botswana / SAST = UTC+2


def _leaderboard_always_show():
    """Departments pinned onto the leaderboard even when below the 3-tracker
    hide threshold (CFO 2026-07-23 — keep the Executive / C-Suite team visible).
    Override with settings.WORKFORCE_LEADERBOARD_ALWAYS_SHOW."""
    from django.conf import settings
    return getattr(settings, 'WORKFORCE_LEADERBOARD_ALWAYS_SHOW', ['Executive', 'C-Suite', 'Exco'])


def day_window_utc(day: datetime.date):
    """The UTC datetime window covering the BOTSWANA calendar day `day`.
    Requesting plain UTC dates shifted evening work into the next day and
    produced false under-6h flags (Fable review)."""
    start = datetime.datetime.combine(day, datetime.time(0)) - LOCAL_OFFSET
    return start, start + datetime.timedelta(days=1)


def circuit_breaker(summary: dict, max_dnt_pct: float = None):
    """(tripped, reason). Refuses to publish a report whose shape screams
    'the data layer broke' — TD outage, expired token, or a matching collapse —
    instead of blasting false 'did not track' accusations to managers.

    The did-not-track threshold is deliberately OUTAGE-level (default 60%), not a
    normal-absence level: on any ordinary day some matched staff genuinely don't
    track, and flagging them is the whole point — so only a collapse (most of the
    matched roster silent at once) should suppress the send (Fable review
    2026-07-14). Tune WORKFORCE_BREAKER_DNT_PCT from the observed dry-run."""
    from django.conf import settings
    if max_dnt_pct is None:
        max_dnt_pct = float(getattr(settings, 'WORKFORCE_BREAKER_DNT_PCT', 60.0))
    roster = summary.get('roster') or 0
    tracked = summary.get('tracked') or 0
    dnt = summary.get('did_not_track') or 0
    if roster == 0:
        return True, 'matched roster is EMPTY — Time Doctor returned no usable users or matching collapsed'
    if tracked == 0:
        return True, 'ZERO tracked employees — likely a Time Doctor outage or an expired token'
    pct = 100.0 * dnt / roster
    if pct > max_dnt_pct:
        return True, (f'did-not-track = {dnt} of {roster} matched staff ({pct:.0f}%) exceeds the '
                      f'{max_dnt_pct:.0f}% sanity threshold — data looks wrong, not people')
    return False, ''


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.strptime(str(ts)[:19], '%Y-%m-%dT%H:%M:%S')
    except Exception:    # noqa: BLE001
        return None


def _hm(dt):
    """Format a UTC datetime as local HH:MM (Botswana)."""
    if not dt:
        return '—'
    return (dt + LOCAL_OFFSET).strftime('%H:%M')


def per_user_day(users, worklog):
    """{uid: {name,email,sec,manual_sec,devices,start,end}} with first-start /
    last-end (UTC).

    `sec` is OBSERVED time on the person's BUSIEST DEVICE — not the sum of their
    devices, and not including manually-entered rows (CFO 2026-07-29).

    Both of those used to be added in, and the result went out to all 79 staff:
    the CFO's 28 July read 21.64 h in a 24-hour day (desktop 9.02 h observed +
    4.70 h typed in manually, plus laptop 7.92 h observed). Time Doctor bills per
    device and these rows carry no cross-device overlap information, so two
    machines running through the same working day cannot be de-duplicated — the
    only defensible figure is the device the person actually worked on most.
    Manual rows are kept separately in `manual_sec` so they can still be shown.

    `start`/`end` still span every device: arrival and departure are about when
    the person was at work, and the earliest start is the earliest start whichever
    machine saw it.
    """
    out = {}
    for u in users:
        if u.get('id'):
            out[u['id']] = {'name': u.get('name') or '', 'email': u.get('email') or '',
                            'sec': 0, 'manual_sec': 0, 'devices': 0,
                            'start': None, 'end': None}
    # Pass 1 — accumulate per RAW device id, keeping observed and manual apart.
    per_device: dict = {}
    for bucket in (worklog or []):
        rows = bucket if isinstance(bucket, list) else [bucket]
        for r in rows:
            if not isinstance(r, dict):
                continue
            raw_uid = r.get('userId')
            if raw_uid is None:
                continue
            uid = fold_uid(raw_uid)          # the person this device belongs to
            t = int(r.get('time') or 0)
            is_manual = str(r.get('mode') or '').strip().lower() == 'manual'
            d = per_device.setdefault((uid, raw_uid), {'sec': 0, 'manual_sec': 0})
            if is_manual:
                d['manual_sec'] += t
            else:
                d['sec'] += t

            rec = out.setdefault(uid, {'name': '', 'email': '', 'sec': 0, 'manual_sec': 0,
                                       'devices': 0, 'start': None, 'end': None})
            st = _parse(r.get('start'))
            if st:
                if rec['start'] is None or st < rec['start']:
                    rec['start'] = st
                en = st + datetime.timedelta(seconds=t)
                if rec['end'] is None or en > rec['end']:
                    rec['end'] = en

    # Pass 2 — each person keeps their busiest device's observed time.
    for (uid, _raw), d in per_device.items():
        rec = out.setdefault(uid, {'name': '', 'email': '', 'sec': 0, 'manual_sec': 0,
                                   'devices': 0, 'start': None, 'end': None})
        rec['devices'] = (rec.get('devices') or 0) + 1
        if d['sec'] > rec['sec']:
            rec['sec'] = d['sec']
            rec['manual_sec'] = d['manual_sec']
    return out


# CFO 2026-08-27: ONE company start time. Omni had two — the hours reminder
# chased anyone starting after 08:15 while this report only counted them late
# after 09:00, so a person arriving 08:45 was chased but never appeared as late.
# The CFO picked 08:15 (his stated start time). Every late-arrival check in the
# company reads THIS constant — do not re-introduce a second literal.
LATE_START_AFTER = datetime.time(8, 15)


def late_days_by_user(worklog, after=LATE_START_AFTER) -> dict:
    """{userId: number of days whose FIRST tracked start (Botswana local) was
    after `after`} over a worklog window. Powers the punctuality note + the
    latecomer dashboard. Aggregates only — no window/app content."""
    firsts = {}   # (uid, local_date) -> earliest local start
    for bucket in (worklog or []):
        rows = bucket if isinstance(bucket, list) else [bucket]
        for r in rows:
            if not isinstance(r, dict):
                continue
            uid = fold_uid(r.get('userId'))
            st = _parse(r.get('start'))
            if uid is None or not st:
                continue
            local = st + LOCAL_OFFSET
            key = (uid, local.date())
            if key not in firsts or local < firsts[key]:
                firsts[key] = local
    counts = {}
    for (uid, _d), first in firsts.items():
        if first.time() > after:
            counts[uid] = counts.get(uid, 0) + 1
    return counts


def tracked_seconds_by_user_day(worklog) -> dict:
    """{(userId, Botswana local date): tracked seconds} — per-day totals over a
    window. Used by the offboarding check to see which working days were zero."""
    out = {}
    for bucket in (worklog or []):
        rows = bucket if isinstance(bucket, list) else [bucket]
        for r in rows:
            if not isinstance(r, dict):
                continue
            uid = fold_uid(r.get('userId'))
            st = _parse(r.get('start'))
            if uid is None or not st:
                continue
            key = (uid, (st + LOCAL_OFFSET).date())
            out[key] = out.get(key, 0) + int(r.get('time') or 0)
    return out


def _hours(sec):
    return round((sec or 0) / 3600, 2)


def _roster_and_matcher(client, persist_map=False):
    """One TD roster pull + ONE matcher against the tracking-eligible payroll
    staff. Returns (users, ids, matcher, profiles)."""
    from hris import eligibility
    users_all = active_td_users(client.users())
    ids = [u.get('id') for u in users_all if u.get('id')]   # ALL machines — the API pull needs every id
    users = collapse_users(users_all)                        # but match/seed on ONE row per person
    profiles = eligibility.tracking_profiles()
    matcher = TDMatcher(users, [p.employee for p in profiles])
    if persist_map:
        matcher.persist_suggestions()
    return users, ids, matcher, profiles


def _split_matched(matcher, day_map, on_leave):
    """The TD-account-centric split: matched staff → tracked / did-not-track /
    on-leave; unmatched staff → ghost list (informational only)."""
    tracked_rows, dnt, on_leave_list = [], [], []
    for uid, emp in matcher.employee_for_uid.items():
        nm = (getattr(emp, 'full_name', '') or '').strip()
        if not nm:
            continue
        if nm.lower() in on_leave:
            on_leave_list.append(nm)
            continue
        rec = day_map.get(uid) or {'sec': 0, 'start': None, 'end': None}
        if rec['sec'] > 0:
            tracked_rows.append({'name': nm, 'uid': uid, 'sec': rec['sec'],
                                 'start': rec['start'], 'end': rec['end']})
        else:
            dnt.append(nm)
    ghosts = sorted({(getattr(e, 'full_name', '') or '').strip()
                     for e in matcher.unmatched_employees
                     if (getattr(e, 'full_name', '') or '').strip()
                     and (getattr(e, 'full_name', '') or '').strip().lower() not in on_leave})
    return tracked_rows, sorted(dnt), sorted(on_leave_list), ghosts


def _summary(tracked_rows, dnt, matched_count, prod_by_uid=None):
    total_sec = sum(r['sec'] for r in tracked_rows)
    prod_h = None
    if prod_by_uid is not None:
        vals = [prod_by_uid.get(r['uid']) for r in tracked_rows]
        prod_h = round(sum(v for v in vals if v is not None), 1)
    return {'tracked': len(tracked_rows), 'did_not_track': len(dnt),
            'roster': matched_count, 'total_h': _hours(total_sec),
            'avg_h': _hours(total_sec / len(tracked_rows)) if tracked_rows else 0,
            'prod_h': prod_h}


def planned_for_day(day):
    """Employees who self-reported in advance they'd be out/short this day — an
    EXPLAINED WorkdayJustification they filed (pending the manager review).
    Returns {employee_id(str): "Name — reason[: note]"}.

    Keyed by employee id, NOT name: the roster has duplicate full names, so a
    name key would collide (lose a row) and over-exclude a same-named no-show.
    Approved-leave (JUSTIFIED) days are intentionally NOT here — they're governed
    live by eligibility.on_leave_names, so a later-cancelled leave drops the
    person correctly. (CFO 2026-07-15 — planned out-of-office, e.g. golf day.)"""
    from hris.models import WorkdayJustification
    labels = dict(WorkdayJustification.Reason.choices)
    out = {}
    qs = (WorkdayJustification.objects
          .filter(work_date=day, responded_at__isnull=False,
                  status=WorkdayJustification.Status.EXPLAINED)
          .exclude(reason=WorkdayJustification.Reason.NONE)
          .select_related('profile__employee'))
    for r in qs:
        emp = r.profile.employee
        nm = (getattr(emp, 'full_name', '') or '').strip()
        if not nm:
            continue
        note = (r.justification or '').strip()
        label = labels.get(r.reason, r.reason)
        out[str(emp.id)] = f'{nm} — {label}' + (f': {note}' if note else '')
    return out


def _daily_targets(matcher, day, off_dates=()):
    """{td_uid: required productive hours for `day`} — per person, because
    managers / EXCO / FM carry the lighter 4.5h weekday rate (CFO 2026-07-23).
    Judging everyone against the 6.5h staff day would name managers for working
    their own full day."""
    from hris import workforce
    from hris.workforce_roles import is_manager_hours_employee
    out = {}
    for uid, emp in matcher.employee_for_uid.items():
        try:
            t = float(workforce.required_hours_for_date(
                day, off_dates, is_manager=is_manager_hours_employee(emp)))
        except Exception:    # noqa: BLE001
            t = 6.5
        if t > 0:
            out[uid] = t
    return out


def _shortfall_streaks(client, users, ids, matcher, day, candidates, targets, *, back=4):
    """{uid: consecutive days (including `day`) short of their own productive
    target} for the uids in `candidates`.

    A single bad day is noise; four in a row is a pattern, and the pattern is what
    a manager has to act on. Walks back one day at a time and stops as soon as no
    candidate is still on a streak, so a clean team costs ONE extra pull."""
    streak = {uid: 1 for uid in candidates}
    live = set(candidates)
    for k in range(1, back + 1):
        if not live:
            break
        pday = day - datetime.timedelta(days=k)
        try:
            from hris import workforce
            if float(workforce.required_hours_for_date(pday)) <= 0:
                continue                      # Sunday / holiday — not a working day
            pf, pt = day_window_utc(pday)
            pwl = client.worklog(pf, pt, user_ids=ids)
            ptu = client.timeuse(pf, pt, user_ids=ids)
            pagg = aggregate(users, pwl, ptu, [], [], as_of=pday, td_user_ids=ids)
            pprod = {m['user_id']: (m.get('productive_hours') or 0) for m in pagg['members']}
        except Exception:    # noqa: BLE001
            break                             # tolerate a bad pull — never block the report
        still = set()
        for uid in live:
            target = targets.get(uid)
            if target and float(pprod.get(uid, 0)) < float(target):
                streak[uid] += 1
                still.add(uid)
        live = still
    return streak


def _dark_streaks(client, users, ids, candidates, day, *, back=6):
    """({uid: consecutive dark days incl. `day`}, {uid: last day they DID track}).

    Walks back a day at a time and stops as soon as nobody is still dark, so one
    silent day costs one extra pull. Rest days are skipped, not counted as dark —
    a Sunday must never inflate somebody's streak into an accusation."""
    from hris import workforce
    streak = {uid: 1 for uid in candidates}
    last = {}
    live = set(candidates)
    for k in range(1, back + 1):
        if not live:
            break
        pday = day - datetime.timedelta(days=k)
        try:
            if float(workforce.required_hours_for_date(pday)) <= 0:
                continue
            pf, pt = day_window_utc(pday)
            pm = per_user_day(users, client.worklog(pf, pt, user_ids=ids))
        except Exception:    # noqa: BLE001
            break
        still = set()
        for uid in live:
            if (pm.get(uid) or {'sec': 0})['sec'] == 0:
                streak[uid] += 1
                still.add(uid)
            elif uid not in last:
                last[uid] = pday
        live = still
    return streak, last


def _unexplained_by_manager(matcher, names, streaks, last_tracked):
    """Who was absent from Time Doctor with NO leave applied and NO absence note,
    grouped under the MANAGER who owes the answer (CFO 2026-07-30 — "you have the
    manager and the department, ask the manager why … we hold people accountable").

    `names` is the already-filtered did-not-track list, so approved leave and
    self-reported/planned days are out by construction — everyone here is genuinely
    unexplained. Each row carries the person's DEPARTMENT so the manager cannot
    claim it is somebody else's team, plus days dark and when they last tracked.

    Reports whose employee record has NO manager come back under the
    'No manager on file' key — an accountability hole is itself a finding for HR,
    so it is shown rather than silently dropped.

    C-SUITE / EXCO ARE NEVER PUT UNDER A MANAGER. The first live run of this block
    filed the CEO under Unami Butale as "1 to answer for" — telling the Chief Human
    Capital Officer to explain the CEO's tracking. That is exactly the false
    escalation that got the accountability crons paused on 28 July ("CEO never
    clocks"), and the CEO does not report to an employee anyway. Executives are
    addressed to the BOARD instead (CFO 2026-07-30), which is who they answer to —
    so nothing is hidden and nobody is asked to discipline their own boss."""
    from django.conf import settings
    from hris.models import HRISProfile

    exec_depts = {d.strip().lower() for d in
                  getattr(settings, 'WORKFORCE_LEADERBOARD_ALWAYS_SHOW', ['Executive', 'C-Suite', 'Exco'])
                  if (d or '').strip()}
    wanted = {}
    for uid, emp in matcher.employee_for_uid.items():
        nm = (getattr(emp, 'full_name', '') or '').strip()
        if nm and nm in set(names):
            wanted.setdefault(nm, []).append((uid, emp))

    emps = [e for rows in wanted.values() for _uid, e in rows]
    mgr_by_emp = {}
    for prof in (HRISProfile.objects.filter(employee__in=emps)
                 .select_related('manager', 'employee')):
        mgr = getattr(prof, 'manager', None)
        mgr_by_emp[prof.employee_id] = (getattr(mgr, 'full_name', '') or '').strip() or ''

    groups: dict = {}
    for nm, rows in wanted.items():
        for uid, emp in rows:
            dept = (getattr(emp, 'department', '') or '').strip() or 'no department set'
            mgr_name = mgr_by_emp.get(getattr(emp, 'id', None)) or ''
            last = last_tracked.get(uid)
            if dept.lower() in exec_depts:
                key = EXEC_KEY                      # never under a staff manager
            else:
                key = mgr_name or NO_MANAGER_KEY
            groups.setdefault(key, []).append({
                'name': nm,
                'dept': dept,
                'days_dark': int(streaks.get(uid) or 1),
                'last_tracked': last.strftime('%a %d %b') if last else None,
            })
    for rows in groups.values():
        rows.sort(key=lambda r: (-r['days_dark'], r['name']))
    # Real managers with the worst problem first; the unowned and executive
    # buckets always sit last — neither is a manager's action item.
    return dict(sorted(groups.items(),
                       key=lambda kv: (kv[0] in NON_MANAGER_KEYS,
                                       -max(r['days_dark'] for r in kv[1]),
                                       -len(kv[1]), kv[0])))


def _guard_history_and_samples(client, day, back=7):
    """Inputs for the people-data guardrail (CFO 2026-08-01):
      samples  — the same day's multi-pull settle samples (03:00/04:00/08:30),
                 {slot: {uid: tracked_sec}}, from today's snapshot.
      history  — per-uid recent tracked seconds {uid: {iso_date: sec}} from the
                 last `back` days' snapshots, so a lone zero can be told apart
                 from a normal absence.
    Tolerant: any read problem returns ({}, {}) — the guard then relies on the
    single-pull fallback + AI, never crashes the report."""
    from integrations.models import TimeDoctorDailySnapshot
    samples, hist = {}, {}
    try:
        snap = TimeDoctorDailySnapshot.objects.filter(
            company_id=client.company_id, as_of=day).first()
        if snap:
            samples = dict(snap.settle_samples or {})
        prior = (TimeDoctorDailySnapshot.objects
                 .filter(company_id=client.company_id, as_of__lt=day,
                         as_of__gte=day - datetime.timedelta(days=back))
                 .order_by('as_of'))
        for s in prior:
            iso = s.as_of.isoformat()
            for m in (s.payload or []):
                uid = m.get('user_id')
                if uid:
                    hist.setdefault(uid, {})[iso] = int(round((m.get('hours_tracked') or 0) * 3600))
    except Exception:    # noqa: BLE001
        return {}, {}
    return samples, hist


def persist_reported_no_track(client, day, uids):
    """Record the td_user_ids we PUBLISHED as did-not-track for `day`, so the next
    morning can check whether any of their hours arrived late (CFO 2026-08-01)."""
    from integrations.models import TimeDoctorDailySnapshot
    try:
        snap, _ = TimeDoctorDailySnapshot.objects.get_or_create(
            company_id=client.company_id, as_of=day)
        snap.reported_no_track = sorted({u for u in (uids or []) if u})
        snap.save(update_fields=['reported_no_track', 'updated_at'])
    except Exception:    # noqa: BLE001 — a correction-log write must never break the send
        pass


def late_corrections(client, day):
    """People PUBLISHED as 'did not track' on `day` whose hours have since arrived
    (a late/stuck Time Doctor upload). Returns [{'name','hours'}], read-only. This
    is the backstop for data that lands AFTER the 09:00 report — the guardrail
    catches the rest before the send."""
    from integrations.models import TimeDoctorDailySnapshot
    snap = TimeDoctorDailySnapshot.objects.filter(
        company_id=client.company_id, as_of=day).first()
    reported = set((snap.reported_no_track or []) if snap else [])
    if not reported:
        return []
    users, ids, matcher, _ = _roster_and_matcher(client, persist_map=False)
    w_from, w_to = day_window_utc(day)
    day_map = per_user_day(users, client.worklog(w_from, w_to, user_ids=ids))
    out = []
    for uid in reported:
        emp = matcher.employee_for_uid.get(uid)
        if not emp:
            continue
        sec = (day_map.get(uid) or {'sec': 0}).get('sec', 0)
        if sec > 0:
            out.append({'name': (getattr(emp, 'full_name', '') or '').strip(),
                        'hours': round(sec / 3600, 1)})
    return sorted(out, key=lambda r: r['name'])


def build_correction_html(day, rows):
    """Small navy/orange correction note listing people whose hours arrived late."""
    items = ''.join(
        f'<tr><td style="padding:8px 12px;border-bottom:1px solid #EEF1F5">'
        f'<b>{_html.escape(r["name"])}</b></td>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #EEF1F5;color:{GREEN};font-weight:700">'
        f'{r["hours"]}h — did track</td></tr>'
        for r in rows)
    return (
        f'<div style="font-family:Book Antiqua,Georgia,serif;max-width:640px;margin:0 auto;color:{INK}">'
        f'<div style="background:{NAVY};color:#fff;padding:16px 20px;border-radius:12px 12px 0 0">'
        f'<div style="font-size:18px;font-weight:800">✅ Correction — hours arrived late</div>'
        f'<div style="font-size:13px;opacity:.85;margin-top:4px">{day.strftime("%A %d %B %Y")}</div></div>'
        f'<div style="border:1px solid #EAEEF3;border-top:0;border-radius:0 0 12px 12px;padding:16px 20px">'
        f'<p style="font-size:14px;margin:0 0 12px">The people below were listed as '
        f'<b>did not track</b> in that day\'s report. Their Time Doctor hours have since '
        f'arrived — please <b>disregard the earlier flag</b> for them.</p>'
        f'<table role="presentation" style="width:100%;border-collapse:collapse">{items}</table>'
        f'<p style="font-size:12px;color:{MUT};margin:14px 0 0">Automatic correction — omni '
        f'people-data guardrail. Time Doctor sometimes uploads a machine\'s hours late.</p>'
        f'</div></div>')


def compute(client, day, persist_map=False, off_dates=()):
    """Build every section from live Time Doctor data for the Botswana day."""
    users, ids, matcher, _ = _roster_and_matcher(client, persist_map)
    w_from, w_to = day_window_utc(day)
    wl = client.worklog(w_from, w_to, user_ids=ids)
    tu = client.timeuse(w_from, w_to, user_ids=ids)

    day_map = per_user_day(users, wl)
    agg = aggregate(users, wl, tu, [], [], as_of=day, td_user_ids=ids)
    prod_by_uid = {m['user_id']: m.get('productive_hours') for m in agg['members']}

    from hris import eligibility
    on_leave = eligibility.on_leave_names(day)
    planned = planned_for_day(day)            # {employee_id: "Name — reason"}
    planned_ids = set(planned)
    tracked, dnt_raw, on_leave_list, ghosts = _split_matched(matcher, day_map, on_leave)
    # Displayed no-show list drops people who told us in advance — matched by
    # EMPLOYEE ID, not name (the roster has duplicate full names). The 3+-day
    # ALARM below is seeded from dnt_raw, so a same-day self-report can never
    # mute the streak escalation.
    # A LIST, not a set — two different employees who share a full name and both
    # fail to track must BOTH count (a set would collapse them to one and hide a
    # real no-show). Planned people are dropped by employee id (dup-name-safe).
    # Raw no-track candidates, kept as (uid, name) PAIRS so the guardrail can
    # drop a held person by their stable TD id — never by name (dup full names).
    dnt_pairs = [
        (uid, (getattr(emp, 'full_name', '') or '').strip())
        for uid, emp in matcher.employee_for_uid.items()
        if (getattr(emp, 'full_name', '') or '').strip()
        and (day_map.get(uid) or {'sec': 0}).get('sec', 0) == 0
        and (getattr(emp, 'full_name', '') or '').strip().lower() not in on_leave
        and str(getattr(emp, 'id', '')) not in planned_ids
    ]

    # PEOPLE-DATA GUARDRAIL (CFO 2026-08-01): a no-track is only published once it
    # has SETTLED across the day's pulls and two AI engines agree it's a genuine
    # absence, not a late/stuck Time Doctor upload. Held names are dropped ENTIRELY
    # — from this list, the 3-day alarm, and the manager-answer section (CFO chose
    # "hold the name completely"). Fail-safe: anything unproven is HELD, never named.
    from django.conf import settings as _st
    held_uids: set = set()
    held_names: list = []
    held_ai_down = 0
    if getattr(_st, 'WORKFORCE_DATA_GUARD_ENABLED', True) and dnt_pairs:
        from hris import people_data_guard as pdg
        samples, hist = _guard_history_and_samples(client, day)
        cands = [{'uid': u, 'name': n, 'is_zero': True} for u, n in dnt_pairs]
        try:
            decisions = pdg.guard_candidates(
                cands, samples, hist, day,
                use_ai=getattr(_st, 'WORKFORCE_DATA_GUARD_AI', True))
        except Exception:    # noqa: BLE001 — AI-path bug must not blank the report
            decisions = pdg.guard_candidates(cands, samples, hist, day, use_ai=False)
        held_uids = {u for u, d in decisions.items() if d.get('action') == 'hold'}
        held_names = sorted(d['name'] for d in decisions.values() if d.get('action') == 'hold')
        # How many were held because the AI gate could not run (vs a real settle
        # hold). A high count with the AI down means the guard is silently
        # suppressing the whole report — surfaced so a dead gateway can't read as
        # "all clear" (Fable review 2026-08-01, H6).
        held_ai_down = sum(1 for d in decisions.values() if d.get('action') == 'hold'
                           and 'unavailable' in (d.get('reason') or ''))

    dnt_kept = [(u, n) for u, n in dnt_pairs if u not in held_uids]
    did_not_track = sorted(n for _u, n in dnt_kept)

    def bucket(lo, hi):
        out = [(r['name'], r['sec'], prod_by_uid.get(r['uid'])) for r in tracked if lo <= _hours(r['sec']) < hi]
        return sorted(out, key=lambda x: x[1])

    # 3+ consecutive no-track — matched accounts only, tracked by stable TD id.
    alarm = []
    try:
        dnt_names = set(dnt_raw)
        zero_uids = {uid for uid, emp in matcher.employee_for_uid.items()
                     if (getattr(emp, 'full_name', '') or '').strip() in dnt_names
                     and uid not in held_uids}   # guardrail: never alarm a held (likely-late) name
        for k in (1, 2):
            if not zero_uids:
                break
            pday = day - datetime.timedelta(days=k)
            pf, pt = day_window_utc(pday)
            pm = per_user_day(users, client.worklog(pf, pt, user_ids=ids))
            zero_uids = {uid for uid in zero_uids if (pm.get(uid) or {'sec': 0})['sec'] == 0}
        alarm = sorted((getattr(matcher.employee_for_uid[uid], 'full_name', '') or '').strip()
                       for uid in zero_uids)
    except Exception:    # noqa: BLE001
        alarm = []

    late = sorted([(r['name'], _hm(r['start'])) for r in tracked
                   if r['start'] and (r['start'] + LOCAL_OFFSET).time() > LATE_START_AFTER],
                  key=lambda x: x[1])
    emp_name_by_uid = {uid: (getattr(emp, 'full_name', '') or '').strip()
                       for uid, emp in matcher.employee_for_uid.items()}
    unproductive = sorted(
        [(emp_name_by_uid[m['user_id']], m.get('unproductive_seconds', 0)) for m in agg['members']
         if m.get('user_id') in emp_name_by_uid
         and (m.get('unproductive_seconds') or 0) > 300
         and emp_name_by_uid[m['user_id']].lower() not in on_leave],
        key=lambda x: x[1], reverse=True)[:10]

    # --- Team pulse (momentum · leaderboard · focus) — CFO 2026-07-16 ----------
    name_by_uid = {uid: (getattr(e, 'full_name', '') or '').strip()
                   for uid, e in matcher.employee_for_uid.items()}
    dept_by_uid = {uid: (getattr(e, 'department', '') or '').strip()
                   for uid, e in matcher.employee_for_uid.items()}
    matched_uids = list(matcher.employee_for_uid.keys())
    secs_by_uid = {uid: (day_map.get(uid) or {'sec': 0})['sec'] for uid in matched_uids}
    leaderboard, lb_hidden = workforce_pulse.team_leaderboard(
        matched_uids, secs_by_uid, prod_by_uid, dept_by_uid,
        always_show=_leaderboard_always_show())
    # Focus runs off TIMEUSE (productive rows only), not the worklog — see
    # workforce_pulse.focus_stats. `ids` is the request order timeuse is keyed by.
    prod_secs_by_uid = {uid: float(h or 0) * 3600 for uid, h in prod_by_uid.items()}
    focus = workforce_pulse.focus_stats(tu, name_by_uid, matched_uids, ids,
                                        prod_secs_by_uid=prod_secs_by_uid)

    # Short of their own target, worst first + how many days in a row (CFO
    # 2026-07-30). Only people who actually tracked — no-shows are named already.
    targets = _daily_targets(matcher, day, off_dates)
    short_uids = [uid for uid in matched_uids
                  if secs_by_uid.get(uid, 0) > 0
                  and targets.get(uid)
                  and float(prod_by_uid.get(uid) or 0) < float(targets[uid])
                  and (name_by_uid.get(uid) or '').lower() not in on_leave]
    streaks = _shortfall_streaks(client, users, ids, matcher, day, short_uids, targets) \
        if short_uids else {}
    shortfall = workforce_pulse.shortfall_board(
        short_uids, prod_by_uid, name_by_uid, targets, streaks=streaks)

    # Unexplained absence, grouped under the manager who owes the answer, with
    # each person's department next to their name (CFO 2026-07-30).
    dark_uids = [u for u, _n in dnt_kept]   # guardrail-filtered; held names never escalate
    dark_streaks, last_tracked = (_dark_streaks(client, users, ids, dark_uids, day)
                                  if dark_uids else ({}, {}))
    unexplained = _unexplained_by_manager(matcher, did_not_track, dark_streaks, last_tracked)
    # Momentum vs the SAME weekday last week (one extra worklog pull; tolerant).
    prev_secs = {}
    try:
        lf, lt = day_window_utc(day - datetime.timedelta(days=7))
        lw_map = per_user_day(users, client.worklog(lf, lt, user_ids=ids))
        prev_secs = {uid: (lw_map.get(uid) or {'sec': 0})['sec'] for uid in matched_uids}
    except Exception:    # noqa: BLE001
        prev_secs = {}
    mo = workforce_pulse.momentum(secs_by_uid, prev_secs, name_by_uid)
    mo['compare_label'] = 'vs same day last week'

    # THE MIRROR GATE (TD-ORPHAN-01, bug 5dffc022): every gate above asks "are we
    # about to accuse someone unfairly?" and so fails safe by going QUIET. That
    # cannot catch hours landing on an account nobody owns — 18.29 h vanished on
    # 2-Sep and only the person it happened to ever noticed. This reconciles the
    # other direction, hours -> people, and fails LOUD.
    orphans = []
    orphans_ai_down = False
    try:
        from django.conf import settings as _st_orph

        from hris import people_data_guard as _pdg
        # agg['members'] is post-fold, so an account that DID fold onto its owner
        # is correctly no longer an orphan; one that could not fold stays and
        # needs a human.
        # Ownership is a map row pointing at an employee — NOT this report's
        # tracking-eligible scope. Building it from employee_for_uid alone cried
        # wolf on five people who hold a good confirmed link.
        owned = _pdg.owned_td_uids(
            extra={str(u) for u in matcher.employee_for_uid}
            | {str(fold_uid(u)) for u in matcher.employee_for_uid})
        orphans = _pdg.orphan_hours_gate(agg['members'], owned)
        if orphans:
            # The roster MUST include people who already track — that omission is
            # exactly what let her orphan account go unexamined.
            # Matched employees FIRST, then the ghosts. ghost_payroll's screen
            # offered only the ghosts, which is why an orphan account belonging
            # to someone who already tracks was never compared against them.
            roster = sorted({
                (getattr(e, 'full_name', '') or '').strip()
                for e in (list(matcher.employee_for_uid.values())
                          + list(getattr(matcher, 'unmatched_employees', [])))
                if (getattr(e, 'full_name', '') or '').strip()})
            screened = _pdg.screen_orphan_hours(
                orphans, roster, day,
                use_ai=getattr(_st_orph, 'WORKFORCE_DATA_GUARD_AI', True))
            orphans, orphans_ai_down = screened['raise'], screened['ai_unavailable']
    except Exception:      # noqa: BLE001 — a guard bug must not kill the report,
        # but it must not silently clear the alarm either: log it loudly.
        log.exception('orphan-hours gate failed for %s — lost hours may be '
                      'going unreported', day)

    return {
        'day': day,
        # Unowned Time Doctor hours: real work credited to nobody. Named, always.
        'orphan_hours': orphans,
        'orphan_hours_total': round(sum(o['hours'] for o in orphans), 2),
        'orphan_hours_ai_down': orphans_ai_down,
        'summary': _summary(tracked, did_not_track, len(matcher.employee_for_uid) - len(on_leave_list), prod_by_uid),
        'did_not_track': did_not_track,
        'did_not_track_uids': [u for u, _n in dnt_kept],   # published survivors, for next-day correction
        'held': held_names,   # guardrail: withheld pending settle + AI verification (not displayed)
        'held_ai_down': held_ai_down,   # of held: how many because the AI gate couldn't run
        'on_leave': on_leave_list,
        'planned': sorted(planned.values()),
        'alarm': alarm,
        'critical': bucket(0, 3),
        'low': bucket(3, 5),
        'below6': bucket(5, 6),
        'late': late,
        'unproductive': unproductive,
        'ghosts': ghosts,
        # For the ghost-payroll guard (CFO 2026-08-05): who currently HAS a matched
        # Time Doctor account (so recovered ghost tasks can auto-close), and the TD
        # accounts that matched no one (so the AI screen can spot a working person
        # hiding behind a variant name before HR is sent chasing them).
        'matched_names': sorted({(getattr(e, 'full_name', '') or '').strip()
                                 for e in matcher.employee_for_uid.values()
                                 if (getattr(e, 'full_name', '') or '').strip()}),
        'unmatched_td': [{'name': u.get('name') or '', 'email': u.get('email') or ''}
                         for u in getattr(matcher, 'unmatched_td', [])],
        'day_map': day_map,
        'momentum': mo,
        'leaderboard': leaderboard,
        'lb_hidden': lb_hidden,
        'focus': focus,
        'shortfall': shortfall,
        'unexplained': unexplained,
    }


def compute_weekly(client, monday, off_dates=None, persist_map=False):
    """Mon-Sat weekly exceptions. `monday` is the Monday of the week."""
    from hris import workforce
    if off_dates is None:
        off_dates = set()
    sat = monday + datetime.timedelta(days=5)
    users, ids, matcher, _ = _roster_and_matcher(client, persist_map)
    w_from = day_window_utc(monday)[0]
    w_to = day_window_utc(sat)[1]
    wl = client.worklog(w_from, w_to, user_ids=ids)
    tu = client.timeuse(w_from, w_to, user_ids=ids)
    day_map = per_user_day(users, wl)
    agg = aggregate(users, wl, tu, [], [], as_of=sat, td_user_ids=ids)
    prod_by_uid = {m['user_id']: m.get('productive_hours') for m in agg['members']}

    from hris import eligibility
    on_leave = eligibility.on_leave_names(sat)
    tracked, never, on_leave_list, ghosts = _split_matched(matcher, day_map, on_leave)
    # Planned days told to us across the week (any working day). Shown to the
    # manager and dropped from the DISPLAY "did not track" list (by employee id);
    # the "never tracked all week" alarm stays raw.
    planned = {}
    for i in range(6):
        planned.update(planned_for_day(monday + datetime.timedelta(days=i)))
    planned_ids = set(planned)
    # LIST not set — preserve multiplicity so same-named no-shows both count.
    never_display = sorted(
        (getattr(emp, 'full_name', '') or '').strip()
        for uid, emp in matcher.employee_for_uid.items()
        if (getattr(emp, 'full_name', '') or '').strip()
        and (day_map.get(uid) or {'sec': 0}).get('sec', 0) == 0
        and (getattr(emp, 'full_name', '') or '').strip().lower() not in on_leave
        and str(getattr(emp, 'id', '')) not in planned_ids
    )

    # Per-employee weekly target: managers / EXCO / FM are on the lighter 4.5h
    # weekday rate (CFO 2026-07-23), so they must NOT be flagged critical/low for
    # working their own full week. Falls back to the 35.5h staff week.
    from hris.workforce_roles import is_manager_hours_employee
    _target_cache: dict = {}

    def _emp_week_target(uid):
        if uid not in _target_cache:
            emp = matcher.employee_for_uid.get(uid)
            t = float(workforce.weekly_required_hours(
                monday, off_dates, is_manager=is_manager_hours_employee(emp)))
            _target_cache[uid] = t or 35.5
        return _target_cache[uid]

    crit = sorted([(r['name'], r['sec'], prod_by_uid.get(r['uid'])) for r in tracked
                   if _hours(r['sec']) < 0.30 * _emp_week_target(r['uid'])], key=lambda x: x[1])
    low = sorted([(r['name'], r['sec'], prod_by_uid.get(r['uid'])) for r in tracked
                  if 0.30 * _emp_week_target(r['uid']) <= _hours(r['sec']) < 0.60 * _emp_week_target(r['uid'])],
                 key=lambda x: x[1])
    emp_name_by_uid = {uid: (getattr(emp, 'full_name', '') or '').strip()
                       for uid, emp in matcher.employee_for_uid.items()}
    unproductive = sorted(
        [(emp_name_by_uid[m['user_id']], m.get('unproductive_seconds', 0)) for m in agg['members']
         if m.get('user_id') in emp_name_by_uid
         and (m.get('unproductive_seconds') or 0) > 600
         and emp_name_by_uid[m['user_id']].lower() not in on_leave],
        key=lambda x: x[1], reverse=True)[:10]
    # --- Team pulse for the week (momentum vs prior week · leaderboard · focus) -
    name_by_uid = {uid: (getattr(e, 'full_name', '') or '').strip()
                   for uid, e in matcher.employee_for_uid.items()}
    dept_by_uid = {uid: (getattr(e, 'department', '') or '').strip()
                   for uid, e in matcher.employee_for_uid.items()}
    matched_uids = list(matcher.employee_for_uid.keys())
    secs_by_uid = {uid: (day_map.get(uid) or {'sec': 0})['sec'] for uid in matched_uids}
    leaderboard, lb_hidden = workforce_pulse.team_leaderboard(
        matched_uids, secs_by_uid, prod_by_uid, dept_by_uid,
        always_show=_leaderboard_always_show())
    focus = workforce_pulse.focus_stats(
        tu, name_by_uid, matched_uids, ids,
        prod_secs_by_uid={uid: float(h or 0) * 3600 for uid, h in prod_by_uid.items()})

    # Weekly shortfall board — against each person's own WEEK target (managers
    # 25.5h, staff 35.5h). No day-streak here: the week itself is the streak.
    week_targets = {uid: _emp_week_target(uid) for uid in matched_uids}
    short_uids = [uid for uid in matched_uids
                  if secs_by_uid.get(uid, 0) > 0
                  and week_targets.get(uid)
                  and float(prod_by_uid.get(uid) or 0) < float(week_targets[uid])
                  and (name_by_uid.get(uid) or '').lower() not in on_leave]
    shortfall = workforce_pulse.shortfall_board(
        short_uids, prod_by_uid, name_by_uid, week_targets)

    # Nobody tracked ALL WEEK and nobody explained — the manager answers for that.
    dark_uids = [uid for uid, emp in matcher.employee_for_uid.items()
                 if (getattr(emp, 'full_name', '') or '').strip() in set(never_display)]
    dark_streaks, last_tracked = (_dark_streaks(client, users, ids, dark_uids, sat, back=8)
                                  if dark_uids else ({}, {}))
    unexplained = _unexplained_by_manager(matcher, never_display, dark_streaks, last_tracked)
    prev_secs = {}
    try:
        pmon = monday - datetime.timedelta(days=7)
        pf = day_window_utc(pmon)[0]
        pt = day_window_utc(pmon + datetime.timedelta(days=5))[1]
        pw_map = per_user_day(users, client.worklog(pf, pt, user_ids=ids))
        prev_secs = {uid: (pw_map.get(uid) or {'sec': 0})['sec'] for uid in matched_uids}
    except Exception:    # noqa: BLE001
        prev_secs = {}
    mo = workforce_pulse.momentum(secs_by_uid, prev_secs, name_by_uid)
    mo['compare_label'] = 'vs last week'

    return {
        'day': sat,
        'summary': _summary(tracked, never_display, len(matcher.employee_for_uid) - len(on_leave_list), prod_by_uid),
        'did_not_track': never_display, 'on_leave': on_leave_list, 'planned': sorted(planned.values()),
        'alarm': never, 'critical': crit, 'low': low,
        'below6': [], 'late': [], 'unproductive': unproductive, 'ghosts': ghosts, 'day_map': day_map,
        # Same two keys compute() returns, so the Sunday weekly run screens + auto-
        # resolves ghosts identically to a weekday (Fable review — without these the
        # weekly path bypassed the AI screen and chased working people once a week).
        'matched_names': sorted({(getattr(e, 'full_name', '') or '').strip()
                                 for e in matcher.employee_for_uid.values()
                                 if (getattr(e, 'full_name', '') or '').strip()}),
        'unmatched_td': [{'name': u.get('name') or '', 'email': u.get('email') or ''}
                         for u in getattr(matcher, 'unmatched_td', [])],
        'momentum': mo, 'leaderboard': leaderboard, 'lb_hidden': lb_hidden, 'focus': focus,
        'shortfall': shortfall, 'unexplained': unexplained,
    }


def _hms(sec):
    h = int((sec or 0) // 3600); m = int(((sec or 0) % 3600) // 60)
    return f'{h}h {m:02d}m'


log = logging.getLogger(__name__)


def _leave_split(day):
    """(on_leave_today, upcoming_next_7d) as lists of {name, start, end} from
    APPROVED LeaveRequests. Defensive: a broken lookup degrades to empty and is
    logged, never raised — a render must not die on a leave query."""
    today_rows, upcoming = [], []
    try:
        from hris.models import LeaveRequest
        horizon = day + datetime.timedelta(days=7)
        rows = (LeaveRequest.objects
                .filter(status=LeaveRequest.Status.APPROVED,
                        end_date__gte=day, start_date__lte=horizon)
                .select_related('profile__employee')
                .order_by('start_date'))
        for lr in rows:
            nm = (getattr(getattr(lr.profile, 'employee', None), 'full_name', '') or '').strip()
            if not nm:
                continue
            row = {'name': nm, 'start': lr.start_date, 'end': lr.end_date}
            if lr.start_date <= day <= lr.end_date:
                today_rows.append(row)
            elif lr.start_date > day:
                upcoming.append(row)
    except Exception:   # noqa: BLE001
        log.exception('_leave_split failed for %s', day)
    # Payroll-status leave has no dates but must still show as "on leave today":
    # the engine's exclusion set (eligibility.on_leave_names) counts these too, so
    # omitting them here would drop someone from the brief while still excluding
    # them from did-not-track — a silent contradiction of the footer (H55 drift).
    try:
        from payroll.models import Employee
        seen = {r['name'].strip().lower() for r in today_rows}
        for e in Employee.objects.filter(status='on_leave'):
            nm = (getattr(e, 'full_name', '') or '').strip()
            if nm and nm.lower() not in seen:
                today_rows.append({'name': nm, 'start': day, 'end': day})
                seen.add(nm.lower())
    except Exception:   # noqa: BLE001
        log.exception('_leave_split payroll on_leave lookup failed for %s', day)
    return today_rows, upcoming


def _birthdays_week(day):
    """Active-staff birthdays in the next 7 days as {name, in_days}. Name + how
    soon only — NEVER the year / date-of-birth (PII). Defensive: degrades to []."""
    out = []
    try:
        from hris.models import HRISProfile
        from payroll.models import Employee
        for off in range(7):
            d = day + datetime.timedelta(days=off)
            qs = (HRISProfile.objects
                  .filter(employee__status=Employee.Status.ACTIVE,
                          date_of_birth__month=d.month, date_of_birth__day=d.day)
                  .exclude(employee__full_name__exact='')
                  .exclude(employee__is_test_record=True)
                  .select_related('employee'))
            for p in qs:
                nm = (getattr(p.employee, 'full_name', '') or '').strip()
                if nm:
                    out.append({'name': nm, 'in_days': off})
    except Exception:   # noqa: BLE001
        log.exception('_birthdays_week failed for %s', day)
    return out


def build_html(data, helpdesk_url='https://omni.alphadirect.co.bw/helpdesk/', weekly=False,
               bug_url='https://omni.alphadirect.co.bw/report-bug', feedback_missing=None,
               show_tip=True):
    s = data['summary']; day = data['day']
    esc = _html.escape

    def chip(label, val, color=NAVY):
        return (f'<td style="padding:12px 14px;background:#F8FAFC;border:1px solid #EAEEF3;border-radius:10px">'
                f'<div style="font-size:11px;color:{MUT};text-transform:uppercase">{label}</div>'
                f'<div style="font-size:20px;font-weight:800;color:{color}">{val}</div></td>')

    def names_list(items, color=INK, fmt=None):
        if not items:
            return f'<div style="font-size:13px;color:{GREEN}">None — all clear. ✅</div>'
        rows = ''
        for it in items:
            rows += (f'<div style="font-size:13px;color:{color};padding:2px 0">{fmt(it)}</div>' if fmt
                     else f'<div style="font-size:13px;color:{color};padding:2px 0">• {esc(str(it))}</div>')
        return rows

    def section(color, emoji, title, body):
        return (f'<tr><td style="padding:16px 22px 0">'
                f'<div style="background:{color};color:#fff;font-size:13px;font-weight:700;'
                f'padding:8px 12px;border-radius:8px 8px 0 0">{emoji} {title}</div>'
                f'<div style="border:1px solid #EEF0F3;border-top:none;border-radius:0 0 8px 8px;padding:12px">{body}</div>'
                f'</td></tr>')

    def fmt_hours(t):
        # t is (name, seconds) or (name, seconds, productive_hours). Show the
        # productive-hours tail when we have it (CFO 2026-07-16 — productive hours
        # is the key metric, so it rides alongside the tracked total everywhere).
        prod = t[2] if len(t) > 2 else None
        tail = (f' <span style="color:{MUT}">· productive <b>{prod}h</b></span>'
                if prod is not None else '')
        return f'• {esc(str(t[0]))} — <b>{_hms(t[1])}</b>{tail}'
    fmt_late = lambda t: f'• {esc(str(t[0]))} — started {esc(str(t[1]))}'

    title = 'Weekly Exceptions Report' if weekly else 'Daily Exceptions Report'
    subtitle = (f"Week ending {day.strftime('%d %B %Y')} (Mon–Sat)" if weekly
                else day.strftime('%A, %d %B %Y')) + ' · for team managers'
    alarm_title = 'Never tracked all week' if weekly else 'Alarm — 3+ consecutive days no tracking'
    dnt_title = (f"Did not track all week ({s['did_not_track']})" if weekly
                 else f"Did not track today ({s['did_not_track']})")
    crit_title = 'Critically low week' if weekly else 'Critical low — under 3 hours'
    low_title = 'Low week hours' if weekly else 'Low hours — 3h to under 5h'

    # Guardrail transparency (Fable review 2026-08-01, H6): held names are NOT
    # shown, but their COUNT is — so a dead AI verification gateway (which would
    # hold everyone) can never masquerade as a clean "all tracked" day.
    _held = data.get('held') or []
    _ai_down = int(data.get('held_ai_down') or 0)
    _held_note = ''
    if _held:
        warn = ('  ⚠️ verification could not run for '
                f'{_ai_down} of these — check the AI service' if _ai_down else '')
        # A table row (not a bare div) — this sits between the section rows, so a
        # loose div would foster-parent to the top of the email (Fable review).
        _held_note = (
            f'<tr><td style="padding:2px 22px"><div style="font-size:12px;color:{MUT};'
            f'background:#F8FAFC;border:1px dashed #CBD5E1;border-radius:8px;padding:9px 12px;'
            f'margin:6px 0">⏳ {len(_held)} name(s) withheld pending Time Doctor data settling — '
            f'not yet confirmed either way, so not named above.{warn}</div></td></tr>')

    # THE MIRROR GATE, made loud (TD-ORPHAN-01, bug 5dffc022). Real recorded work
    # sitting on a Time Doctor account nobody owns. This section is NAMED and
    # never suppressed: the whole class of bug it guards against is silence, so
    # a quiet version of it would be worthless.
    _orph = data.get('orphan_hours') or []
    _orph_html = ''
    if _orph:
        _rows = ''.join(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #EEF1F5">'
            f'<b>{_html.escape(o["name"])}</b>'
            + (f'<span style="color:{MUT}"> — likely '
               f'{_html.escape(o["likely_owner"])}</span>'
               if o.get('likely_owner') else
               f'<span style="color:{MUT}"> — owner unknown</span>')
            + f'</td><td style="padding:6px 12px;border-bottom:1px solid #EEF1F5;'
            f'font-weight:700;white-space:nowrap">{o["hours"]}h</td></tr>'
            for o in _orph)
        _ai_note = ('  ⚠️ the AI owner-suggestion could not run — every row is '
                    'listed anyway' if data.get('orphan_hours_ai_down') else '')
        _orph_html = (
            f'<tr><td style="padding:2px 22px"><div style="font-size:13px;'
            f'border:2px solid {RED};border-radius:8px;padding:10px 12px;margin:8px 0">'
            f'<div style="font-weight:700;color:{RED};margin-bottom:6px">'
            f'🧩 {data.get("orphan_hours_total")}h of tracked work belongs to '
            f'NOBODY — {len(_orph)} unlinked Time Doctor account(s)</div>'
            f'<table style="border-collapse:collapse;width:100%">{_rows}</table>'
            f'<div style="font-size:12px;color:{MUT};margin-top:6px">These hours '
            f'are missing from someone\'s figure right now. Link each account to '
            f'its person in Omni (Time Doctor user map) and the hours return '
            f'automatically.{_ai_note}</div></div></td></tr>')

    extra = ''
    if not weekly:
        extra = (section(AMBER, '📊', 'Below 6 hours — 5h to under 6h', names_list(data['below6'], fmt=fmt_hours))
                 + section(NAVY, '🌅', 'Late starters — after 08:15', names_list(data['late'], fmt=fmt_late)))

    # --- Team pulse band: momentum + leaderboard + focus (CFO 2026-07-16) ------
    period_word = 'this week' if weekly else 'today'

    def momentum_html():
        mo = data.get('momentum') or {}
        if not mo:
            return ''
        pct = mo.get('pct')
        up = mo.get('up')
        col = GREEN if up else RED
        badge = '—' if pct is None else f'{"▲" if up else "▼"} {abs(pct)}%'
        imp = ' · '.join(f'{esc(n)} <b style="color:{GREEN}">+{h}h</b>' for n, h in mo.get('most_improved', []))
        imp = imp or 'steady across the board'
        return (
            f'<tr><td style="padding:16px 22px 0">'
            f'<div style="background:#F0FDF4;border:1px solid #C7EBD9;border-radius:10px;padding:12px 14px">'
            f'<div style="font-size:13px;font-weight:800;color:{NAVY}">📈 Team momentum</div>'
            f'<div style="font-size:14px;color:{INK};margin-top:4px"><b>{mo.get("this_h","—")}h</b> {period_word} '
            f'· <b style="color:{col}">{badge}</b> <span style="color:{MUT}">{esc(mo.get("compare_label",""))}</span></div>'
            f'<div style="font-size:12px;color:{MUT};margin-top:4px">Most improved: {imp}</div>'
            f'</div></td></tr>')

    def leaderboard_html():
        lb = data.get('leaderboard') or []
        if not lb:
            return ''
        medals = ['🥇', '🥈', '🥉']
        body = ('<table style="border-collapse:collapse;width:100%;font-size:13px">'
                f'<thead><tr style="color:{MUT};font-size:11px;text-transform:uppercase">'
                '<th style="text-align:left;padding:4px 6px">Team</th>'
                '<th style="text-align:center;padding:4px 6px">People</th>'
                '<th style="text-align:right;padding:4px 6px">Avg productive</th>'
                '<th style="text-align:right;padding:4px 6px">Avg hours</th></tr></thead><tbody>')
        ranked_i = 0
        for r in lb:
            if r.get('pinned'):
                rank = '👔'
                label = esc(r['dept'])
            else:
                rank = medals[ranked_i] if ranked_i < 3 else f'{ranked_i + 1}.'
                label = esc(r['dept'])
                ranked_i += 1
            body += (f'<tr>'
                     f'<td style="padding:5px 6px;border-bottom:1px solid #EEF0F3;color:{INK}">{rank} {label}</td>'
                     f'<td style="padding:5px 6px;border-bottom:1px solid #EEF0F3;text-align:center;color:{MUT}">{r["heads"]}</td>'
                     f'<td style="padding:5px 6px;border-bottom:1px solid #EEF0F3;text-align:right;color:{GREEN};font-weight:700">{r["avg_prod_h"]}h</td>'
                     f'<td style="padding:5px 6px;border-bottom:1px solid #EEF0F3;text-align:right;color:{INK}">{r["avg_h"]}h</td>'
                     f'</tr>')
        body += '</tbody></table>'
        hidden = data.get('lb_hidden', 0)
        if hidden:
            body += (f'<div style="font-size:11px;color:{MUT};margin-top:8px">Ranked on average productive hours '
                     f'per person; teams of 3+ trackers only. {hidden} person(s) not counted — small or '
                     f'unassigned teams (set their Department in omni to include them).</div>')
        return section('#4338CA', '🏆', f'Team leaderboard — {period_word}', body)

    def focus_html():
        foc = data.get('focus') or {}
        if not foc:
            return ''
        tf = foc.get('top_focus', [])
        best = foc.get('best_hour_label')
        medals = ['🥇', '🥈', '🥉']
        top_line = (''.join(
            f'<div style="font-size:13px;color:{INK};padding:2px 0">{medals[i] if i < 3 else "•"} '
            f'{esc(n)} — <b>{_hms(sec)}</b> productive, unbroken</div>'
            for i, (n, sec) in enumerate(tf))
            or f'<div style="font-size:13px;color:{MUT}">No productive focus blocks yet.</div>')
        peak = (f'<div style="font-size:13px;color:{INK};margin-top:6px">🕐 Team was sharpest around '
                f'<b>{esc(best)}</b></div>' if best else '')
        note = (f'<div style="font-size:11px;color:{MUT};margin-top:6px">Longest unbroken stretch of '
                f'PRODUCTIVE time (Time Doctor productive rows only, one machine — never two added '
                f'together).</div>')
        return section(NAVY, '🎯', f'Deepest focus — {period_word}', top_line + peak + note)

    def shortfall_html():
        """The mirror of the leaderboard: who missed their own target, and for how
        many days running (CFO 2026-07-30). Named, with their own number next to
        the target they were given — no adjectives, the gap does the talking."""
        rows = data.get('shortfall') or []
        if not rows:
            return section(GREEN, '💪', f'Everyone hit their hours — {period_word}',
                           f'<div style="font-size:13px;color:{GREEN}">Not one person short of target. '
                           f'Say so out loud today. ✅</div>')
        body = ''
        for r in rows:
            st = int(r.get('streak') or 1)
            if st >= 3:
                badge = (f'<span style="background:{RED};color:#fff;font-size:11px;font-weight:700;'
                         f'padding:1px 6px;border-radius:10px">{st} days in a row</span>')
            elif st == 2:
                badge = (f'<span style="background:{AMBER};color:#fff;font-size:11px;font-weight:700;'
                         f'padding:1px 6px;border-radius:10px">2nd day</span>')
            else:
                badge = f'<span style="color:{MUT};font-size:11px">first day</span>'
            body += (f'<div style="font-size:13px;color:{INK};padding:3px 0">'
                     f'🐢 {esc(r["name"])} — <b style="color:{RED}">{r["prod_h"]}h</b> productive of '
                     f'<b>{r["target_h"]}h</b> target · short <b style="color:{RED}">{r["gap_h"]}h</b> '
                     f'{badge}</div>')
        body += (f'<div style="font-size:11px;color:{MUT};margin-top:8px">Measured on PRODUCTIVE hours '
                 f'against each person\'s own target (managers/EXCO 4.5h weekday, staff 6.5h). '
                 f'People who did not track at all are named in their own section above. '
                 f'Ask them today — a gap nobody mentions becomes next month\'s appraisal surprise.</div>')
        return section(RED, '🐢', f'Short of target — {period_word}', body)

    def unexplained_html():
        """The accountability block, and the first thing in the email (CFO
        2026-07-30 — "nail the people who don't track … ask the manager why").

        Absence with no leave applied and no explanation is a MANAGER's item, so
        the manager is named as the owner and their people are listed under them
        with the department against each name. The questions are addressed to the
        manager, not to the room."""
        groups = data.get('unexplained') or {}
        if not groups:
            return ''
        people = sum(len(v) for v in groups.values())
        owned = [m for m in groups if m not in NON_MANAGER_KEYS]
        body = ''
        for mgr, rows in groups.items():
            unowned = mgr in NON_MANAGER_KEYS
            is_exec = (mgr == EXEC_KEY)
            worst = max(r['days_dark'] for r in rows)
            head_col = MUT if unowned else RED
            if is_exec:
                head_col = NAVY                       # the board gets the house colour
                heading = f'🏛️ {esc(mgr)} · {len(rows)} of your executives'
            elif unowned:
                heading = f'❓ {esc(mgr)} — HR to assign one · {len(rows)} to answer for'
            else:
                heading = f'👤 {esc(mgr)} · {len(rows)} to answer for'
            body += (f'<div style="margin:0 0 10px;border:1px solid #F3D6D6;border-radius:10px;overflow:hidden">'
                     f'<div style="background:{head_col};padding:7px 12px;color:#fff;font-size:12px;font-weight:800">'
                     f'{heading}</div>')
            for r in rows:
                dd = r['days_dark']
                dcol = RED if dd >= 3 else AMBER if dd == 2 else MUT
                last = (f' · last tracked <b>{esc(r["last_tracked"])}</b>' if r.get('last_tracked')
                        else ' · <b>no tracking in the last 6 working days</b>')
                body += (f'<div style="font-size:13px;color:{INK};padding:6px 12px;border-top:1px solid #F7E7E7">'
                         f'⛔ <b>{esc(r["name"])}</b> <span style="color:{MUT}">({esc(r["dept"])})</span> — '
                         f'<b style="color:{dcol}">{dd} day{"s" if dd != 1 else ""} no Time Doctor</b>'
                         f'{last}</div>')
            if is_exec:
                # The board is asked, not told — and it is asked the one question
                # that matters when the person at the top is the one not tracking.
                who = ', '.join(esc(r['name']) for r in rows)
                roster = (data.get('summary') or {}).get('roster') or 0
                crowd = f'{roster} people are' if roster else 'the whole company is'
                body += (f'<div style="font-size:12px;color:{INK};padding:8px 12px;'
                         f'border-top:1px solid #F7E7E7;background:#F5F9FF">'
                         f'Good morning. {crowd} asked to clock in every day, and '
                         f'{who} has not for <b>{worst} day{"s" if worst != 1 else ""}</b>. '
                         f'The rule is easier to enforce from the front. '
                         f'<b>Over to you</b> — an employee cannot ask this of a CEO.</div>')
            body += '</div>'
        mgr_word = ('manager' if len(owned) == 1 else 'managers')
        # Only ask the questions when a real manager owes an answer. With just the
        # board bucket, "0 managers — answer these today" would be nonsense.
        if owned:
            body += (f'<div style="background:#FFF7E8;border:1px solid #F3E4C4;border-radius:10px;'
                     f'padding:12px 14px;margin-top:4px">'
                     f'<div style="font-size:13px;font-weight:800;color:{NAVY};margin-bottom:6px">'
                     f'❓ {len(owned)} {mgr_word} — answer these today</div>'
                     f'<div style="font-size:13px;color:{NAVY};line-height:1.75">'
                     f'1. Your person recorded <b>no time at all</b> and filed <b>no leave</b> and '
                     f'<b>no reason</b>. Where were they, and did you know?<br>'
                     f'2. If they were off — why was leave not applied for, and who approved the absence?<br>'
                     f'3. If they were working — why is there nothing on Time Doctor, and what have you '
                     f'done about it since?<br>'
                     f'4. Reply to this email with the answer per person. <b>No answer is itself the '
                     f'finding</b> and it goes up with your name on it.</div></div>')
        title = f'Unexplained — no hours, no leave, no reason ({people})'
        if owned:
            title += f' · {len(owned)} {mgr_word} must answer'
        return section(RED, '🚩', title, body)

    def ghost_task_note():
        """Under the ghost list: every ghost is now an HR task with a deadline, so
        the list stops being something everyone reads and nobody owns
        (CFO 2026-07-30)."""
        if not data.get('ghosts'):
            return ''
        new = data.get('ghost_tasks_new') or []
        open_n = data.get('ghost_tasks_open')
        bits = []
        if new:
            bits.append(f'<b>{len(new)}</b> new task(s) raised with HR this morning')
        if open_n:
            bits.append(f'<b>{open_n}</b> already open')
        line = ' · '.join(bits) or 'Tasks are raised with HR each morning'
        return (f'<div style="font-size:11px;color:{MUT};margin-top:8px">🗂️ {line}. HR has '
                f'<b>3 days</b> to confirm each name: either they no longer work here (start the '
                f'payroll removal — the change itself is CFO-authorised), or they DO work here and '
                f'the tracker is missing. Unanswered after 3 days, HR gets named right below.</div>')

    def hr_overdue_html():
        """HR named for sitting on ghost tasks past the deadline (CFO 2026-07-30 —
        "3 days after HR doesn't respond or fix, make fun of HR also").

        Managers are named in this email for an unanswered question; HR does not get
        an exemption. Wry, but only facts: who owns it, which name, how many days
        late — and the reason it matters, which is that payroll keeps paying."""
        rows = data.get('hr_overdue') or []
        if not rows:
            return ''
        worst = max(r['days_late'] for r in rows)
        owners = sorted({r['hr'] for r in rows})
        body = ''
        for r in rows:
            body += (f'<div style="font-size:13px;color:{INK};padding:3px 0">'
                     f'🙈 <b>{esc(r["person"])}</b> — still on payroll, still no Time Doctor · '
                     f'<b style="color:{RED}">{r["days_late"]} day'
                     f'{"s" if r["days_late"] != 1 else ""} past HR\'s deadline</b> '
                     f'<span style="color:{MUT}">(owner: {esc(r["hr"])})</span></div>')
        body += (f'<div style="font-size:12px;color:{INK};margin-top:8px">'
                 f'{esc(", ".join(owners))} — the ghosts are winning {worst}–0. '
                 f'Every day this sits open, payroll pays somebody nobody can find. '
                 f'Answer the task in omni: are they gone, or is the tracker missing?</div>')
        return section(RED, '🙈',
                       f'HR has not answered — ghost payroll tasks past deadline ({len(rows)})',
                       body)

    pulse_html = (unexplained_html() + momentum_html() + leaderboard_html()
                  + focus_html() + shortfall_html())

    # Ghost payroll is a PRIVATE HR matter now — a task owned by Dorothy, NOT a
    # line in this all-manager email (CFO 2026-08-05: "we should not report this in
    # the morning time doctor"). Naming a mismatched-but-working person to every
    # manager was the whole failure. Kept behind a flag so it can be switched back.
    from django.conf import settings as _st_ghost
    _ghost_section = ''
    if getattr(_st_ghost, 'WORKFORCE_GHOSTS_IN_EMAIL', False):
        _ghost_section = section(
            MUT, '👻',
            f"Payroll ghost list ({len(data['ghosts'])}) — on payroll, no Time Doctor presence",
            names_list(data['ghosts']) + ghost_task_note())

    # HR accountability rides its OWN switch (CFO 2026-08-09). Naming HR for
    # sitting on a ghost task past the 3-day deadline was the CFO's request of
    # 2026-07-30, but it lived inside the ghost block above — so switching the
    # ghost LIST off on 5 Aug silently switched HR CHASING off too. That was a
    # side effect, not a decision. The two are different things: the ghost list
    # names a member of STAFF who may simply be mismatched in the tracker, while
    # this names the HR owner of an unanswered task. Default ON, because the
    # deadline is meaningless if nobody sees it pass.
    if getattr(_st_ghost, 'WORKFORCE_HR_OVERDUE_IN_EMAIL', True):
        _ghost_section += hr_overdue_html()

    # Monthly performance-feedback non-compliance (CFO 2026-07-20). Managers who
    # still owe monthly feedback are named for the C-suite. Empty → no section.
    _fb_section = ''
    if feedback_missing:
        # Each name is a link straight to that manager's feedback screen (CFO
        # 2026-08-07 — naming someone without giving them the button is what
        # made this hard). Plain strings still render, for older callers.
        def _fb_row(it):
            if isinstance(it, dict):
                return (f'• {esc(it.get("label", ""))} '
                        f'<a href="{esc(it.get("url", ""))}" '
                        f'style="color:{ORANGE};font-weight:700;text-decoration:underline">'
                        f'give feedback →</a>')
            return f'• {esc(str(it))}'
        _fb_section = section(
            RED, '📝',
            f"Managers who have NOT logged monthly performance feedback ({len(feedback_missing)})",
            names_list(feedback_missing, fmt=_fb_row))

    # C4/C6/C7 — dated leave split, upcoming birthdays, rotating "Did you know?".
    _lt, _lu = _leave_split(day)
    _bd = _birthdays_week(day)

    def _fmt_leave(r):
        if r['start'] == r['end']:
            return f"• {esc(r['name'])} — {r['start']:%a %d %b}"
        return f"• {esc(r['name'])} — {r['start']:%d %b} – {r['end']:%d %b}"

    def _fmt_bday(r):
        when = ('today 🎂' if r['in_days'] == 0
                else 'tomorrow' if r['in_days'] == 1
                else f"in {r['in_days']} days")
        return f"• {esc(r['name'])} — {when}"

    _bday_section = (section('#7C3AED', '🎉',
                             f"Upcoming birthdays this week ({len(_bd)})",
                             names_list(_bd, fmt=_fmt_bday)) if _bd else '')

    _tip_html = ''
    if show_tip:
        from hris import omni_tips
        _tip = omni_tips.tip_for(day)
        _tip_links = ''.join(
            f'<a href="{esc(u)}" style="display:inline-block;margin:6px 8px 0 0;padding:6px 12px;'
            f'background:{NAVY};color:{ORANGE};text-decoration:none;border-radius:8px;'
            f'font-weight:700;font-size:12px">{esc(l)}</a>'
            for l, u in _tip.get('links', []))
        _tip_html = (
            f'<tr><td style="padding:16px 22px 0"><div style="background:#FFF7E8;'
            f'border:1px solid #F3E4C4;border-radius:10px;padding:14px">'
            f'<div style="font-size:11px;color:{AMBER};text-transform:uppercase;'
            f'letter-spacing:.04em;font-weight:700">{_tip.get("emoji","💡")} Did you know?</div>'
            f'<div style="font-size:15px;font-weight:800;color:{NAVY};margin-top:3px">'
            f'{esc(_tip.get("title",""))}</div>'
            f'<div style="font-size:13px;color:{INK};margin-top:4px">{esc(_tip.get("blurb",""))}</div>'
            f'{_tip_links}</div></td></tr>')

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ margin:0; }}
  .ad-wrap {{ max-width:680px; margin:16px auto; }}
  @media only screen and (max-width:480px) {{
    .ad-wrap {{ width:100% !important; border-radius:0 !important; }}
    .ad-chips, .ad-chips tbody, .ad-chips tr {{ display:block !important; width:100% !important; }}
    .ad-chips td {{ display:inline-block !important; width:45% !important; }}
  }}
</style></head>
<body style="margin:0;background:#EEF2F7;font-family:'Book Antiqua','Palatino Linotype',Palatino,Georgia,serif">
<div class="ad-wrap" style="max-width:680px;margin:16px auto;background:#fff;border-radius:16px;overflow:hidden">
  <div style="background:linear-gradient(135deg,{NAVY} 0%,#243b55 70%,{ORANGE} 170%);padding:24px 22px">
    <div style="font-size:12px;color:#FFD98A;letter-spacing:.06em;text-transform:uppercase">Alpha Direct · Workforce</div>
    <div style="font-size:22px;font-weight:800;color:#fff;margin-top:4px">{title}</div>
    <div style="font-size:13px;color:#C7D2E0;margin-top:3px">{subtitle}</div>
  </div>
  <table cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">
    <tr><td style="padding:18px 22px 0">
      <table class="ad-chips" cellspacing="8" role="presentation" style="border-collapse:separate;width:100%"><tr>
        {chip('Tracked', s['tracked'])}
        {chip('Did not track', s['did_not_track'], RED)}
        {chip('Total hours', s['total_h'])}
        {chip('Productive hours', '—' if s.get('prod_h') is None else s['prod_h'], GREEN)}
        {chip('Avg / user', s['avg_h'])}
      </tr></table>
    </td></tr>
    {pulse_html}
    {section('#0F766E', '🌴', f"On leave today ({len(_lt)})", names_list(_lt, fmt=_fmt_leave))}
    {(section('#0EA5A0', '📅', f"Upcoming leave — next 7 days ({len(_lu)})", names_list(_lu, fmt=_fmt_leave)) if _lu else '')}
    {_bday_section}
    {(section('#1D4ED8', '🗓️', f"Told us in advance — planned / explained ({len(data.get('planned', []))})", names_list(data.get('planned', []))) if data.get('planned') else '')}
    {section(RED, '🚨', alarm_title, names_list(data['alarm']))}
    {section(AMBER, '⛔', dnt_title, names_list(data['did_not_track']))}
    {_held_note}
    {_orph_html}
    {section(RED, '⏱️', crit_title, names_list(data['critical'], fmt=fmt_hours))}
    {section(AMBER, '📉', low_title, names_list(data['low'], fmt=fmt_hours))}
    {extra}
    {_fb_section}
    {section(NAVY, '🎯', 'High unproductive time', names_list(data['unproductive'], fmt=fmt_hours))}
    {_ghost_section}
    {_tip_html}
    <tr><td style="padding:16px 22px 22px">
      <div style="background:#FFF7E8;border:1px solid #F3E4C4;border-radius:8px;padding:12px;font-size:12px;color:{AMBER}">
        ⚠️ Prepared by AI. Approved leave is excluded above, but always review alongside HR records before any action.
        Tracking not working for someone on your team? They can raise an IT ticket at
        <a href="{helpdesk_url}" style="color:{NAVY}">the omni Help Desk</a>.
      </div>
      <div style="margin-top:8px;font-size:10px;color:#9CA3AF;line-height:1.4">
        Human Resources: if any name here is wrong — the person was actually working, is on leave, or has resigned —
        <a href="{bug_url}" style="color:#9CA3AF;text-decoration:underline">click here to upload evidence (a screenshot) to omni bug reporting</a>
        so we can correct it at source.
      </div>
    </td></tr>
  </table>
</div></body></html>"""
