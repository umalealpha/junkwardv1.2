"""
integrations/td_integrity.py

Fake-activity detector for the Time Doctor daily pull — catches the "weight on
the spacebar" trick, where a heavy object held on a key feeds Time Doctor a
constant stream of keystrokes so it records productive hours while nobody is
actually working (the Natasha Nthite audit, Aug 2026).

The trick has a fingerprint genuine work never shows, and every signal below is
derived from data omni ALREADY pulls (worklog + timeuse). NOTHING new is
collected and, in keeping with AD-POL-AI-GOV-001, NO window/app titles are
returned or stored — only privacy-safe counts and minutes.

Two catchers:

  1. SESSION CONTINUITY  (from worklog rows: start + time + mode)  — PRIMARY.
     A held key keeps Time Doctor "active" with no idle pauses, so the day is
     one long unbroken block. Real work has natural idle gaps (thinking, phone,
     coffee, meetings away from the desk). We measure the longest unbroken
     stretch and how many real breaks there were. Uses only fields proven live
     in the API (see integrations/test_td_matching.py fixtures).

  2. WINDOW VARIETY  (from timeuse rows: the window/app title)  — CORROBORATING.
     Real work jumps between many windows/apps a day; a weight on the spacebar
     sits in ONE window. We keep only the COUNT of distinct windows, never the
     titles. The exact title field name is NOT proven in our code (we normally
     discard it), so this catcher is DEFENSIVE: if it cannot read a title it
     returns None and NEVER contributes to a flag. It can only raise confidence
     on a session already flagged by catcher 1 — it can never accuse alone.

The detector FLAGS for the human "explain your day" step; it never docks on its
own. A genuine heads-down focus session can look continuous, which is exactly
why a person's own explanation is the gate before any leave is touched.

Pure stdlib (datetime only) — no Django, no DB — so it unit-tests offline
against synthetic days with no network and no sqlite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from integrations.td_matching import canonical_identity

# ---- Tunable thresholds (deliberately conservative — this feeds a real-pay
# accountability flow, so we would rather miss a cheat than accuse a worker).
IDLE_GAP_SECONDS        = 5 * 60      # a gap >= 5 min counts as a real break
BLOCK_FLAG_MINUTES      = 180        # >= 3 h with ZERO real breaks is implausible
MAX_BREAKS_FOR_FLAG     = 1          # a flagged day has at most one break
MIN_PRODUCTIVE_HOURS    = 3.0        # ignore short days — nothing worth faking
LOW_WINDOW_VARIETY      = 2          # <= 2 distinct windows all day is monotonous

# Candidate keys the Time Doctor timeuse row MIGHT carry the window/app title
# under. We never read the value itself for storage — only to count distinct
# windows. If none of these are present the variety catcher stays silent.
_TITLE_KEYS = ('title', 'name', 'window', 'app', 'value', 'application')


def _parse_start(v: Any) -> Optional[datetime]:
    """Parse a Time Doctor `start` (ISO, e.g. '2026-07-24T07:00:00Z') to an
    aware UTC datetime. Returns None on anything unparseable — never raises."""
    if not v:
        return None
    try:
        s = str(v).strip().replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:      # noqa: BLE001
        return None


@dataclass
class SessionSignal:
    tracked_minutes:     float = 0.0
    longest_block_min:   float = 0.0
    idle_breaks:         int   = 0
    machine_count:       int   = 1


@dataclass
class IntegritySignal:
    user_id:             Any = None
    name:                str = ''
    tracked_minutes:     float = 0.0
    longest_block_min:   float = 0.0
    idle_breaks:         int   = 0
    machine_count:       int   = 1
    distinct_windows:    Optional[int] = None   # None => could not read (never flags)
    productive_hours:    Optional[float] = None
    suspicion:           str = 'clean'          # clean | watch | suspicious
    reasons:             List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        # Privacy-safe: counts + minutes only, no titles (AD-POL-AI-GOV-001).
        return {
            'longest_block_min': round(self.longest_block_min, 1),
            'idle_breaks':       self.idle_breaks,
            'distinct_windows':  self.distinct_windows,
            'suspicion':         self.suspicion,
            'reasons':           list(self.reasons),
        }


def _flatten_per_user(per_user_arrays: List[Any]) -> List[dict]:
    """worklog/timeuse arrive as a list of per-user arrays of row dicts."""
    rows: List[dict] = []
    for bucket in (per_user_arrays or []):
        if isinstance(bucket, list):
            rows.extend(r for r in bucket if isinstance(r, dict))
        elif isinstance(bucket, dict):
            rows.append(bucket)
    return rows


def _session_signal(rows: List[dict]) -> SessionSignal:
    """Longest unbroken block + real-break count for ONE machine's worklog rows.

    Manual rows (time typed in after the fact) are excluded — they are not an
    observed session and would fake a huge block on their own."""
    intervals: List[Tuple[datetime, int]] = []
    tracked = 0
    for r in rows:
        if str(r.get('mode') or '').strip().lower() == 'manual':
            continue
        secs = int(r.get('time') or 0)
        if secs <= 0:
            continue
        tracked += secs
        st = _parse_start(r.get('start'))
        if st is not None:
            intervals.append((st, secs))

    sig = SessionSignal(tracked_minutes=round(tracked / 60, 1))
    if not intervals:
        # Tracked time but NO parseable timestamps — we cannot judge continuity,
        # so we STAY SILENT rather than treat the day as one unbroken block. If
        # Time Doctor ever drifts its `start` format, _parse_start would return
        # None for every row and the old fallback would have flagged the WHOLE
        # company at once. A real cheat cannot strip TD's timestamps, so silence
        # loses nothing (L20 — fail-silent, never fail-accusing; Fable 5).
        sig.longest_block_min = 0.0
        sig.idle_breaks = 0
        return sig

    intervals.sort(key=lambda t: t[0])
    cur_start, cur_end = intervals[0][0], intervals[0][0] + timedelta(seconds=intervals[0][1])
    longest = (cur_end - cur_start).total_seconds()
    breaks = 0
    for st, secs in intervals[1:]:
        end = st + timedelta(seconds=secs)
        gap = (st - cur_end).total_seconds()
        if gap <= IDLE_GAP_SECONDS:
            if end > cur_end:
                cur_end = end
        else:
            breaks += 1
            cur_start, cur_end = st, end
        longest = max(longest, (cur_end - cur_start).total_seconds())

    sig.longest_block_min = round(longest / 60, 1)
    sig.idle_breaks = breaks
    return sig


def _distinct_windows(rows: List[dict]) -> Optional[int]:
    """Count distinct window/app titles for ONE user's timeuse rows, WITHOUT
    keeping any title. Returns None if no title field is present (so the variety
    catcher stays silent rather than reading every row as one window)."""
    key = None
    for r in rows:
        for k in _TITLE_KEYS:
            if r.get(k):
                key = k
                break
        if key:
            break
    if key is None:
        return None
    seen = set()
    for r in rows:
        t = r.get(key)
        if t:
            seen.add(str(t).strip().lower())
    return len(seen) or None


def _classify(sig: IntegritySignal) -> None:
    """Set suspicion + reasons in place.

    A long unbroken block is NOT the spacebar cheat on its own — a genuinely busy
    developer or accountant also works long continuous stretches. What separates a
    weight-on-a-key from a hard worker is WINDOW VARIETY: the cheat is stuck in one
    or two windows; the worker hops between dozens. The live 28-Aug-2026 prod run
    proved this — the only two long-unbroken people used 200 and 54 windows and were
    real workers, not cheats. So a 'suspicious' flag REQUIRES low window variety;
    high variety exonerates; unreadable variety is a 'watch' (a human should look)."""
    long_unbroken = (sig.longest_block_min >= BLOCK_FLAG_MINUTES
                     and sig.idle_breaks <= MAX_BREAKS_FOR_FLAG)
    enough_hours = (sig.productive_hours or 0) >= MIN_PRODUCTIVE_HOURS

    if not long_unbroken:
        return
    if not enough_hours:
        # Long unbroken block but under the productive-hours floor — a light look,
        # never an accusation.
        sig.suspicion = 'watch'
        sig.reasons.append(
            f'{sig.longest_block_min/60:.1f}h unbroken (below the '
            f'{MIN_PRODUCTIVE_HOURS:.0f}h productive floor)')
        return

    dw = sig.distinct_windows
    if dw is not None and dw <= LOW_WINDOW_VARIETY:
        # Long unbroken block AND stuck in one/two windows — the actual fingerprint.
        sig.suspicion = 'suspicious'
        sig.reasons.append(
            f'{sig.longest_block_min/60:.1f}h unbroken with {sig.idle_breaks} real '
            f'break(s) AND only {dw} window(s) all day — the weight-on-a-key pattern')
    elif dw is None:
        # Can't see window variety, so we cannot tell a cheat from a busy worker —
        # flag for a human to look, not as an accusation.
        sig.suspicion = 'watch'
        sig.reasons.append(
            f'{sig.longest_block_min/60:.1f}h unbroken with {sig.idle_breaks} real '
            f'break(s); window variety unknown — worth a look')
    # else: long block but many windows → genuine focused work, left clean.


def analyze_day(users: List[dict], worklog: List[Any], timeuse: List[Any],
                *, ordered_ids: Optional[List[Any]] = None,
                productive_hours_by_uid: Optional[Dict[Any, float]] = None
                ) -> List[IntegritySignal]:
    """Per-person integrity signals for one day.

    users / worklog / timeuse are the SAME raw payloads the daily pull already
    fetches. `ordered_ids` is the request-order list of TD user ids (timeuse
    rows carry no userId, so buckets are attributed by request order, exactly as
    integrations.timedoctor.aggregate does). `productive_hours_by_uid` (keyed by
    CANONICAL user id) supplies the productive-hours gate from the same
    aggregate; without it the hours gate is simply not met and nothing is
    flagged as suspicious (only 'watch').
    """
    ordered_ids = (list(ordered_ids) if ordered_ids is not None
                   else [u.get('id') for u in (users or []) if u.get('id')])
    names = {u.get('id'): (u.get('name') or '') for u in (users or []) if u.get('id')}

    # --- session signal per TD account (per machine), from worklog ------------
    wl_by_uid: Dict[Any, List[dict]] = {}
    for r in _flatten_per_user(worklog):
        uid = r.get('userId')
        if uid is None:
            continue
        wl_by_uid.setdefault(uid, []).append(r)

    # --- window rows per TD account, from timeuse (bucket = request order) ----
    tu_by_uid: Dict[Any, List[dict]] = {}
    for idx, bucket in enumerate(timeuse or []):
        rows = bucket if isinstance(bucket, list) else [bucket]
        uid = None
        for r in rows:
            if isinstance(r, dict) and r.get('userId'):
                uid = r['userId']
                break
        if uid is None and idx < len(ordered_ids):
            uid = ordered_ids[idx]
        if uid is None:
            continue
        tu_by_uid.setdefault(uid, []).extend(r for r in rows if isinstance(r, dict))

    # --- fold machines onto the canonical person, keeping the WORST machine ---
    # (the one with the longest unbroken block — that is where a trick would run,
    # consistent with the "busiest machine, never the sum" rule).
    by_person: Dict[Any, IntegritySignal] = {}
    all_uids = set(wl_by_uid) | set(tu_by_uid) | set(names)
    for uid in all_uids:
        c_uid, c_name = canonical_identity(uid, names.get(uid, ''))
        key = str(c_uid) if c_uid is not None else (c_name or str(uid))

        ssig = _session_signal(wl_by_uid.get(uid, []))
        dwin = _distinct_windows(tu_by_uid.get(uid, []))

        tgt = by_person.get(key)
        if tgt is None:
            tgt = IntegritySignal(user_id=c_uid, name=c_name)
            by_person[key] = tgt
        else:
            tgt.machine_count += 1

        tgt.tracked_minutes += ssig.tracked_minutes
        # Keep the worst (longest unbroken, fewest breaks) machine's block.
        if ssig.longest_block_min > tgt.longest_block_min:
            tgt.longest_block_min = ssig.longest_block_min
            tgt.idle_breaks = ssig.idle_breaks
        # Fewest distinct windows across machines is the most monotonous.
        if dwin is not None:
            tgt.distinct_windows = (dwin if tgt.distinct_windows is None
                                    else min(tgt.distinct_windows, dwin))

    phours = productive_hours_by_uid or {}
    out: List[IntegritySignal] = []
    for sig in by_person.values():
        sig.productive_hours = phours.get(sig.user_id)
        _classify(sig)
        out.append(sig)

    # Most suspicious first, then longest unbroken block.
    order = {'suspicious': 0, 'watch': 1, 'clean': 2}
    out.sort(key=lambda s: (order.get(s.suspicion, 3), -s.longest_block_min))
    return out


def flagged(signals: List[IntegritySignal]) -> List[IntegritySignal]:
    """Just the ones a human should look at."""
    return [s for s in signals if s.suspicion in ('suspicious', 'watch')]
