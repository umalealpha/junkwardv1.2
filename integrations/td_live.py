"""
integrations/td_live.py

ONE reader for "what does Time Doctor say about day D, right now", plus the
floor rule that protects a person's recorded hours.

THE RULE (CFO directive 2026-09-09, after the same person reported it twice):
**a stored Time Doctor figure for a completed day is a FLOOR, not the truth.**
Time Doctor BACK-FILLS time a machine buffered while it was offline, so the
figure for a day keeps GROWING after the day ends. Nothing re-pulls a completed
day after `pull_timedoctor --slot 0830` (06:30 UTC), so
`TimeDoctorDailySnapshot` freezes at whatever had arrived by then.

Two things went wrong because that rule was not stated anywhere:

  * `send_daily_brief` (07:05 UTC) wrote the permanent record from the 06:30
    snapshot while `send_morning_brief` (07:00 UTC) read Time Doctor live — so
    one employee's EMAIL said 3.24 h and her PORTAL said 1.74 h for the same day
    (bug c82def7f). Her machine had gone offline in a power cut.
  * `reconcile_workday_records` (07:35 + 14:10 UTC) — the command written to
    correct exactly this class of error — re-read the SAME stale snapshot and
    rewrote `tracked_hours` in EITHER direction. Its guard refuses to worsen a
    person's STATUS but never refuses to lower their HOURS, so it pulled the
    corrected figure back down every day. That is why the first report
    (5dffc022, 3-Sep) was closed and the same thing happened again on 9-Sep.

So: read live first, fall back to the stored snapshot, and NEVER let a machine
revise a person's recorded hours DOWNWARD on the strength of a Time Doctor read.
Downward is what an incomplete read looks like; upward is what the truth looks
like. A genuine reduction is a human decision, not a cron's.
"""
from __future__ import annotations

import datetime
import logging

log = logging.getLogger(__name__)


def live_members(day: datetime.date):
    """Time Doctor's CURRENT view of `day`, as snapshot-shaped member rows.

    The same Botswana-day window and the same `aggregate()` call
    `send_morning_brief` uses, so every reader of "hours for day D" agrees.

    Returns None — never an empty list — when Time Doctor is unconfigured,
    returns no roster, or the pull fails, so the caller falls back to the stored
    snapshot rather than scoring people against zero hours.
    """
    try:
        from hris import exceptions_report
        from integrations.timedoctor import TimeDoctorClient, aggregate
        client = TimeDoctorClient.from_settings()
        if not client.configured:
            return None
        day_from, day_to = exceptions_report.day_window_utc(day)
        users = client.users()
        ids = [u.get('id') for u in users if u.get('id')]
        if not ids:
            return None
        members = aggregate(users,
                            client.worklog(day_from, day_to, user_ids=ids),
                            client.timeuse(day_from, day_to, user_ids=ids),
                            [], [], as_of=day, td_user_ids=ids)['members']
        return members or None
    except Exception:    # noqa: BLE001 — degrade to the snapshot, never crash a brief
        # Never silent: a failure here means every reader falls back to a figure
        # that may under-report, so it has to be visible in the ops digest.
        log.warning('Time Doctor live read failed for %s — callers will fall '
                    'back to the stored snapshot.', day, exc_info=True)
        return None


def total_hours(members) -> float:
    """Sum of tracked hours across member rows (0.0 for None/empty)."""
    return sum(float(m.get('hours_tracked') or 0) for m in (members or []))


def rows_for_day(day: datetime.date, snapshot_payload):
    """(rows, source) for `day` — live when it is at least as complete as the
    stored snapshot, else the snapshot.

    Guards the DEGENERATE live read: `aggregate()` emits a row for every roster
    user, so a 200 response with an empty worklog comes back as a full list of
    ZEROS, which `members or None` cannot detect. Writing that over a settled
    snapshot would record the whole company at 0 h and dock real leave. By the
    floor rule a live total BELOW the stored total means the read is incomplete,
    never that the work vanished — so the snapshot wins and we say so loudly.
    """
    snapshot_payload = snapshot_payload or []
    live = live_members(day)
    if live is None:
        return snapshot_payload, 'snapshot (live read unavailable)'
    live_total, snap_total = total_hours(live), total_hours(snapshot_payload)
    if snap_total > 0 and live_total < snap_total:
        log.warning('Time Doctor live read for %s totals %.2f h against a stored '
                    '%.2f h — treating the live read as INCOMPLETE and keeping the '
                    'snapshot (hours are a floor, they do not shrink).',
                    day, live_total, snap_total)
        return snapshot_payload, 'snapshot (live read looked incomplete)'
    return live, 'live'
