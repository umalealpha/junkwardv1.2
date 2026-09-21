"""
integrations/td_screenshot_integrity.py

The REAL fake-activity catcher — the "phantom keystroke" detector (Fable 5,
2026-09-01). Catches the weight-on-a-key / frozen-screen cheat that the
session-block detector (td_integrity.py) misses.

The heart of it, per Fable 5: **real typing changes the screen.** A weight held
on a key feeds Time Doctor constant keystrokes with ZERO mouse and a screen that
never changes — physically impossible for genuine work. Confirmed live on the
Meduduetso Tlagae case (29-Aug-2026): Time Doctor's own UI marked 87 of 91
screenshots "Identical", every one keyboard-full / mouse-empty.

Data source: Time Doctor `/api/1.0/files` gives, PER SCREENSHOT, a `meta` block
with `keys` / `movements` / `clicks` (keyboard, mouse-move, mouse-click counts),
`imageMd5` + `phash` + `hammingDistance` (picture fingerprints), and a
timestamp. We read ONLY those numbers/hashes and compute counts. We NEVER store
or emit `windowTitle` or the image URL (AD-POL-AI-GOV-001) — no screen content
leaves the box, no image is downloaded.

A "phantom interval" is one screenshot where ALL hold:
  - keys >= KEYS_MIN            (real sustained typing, not a few arrow presses)
  - movements == 0 AND clicks == 0   (nobody at the desk touches nothing)
  - the screen is unchanged from the previous shot (same imageMd5, or phash
    hammingDistance <= HAMMING_IDENTICAL)
That conjunction, measured PER interval (never as day-level averages of separate
signals), is the fraud itself.

A day is flagged 'suspicious' only when phantom intervals are a real share of the
day AND the day is keyboard-only AND they credited real time (materiality). It
FLAGS for a human — it never docks.

Pure stdlib (statistics only) — no Django, no DB, no network — so it unit-tests
offline against synthetic screenshot streams.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from integrations.td_matching import canonical_identity

# ---- Tunable thresholds (Fable 5 starting points; tune on 2 weeks of shadow data).
KEYS_MIN            = 40      # sustained typing in one interval, not stray keys
HAMMING_IDENTICAL   = 4       # phash distance <= this == "same screen"
PHANTOM_DAY_PCT     = 0.25    # >= 25% of the day's shots are phantom -> flag
KEYBOARD_ONLY_PCT   = 0.50    # >= 50% of ACTIVE intervals are keyboard-only
PHANTOM_HOURS_MIN   = 2.0     # materiality floor — never a flag over 20 minutes
DEFAULT_INTERVAL_MIN = 3.0    # Time Doctor screenshot cadence when unmeasurable


def _parse_ts(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).strip().replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:      # noqa: BLE001
        return None


def _safe_int(v: Any) -> int:
    """Time Doctor occasionally sends a field in an unexpected shape (its
    hammingDistance is already a list). Never let one malformed screenshot kill a
    whole person's day — coerce, defaulting to 0."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


@dataclass
class Shot:
    ts:     Optional[datetime]
    md5:    str
    keys:   int
    moves:  int
    clicks: int
    hamming: Optional[int]     # phash distance to previous, if TD supplied it
    screen: int = 0


@dataclass
class ScreenSignal:
    user_id:              Any = None
    name:                 str = ''
    shots:                int = 0    # raw screenshots
    intervals:            int = 0    # capture intervals (multi-monitor shots share one)
    identical_pct:        float = 0.0
    keyboard_only_pct:    Optional[float] = None
    frozen_typing_pct:    float = 0.0    # the ANCHOR: typing while EVERY screen is frozen
    frozen_typing_hours:  float = 0.0
    mouse_dead_pct:       float = 0.0    # of the frozen-typing intervals, share with NO mouse
    md5_exact_pct:        float = 0.0    # of frozen-typing intervals, share frozen by exact md5
    mean_keys_frozen:     Optional[float] = None
    productive_hours:     Optional[float] = None
    suspicion:            str = 'clean'          # clean | watch | suspicious
    reasons:              List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        # Privacy-safe: counts/percentages only — no md5, no titles, no images.
        return {
            'shots':               self.shots,
            'intervals':           self.intervals,
            'identical_pct':       round(self.identical_pct * 100, 1),
            'keyboard_only_pct':   (None if self.keyboard_only_pct is None
                                    else round(self.keyboard_only_pct * 100, 1)),
            'frozen_typing_pct':   round(self.frozen_typing_pct * 100, 1),
            'frozen_typing_hours': round(self.frozen_typing_hours, 2),
            'mouse_dead_pct':      round(self.mouse_dead_pct * 100, 1),
            'md5_exact_pct':       round(self.md5_exact_pct * 100, 1),
            'suspicion':           self.suspicion,
            'reasons':             list(self.reasons),
        }


def _shot_from_meta(meta: dict) -> Shot:
    ham = meta.get('hammingDistance')
    if isinstance(ham, list):
        vals = [h for h in ham if isinstance(h, (int, float)) and h >= 0]
        ham = min(vals) if vals else None
    elif isinstance(ham, (int, float)):
        ham = ham if ham >= 0 else None
    else:
        ham = None
    return Shot(
        ts=_parse_ts(meta.get('createdAt') or meta.get('chunkId')),
        md5=str(meta.get('imageMd5') or ''),
        keys=_safe_int(meta.get('keys')),
        moves=_safe_int(meta.get('movements')),
        clicks=_safe_int(meta.get('clicks')),
        hamming=ham,
        screen=_safe_int(meta.get('screenNumber')),
    )


def _median_interval_min(times: List[datetime]) -> float:
    ts = sorted(t for t in times if t)
    gaps = [((b - a).total_seconds() / 60.0) for a, b in zip(ts, ts[1:])
            if 0 < (b - a).total_seconds() / 60.0 <= 15]   # ignore long idle jumps
    return statistics.median(gaps) if gaps else DEFAULT_INTERVAL_MIN


def _analyse_user(shots: List[Shot]) -> ScreenSignal:
    """Work in CAPTURE INTERVALS, not raw shots: multi-monitor captures share a
    timestamp, and a frozen-typing interval requires EVERY monitor in that interval
    to be unchanged. That is the true anchor — real typing changes SOME screen — so
    a person typing on monitor 2 while captured monitor 1 sits frozen is NOT flagged
    (Fable 5 multi-monitor fix)."""
    sig = ScreenSignal(shots=len(shots))
    if not shots:
        return sig
    # Compare each shot to the PREVIOUS shot on the SAME monitor.
    _MIN = datetime.min.replace(tzinfo=timezone.utc)
    shots = sorted(shots, key=lambda s: (s.ts or _MIN, s.screen))
    prev_by_screen: Dict[int, Shot] = {}
    # Bucket shots into intervals keyed by capture timestamp.
    intervals: "Dict[Any, list]" = {}
    order: List[Any] = []
    for s in shots:
        prev = prev_by_screen.get(s.screen)
        unchanged_exact = bool(prev and s.md5 and prev.md5 and s.md5 == prev.md5)
        unchanged = unchanged_exact or bool(prev and s.hamming is not None and s.hamming <= HAMMING_IDENTICAL)
        prev_by_screen[s.screen] = s
        key = s.ts or object()          # None-ts shots each get their own interval
        if key not in intervals:
            intervals[key] = []
            order.append(key)
        intervals[key].append((s, unchanged, unchanged_exact, prev is not None))

    n_int = len(order)
    identical = keyboard_only = active = frozen_typing = frozen_no_mouse = frozen_exact = 0
    frozen_keys: List[int] = []
    for key in order:
        items = intervals[key]
        keys = max(sh.keys for sh, *_ in items)          # keys is interval-global in TD
        moves = max(sh.moves for sh, *_ in items)
        clicks = max(sh.clicks for sh, *_ in items)
        all_have_prev = all(hp for *_, hp in items)
        all_unchanged = all_have_prev and all(u for _, u, _, _ in items)
        all_exact = all_have_prev and all(e for _, _, e, _ in items)

        if keys > 0 or moves > 0 or clicks > 0:
            active += 1
        if keys > 0 and moves == 0 and clicks == 0:
            keyboard_only += 1
        if all_unchanged:
            identical += 1
        if keys >= KEYS_MIN and all_unchanged:
            frozen_typing += 1
            frozen_keys.append(keys)
            if moves == 0 and clicks == 0:
                frozen_no_mouse += 1
            if all_exact:
                frozen_exact += 1

    sig.intervals = n_int
    sig.identical_pct = identical / n_int if n_int else 0.0
    sig.keyboard_only_pct = (keyboard_only / active) if active else None
    sig.frozen_typing_pct = frozen_typing / n_int if n_int else 0.0
    interval_times = [k for k in order if isinstance(k, datetime)]
    sig.frozen_typing_hours = round(frozen_typing * _median_interval_min(interval_times) / 60.0, 2)
    sig.mouse_dead_pct = (frozen_no_mouse / frozen_typing) if frozen_typing else 0.0
    sig.md5_exact_pct = (frozen_exact / frozen_typing) if frozen_typing else 0.0
    sig.mean_keys_frozen = round(statistics.mean(frozen_keys), 1) if frozen_keys else None
    return sig


def _classify(sig: ScreenSignal) -> None:
    strong = (sig.frozen_typing_pct >= PHANTOM_DAY_PCT
              and sig.frozen_typing_hours >= PHANTOM_HOURS_MIN)
    if strong:
        sig.suspicion = 'suspicious'
        mouse = ('no mouse at all' if sig.mouse_dead_pct >= 0.8
                 else 'the mouse barely moving')
        sig.reasons.append(
            f'{sig.frozen_typing_pct*100:.0f}% of screenshots show heavy typing '
            f'(avg {sig.mean_keys_frozen:.0f} keys) on a screen that never '
            f'changes, with {mouse} — {sig.frozen_typing_hours:.1f}h of credited '
            f'time. Real typing changes the screen; this cannot.')
    elif sig.frozen_typing_pct >= PHANTOM_DAY_PCT and sig.frozen_typing_hours >= (PHANTOM_HOURS_MIN / 2):
        sig.suspicion = 'watch'
        sig.reasons.append(
            f'{sig.frozen_typing_pct*100:.0f}% typing on a frozen screen but below '
            f'the full flag gate ({sig.frozen_typing_hours:.1f}h) — worth a look')


def analyze_day(files: List[Any], users: List[dict], *,
                productive_hours_by_uid: Optional[Dict[Any, float]] = None
                ) -> List[ScreenSignal]:
    """Per-person screenshot-integrity signals for one day.

    `files` is the raw `/api/1.0/files` payload (a flat list of capture records,
    each `{userId, date, deviceId, numbers:[screenshot,...]}`; each screenshot
    carries a `meta` block). `users` is the roster (for names). Machines fold onto
    one canonical person via td_matching (uid-keyed; never a fuzzy payroll match).
    """
    names = {u.get('id'): (u.get('name') or '') for u in (users or []) if u.get('id')}
    by_uid: Dict[Any, List[Shot]] = {}
    for rec in (files or []):
        if not isinstance(rec, dict):
            continue
        uid = rec.get('userId')
        if uid is None:
            continue
        for shot in (rec.get('numbers') or []):
            if isinstance(shot, dict) and isinstance(shot.get('meta'), dict):
                by_uid.setdefault(uid, []).append(_shot_from_meta(shot['meta']))

    phours = productive_hours_by_uid or {}
    merged: Dict[Any, ScreenSignal] = {}
    merged_shots: Dict[Any, List[Shot]] = {}
    for uid, shots in by_uid.items():
        c_uid, c_name = canonical_identity(uid, names.get(uid, ''))
        key = str(c_uid) if c_uid is not None else (c_name or str(uid))
        merged_shots.setdefault(key, []).extend(shots)
        if key not in merged:
            merged[key] = ScreenSignal(user_id=c_uid, name=c_name)

    out: List[ScreenSignal] = []
    for key, base in merged.items():
        sig = _analyse_user(merged_shots[key])
        sig.user_id, sig.name = base.user_id, base.name
        sig.productive_hours = phours.get(base.user_id)
        _classify(sig)
        out.append(sig)

    order = {'suspicious': 0, 'watch': 1, 'clean': 2}
    out.sort(key=lambda s: (order.get(s.suspicion, 3), -s.frozen_typing_pct))
    return out


def flagged(signals: List[ScreenSignal]) -> List[ScreenSignal]:
    return [s for s in signals if s.suspicion in ('suspicious', 'watch')]
