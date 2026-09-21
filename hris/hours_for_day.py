"""
hris/hours_for_day.py

THE one door for "how many hours did this person work on day D".

Checklist L27 says there must be a single reader for that question, because when
several jobs and screens each dug into the raw Time Doctor snapshot with their
own window and their own freshness, they disagreed: one employee's brief emailed
3.24 h while her portal showed 1.74 h for the same day, and she reported it
twice. So every hris consumer comes through here, and this module owns the choice
of mechanism. There are three, and which one you want depends on what you
can afford. `live` and `floored` are the two real choices; `stored` is an escape
hatch with one caller:

    live(day, snapshot_payload)   -> Time Doctor's CURRENT view of the day.
        Delegates to integrations.td_live.rows_for_day (the Time-Doctor-side
        primitive: live first, snapshot fallback, and it refuses a live read
        totalling less than the stored snapshot). Costs one API call, so it fits
        a single day inside a cron. Use it when you need PRODUCTIVE hours, which
        the permanent record does not hold.

    stored(day, snapshot_payload) -> the STORED snapshot rows, unchanged.
        The escape hatch, and the only one. For a consumer that can afford
        neither an API call nor a per-day query — today that is the monthly
        cost-per-hour report, which runs inside an uncached web request and would
        need 31 live pulls. It exists so that consumer still comes through this
        door instead of hand-rolling its own `snap.payload` read, which is what
        L27 is actually about. What keeps these figures honest is the 15:50 SAST
        catch-up pull in infra/cron/timedoctor-pull.cron.

    floored(day, emp_hours)       -> TRACKED hours raised to the permanent record.
        One database query, no API call. Use it when you are looping over many
        days, or running inside a web request, or only need "not less than what
        Omni already recorded". Since L28 the record can never be revised down,
        so for a settled day it carries the corrected figure.

All three answer the same question and, for a day that has been reconciled, the same
way — `reconcile_workday_records` writes the live figure into the record. They
differ only for a day not yet reconciled, where `live` is fresher.

**Metric warning.** The record holds TRACKED hours. Never floor a PRODUCTIVE
figure with it: productive is always <= tracked, so that would silently inflate
it and hide a real shortfall. Productive-hours consumers use `live`.
"""
from __future__ import annotations

import datetime
import logging

log = logging.getLogger(__name__)


class RecordUnavailable(RuntimeError):
    """The permanent record could not be read, so no floor could be applied."""


def live(day: datetime.date, snapshot_payload):
    """`(rows, source)` — Time Doctor's current view of `day`.

    Thin delegation to the Time-Doctor-side primitive, kept here so callers have
    one module to import and cannot pick the wrong mechanism by accident.
    """
    from integrations.td_live import rows_for_day
    return rows_for_day(day, snapshot_payload)


def stored(day: datetime.date, snapshot_payload):
    """The stored snapshot rows for `day`, unchanged — no API call, no query.

    Deliberately a pass-through. Its job is to be the ONE place a
    snapshot-only consumer is allowed to read from, so that choice is visible
    and reviewable here rather than buried as a raw `snap.payload` in a report.
    Only use it where both other modes are genuinely unaffordable, and say why
    at the call site.

    Caller beware: this is a FLOOR, not the truth. Time Doctor back-fills a day
    after the pull, so a figure read this way can be low until the 15:50 SAST
    catch-up pull refreshes it. Never judge a person against it — use `floored`
    (which fails closed) or `live`.
    """
    return list(snapshot_payload or [])


def floored(day: datetime.date, emp_hours: dict, profiles=None,
            on_error: str = 'raise') -> dict:
    """`{employee_id: hours}` with every entry raised to the permanent record.

    `emp_hours` is what `integrations.td_matching.hours_by_employee` returned for
    `day` (TRACKED hours, keyed by `employee_id`). An employee ABSENT from it
    stays absent — absent means "no matched Time Doctor account", which callers
    tell apart from a matched zero, and inventing an entry here would turn a
    person with no tracker into one who looks tracked.

    `profiles` narrows the query when the caller already holds the roster.

    **`on_error` defaults to 'raise', and that default is deliberate.** If the
    record cannot be read there is no floor, and the un-floored figure is exactly
    the understated number this exists to prevent — the one that told a person
    they were short on a day Omni had already corrected. Every caller that
    ACCUSES somebody (the weekly review, the Saturday explain-yourself email)
    must fail closed, the same way both already do when Time Doctor cannot
    confirm an identity: an unproven shortfall never becomes an accusation.
    Pass `on_error='degrade'` only where the figure decorates something and a
    dead run would be worse than a stale number; it is logged loudly either way.
    """
    if not emp_hours:
        return emp_hours
    try:
        from hris.models import WorkdayJustification
        rows = (WorkdayJustification.objects
                .filter(work_date=day, profile__employee_id__in=list(emp_hours))
                .values_list('profile__employee_id', 'tracked_hours'))
        if profiles is not None:
            rows = rows.filter(profile__in=profiles)
        out = dict(emp_hours)
        for emp_id, recorded in rows:
            rec = float(recorded or 0)
            if emp_id in out and rec > float(out[emp_id] or 0):
                out[emp_id] = rec
        return out
    except Exception as exc:    # noqa: BLE001 — decided by on_error, never silent
        log.warning('hours_for_day: could not read the permanent record for %s '
                    '(%s) — no floor could be applied.', day, exc, exc_info=True)
        if on_error == 'degrade':
            return emp_hours
        raise RecordUnavailable(
            f'Could not read the permanent hours record for {day} ({exc}). '
            f'Refusing to judge anyone against an un-floored Time Doctor figure.'
        ) from exc


def floored_rows(day: datetime.date, snapshot_payload, employees=None,
                 on_error: str = 'degrade'):
    """Snapshot member ROWS with `hours_tracked` raised to the permanent record.

    The row-shaped sibling of `floored`, for the screens that render Time Doctor
    rows directly rather than a per-employee dict: the manager/HR Time Doctor
    report and the Telegram hours lookup. Both showed a person fewer hours than
    Omni held for them.

    Returns a NEW list; the stored payload is never mutated. Rows whose Time
    Doctor account is not matched to an employee pass through untouched — an
    unmatched row is not evidence of anything, and inventing a figure for it
    would be the name-guessing L19 forbids.

    Defaults to `on_error='degrade'`: these are read-only displays, so a blank
    screen would be worse than a figure that is merely stale. The two commands
    that ACCUSE someone use `floored`, which fails closed.
    """
    rows = list(snapshot_payload or [])
    if not rows:
        return rows
    try:
        from integrations.td_matching import TDMatcher, collapse_users, fold_uid
        if employees is None:
            from hris import eligibility
            employees = [p.employee for p in eligibility.tracking_profiles()]
        matcher = TDMatcher(collapse_users(rows), employees)
        emp_by_uid = {str(fold_uid(uid)): emp
                      for uid, emp in matcher.employee_for_uid.items()}
        if not emp_by_uid:
            return rows
        # One dict for the whole day, then raise each row from it.
        by_emp = {}
        for m in rows:
            uid = m.get('user_id')
            emp = emp_by_uid.get(str(fold_uid(uid))) if uid else None
            if emp is not None:
                by_emp[emp.id] = max(float(by_emp.get(emp.id, 0)),
                                     float(m.get('hours_tracked') or 0))
        raised = floored(day, by_emp, on_error=on_error)
        out = []
        for m in rows:
            uid = m.get('user_id')
            emp = emp_by_uid.get(str(fold_uid(uid))) if uid else None
            rec = raised.get(emp.id) if emp is not None else None
            if rec is not None and float(rec) > float(m.get('hours_tracked') or 0):
                m = {**m, 'hours_tracked': float(rec)}
            out.append(m)
        return out
    except RecordUnavailable:
        # The caller explicitly asked to fail closed (on_error='raise'). Letting
        # this be swallowed by the broad handler below would turn a deliberate
        # refusal back into a silent stale figure — the exact thing the caller
        # was avoiding. Caught by the Telegram fail-closed test.
        raise
    except Exception:    # noqa: BLE001 — a display must render; stale beats blank
        log.warning('hours_for_day: could not floor the rows for %s — showing the '
                    'stored snapshot figures unchanged.', day, exc_info=True)
        return rows


def record_hours_by_date(employee, start: datetime.date, end: datetime.date) -> dict:
    """`{work_date: tracked_hours}` from the permanent record for one employee.

    For a screen that already holds a per-day map from the snapshots and needs
    to raise it — the /my-omni hours tile. Own-scoped by the caller: pass the
    employee whose page is being rendered, never a roster.
    """
    try:
        from hris.models import WorkdayJustification
        return {w.work_date: float(w.tracked_hours or 0) for w in
                (WorkdayJustification.objects
                 .filter(profile__employee=employee,
                         work_date__gte=start, work_date__lte=end)
                 .only('work_date', 'tracked_hours'))}
    except Exception:    # noqa: BLE001 — a tile must render; stale beats blank
        log.warning('hours_for_day: could not read the record for %s between %s '
                    'and %s.', getattr(employee, 'pk', '?'), start, end, exc_info=True)
        return {}
