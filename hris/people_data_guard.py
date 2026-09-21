"""
hris/people_data_guard.py

THE reusable "settle + cross-check" guardrail for people-facing automation
(CFO 2026-08-01). One gate, reused by every job that judges a person from
machine data before a human sees it: the Time Doctor exceptions report, the
daily leave-excuse feed, leave auto-apply, and (as a pre-release CHECK only,
never an auto-action) payroll.

Why it exists: Time Doctor delivers some people's hours LATE — the desktop app
uploads in delayed batches when a machine syncs. A single early snapshot then
judges a day before its data has arrived and wrongly reports "did not track".
That false zero used to flow straight into manager escalation, the excuse feed,
leave, and Development Dialogue. "This is people, we can't make mistakes here."

The gate, in series — a candidate is only REPORTED/ACTED-ON if it passes BOTH:

  Gate 1  SETTLE (deterministic).  The same day is pulled several times
          (03:00 / 04:00 / 08:30). A person's figure is only trusted when it
          has stopped moving across the last two pulls AND, for a zero, their
          own recent history doesn't scream "normally tracks — this is late
          data, not an absence".

  Gate 2  AI CROSS-CHECK (DeepSeek + Gemini, server-side, off the Claude bill,
          PII-firewalled — core.ai_assist). The AI is a VETO, not an authoriser:
          a candidate that reached this gate has already survived the settle gate
          (so it's a chronic non-tracker or a fully-settled zero, NOT a habitual
          tracker with a lone zero). HOLD only when there is a POSITIVE reason —
          an engine says "likely_late" — OR both engines are down (fail-safe).
          "genuine", "uncertain", or an engine that didn't mention the person all
          REPORT. Requiring a positive "genuine" instead over-held real absentees
          and emptied the report (live 2026-08-01); the settle gate is the real
          false-accusation guard and the next-day correction is the backstop.

FAIL-SAFE: if a pull is missing the settle gate holds; if BOTH AI engines are
down (or return nothing usable) the candidate is HELD, never reported. Better to
leave a name off than to wrongly accuse. (CFO: hold the name.)

This module holds the PURE, unit-testable decision logic at the top (no Django
imports) and the AI call (lazy Django import) below, so the core can be proven
without the app or a live model.
"""
from __future__ import annotations

import datetime
import logging

log = logging.getLogger(__name__)

# ── tuning (overridable via settings in the orchestrator) ────────────────────
SETTLE_TOLERANCE_SEC = 60      # figures within 1 min across pulls count as "not moving"
SUSPICIOUS_TRACK_RATIO = 0.6   # tracked >=60% of recent working days → a fresh 0 is suspect
HISTORY_MIN_DAYS = 3           # need at least this many history days to trust the ratio
SETTLED_MIN_SLOTS = 3          # a zero is only "fully settled" once ALL THREE pulls are in
                               # (03:00/04:00/08:30). Two early pulls agreeing at zero is
                               # NOT settled — everyone's PC is still off at 3–4am; the
                               # 08:30 post-boot-up pull is the one that catches late data.


# ── Gate 0: FACTS (pure) ─────────────────────────────────────────────────────
# Added CFO 2026-08-03, after Oratile Ria Tlhomelang. The two gates below ask
# "is this Time Doctor figure FINAL?" — a data-freshness question. They never ask
# "does Omni already hold an EXPLANATION for this day?" Her zero hours were
# perfectly real and perfectly settled; what was wrong was that nobody checked
# the leave register before putting her name in a manager's inbox. A settle gate
# cannot catch that, and neither can an AI looking only at hours.
#
# So this gate runs FIRST and is DETERMINISTIC — it reads records, it does not
# judge. A person who is already explained never reaches the settle gate, never
# costs an AI call, and can never be cleared by an AI verdict.
#
# `facts` is what Omni holds about ONE person on ONE day; every key is optional
# and a missing key means "not known", which never holds a name (absence of a
# record is not evidence either way — same principle as dark_working_streak
# skipping days it has no record for):
#   required_hours  float  hours Omni actually owed them that day
#   day_status      str    WorkdayJustification.status for the day
#   leave           str    plain description of leave covering the day, if any
#   client_visit    str    plain description of a logged visit, if any
#   public_holiday  str    holiday name, if the day is one
#
# Leave at ANY status except refused/cancelled counts: a pending application is
# still notice that the person told us. This matches the shield in
# manager_accountability.dark_reports_by_manager.

# Statuses that mean the day is already accounted for. 'justified' is approved
# leave / an approved visit / a manager-signed explanation; 'explained' is
# self-reported and awaiting that sign-off — both mean somebody has answered, so
# neither is an unexplained absence.
_SETTLED_DAY_STATUSES = ('met', 'justified', 'explained', 'not_required')


def facts_gate(facts: dict, day=None) -> tuple[bool, str]:
    """Deterministic first gate: has this day already been explained?

    Returns (passed, reason). passed=False → HOLD, and the reason NAMES the
    record that explains the day, so the log says why a name was withheld.
    """
    facts = facts or {}

    required = facts.get('required_hours')
    if required is not None:
        try:
            if float(required) <= 0:
                return False, 'Omni owed them no hours that day (off day / rota / holiday)'
        except (TypeError, ValueError):
            pass

    holiday = (facts.get('public_holiday') or '').strip()
    if holiday:
        return False, f'public holiday on record: {holiday}'

    leave = (facts.get('leave') or '').strip()
    if leave:
        return False, f'leave on record: {leave}'

    visit = (facts.get('client_visit') or '').strip()
    if visit:
        return False, f'client visit on record: {visit}'

    status = (facts.get('day_status') or '').strip().lower()
    if status in _SETTLED_DAY_STATUSES:
        return False, f'the day is already recorded as "{status}"'

    return True, 'nothing on record explains the day'


# ── Gate 0 collector (Django) ────────────────────────────────────────────────

def facts_for_profiles(profile_ids, day) -> dict:
    """{profile_id: facts} for facts_gate, read straight from Omni's records.

    One query per record type for the whole batch — never per person, so wiring
    this into a job that scans ~100 staff costs five queries, not five hundred.
    Tolerant: any read problem returns {} for that record type, which means
    "not known" and therefore never holds a name on its own. The settle + AI
    gates still run behind it.
    """
    out = {pid: {} for pid in profile_ids}
    if not profile_ids:
        return out

    from hris.models import ClientVisit, LeaveRequest, PublicHoliday, WorkdayJustification

    # Leave — ANY status except refused/cancelled. A pending application is still
    # the person having told us; mirrors the shield in manager_accountability.
    try:
        for lr in (LeaveRequest.objects
                   .filter(profile_id__in=profile_ids,
                           start_date__lte=day, end_date__gte=day)
                   .exclude(status__in=[LeaveRequest.Status.REFUSED,
                                        LeaveRequest.Status.CANCELLED])
                   .select_related('leave_type')):
            # Leave TYPE is deliberately not recorded here — sick / maternity
            # leave is health data (AD-POL-AI-GOV-001) and the gate only needs to
            # know that leave EXISTS. The status matters (approved vs applied-for),
            # the reason never does.
            out.setdefault(lr.profile_id, {})['leave'] = (
                f'{lr.get_status_display().lower()}, {lr.start_date} to {lr.end_date}')
    except Exception:    # noqa: BLE001
        log.exception('facts_for_profiles: leave lookup failed for %s', day)

    try:
        for cv in ClientVisit.objects.filter(profile_id__in=profile_ids, visit_date=day):
            out.setdefault(cv.profile_id, {})['client_visit'] = f'logged for {day}'
    except Exception:    # noqa: BLE001
        log.exception('facts_for_profiles: client-visit lookup failed for %s', day)

    try:
        for j in WorkdayJustification.objects.filter(profile_id__in=profile_ids, work_date=day):
            f = out.setdefault(j.profile_id, {})
            f['day_status'] = j.status
            f['required_hours'] = float(j.required_hours or 0)
    except Exception:    # noqa: BLE001
        log.exception('facts_for_profiles: workday lookup failed for %s', day)

    try:
        hol = (PublicHoliday.objects
               .filter(holiday_date=day, is_active=True, country_code='BW')
               .first())
        # is_working_day means staff ARE expected to work it (an unpaid holiday),
        # so it explains nothing — only a genuine day off does.
        if hol is not None and not hol.is_working_day:
            for pid in profile_ids:
                out.setdefault(pid, {})['public_holiday'] = hol.name
    except Exception:    # noqa: BLE001
        log.exception('facts_for_profiles: public-holiday lookup failed for %s', day)

    return out


def facts_by_td_uid(employee_for_uid: dict, day) -> dict:
    """{td_uid: facts} for a job that already holds a uid → Employee map
    (TDMatcher.employee_for_uid). Employees with no HRIS profile simply get {} —
    unknown, which never holds a name by itself."""
    if not employee_for_uid:
        return {}
    try:
        from hris.models import HRISProfile
        emp_ids = [getattr(e, 'id', None) for e in employee_for_uid.values()]
        prof_by_emp = {p.employee_id: p.id for p in
                       HRISProfile.objects.filter(employee_id__in=[e for e in emp_ids if e])}
        by_profile = facts_for_profiles(list(prof_by_emp.values()), day)
        return {uid: by_profile.get(prof_by_emp.get(getattr(emp, 'id', None)), {})
                for uid, emp in employee_for_uid.items()}
    except Exception:    # noqa: BLE001
        log.exception('facts_by_td_uid failed for %s — no facts, other gates still run', day)
        return {}


# ── Gate 1: SETTLE (pure) ────────────────────────────────────────────────────
# `samples` is an ORDERED dict-like: {slot_label: {uid: seconds}}, oldest first.
# slot_label is just a tag ("0300", "0400", "0830"); order is insertion order.

def _ordered_slots(samples: dict) -> list:
    """Slot labels oldest → newest. Sorted, NOT dict order: samples come from a
    Postgres jsonb field which does not preserve insertion order. Slot labels are
    zero-padded HHMM (0300/0400/0830), so lexicographic sort == chronological."""
    return sorted(samples.keys())


def slot_seconds(samples: dict, uid: str, slot: str) -> int:
    return int((samples.get(slot) or {}).get(uid, 0) or 0)


def last_seconds(samples: dict, uid: str) -> int:
    """The freshest captured figure for a person (the number we'd report)."""
    slots = _ordered_slots(samples)
    return slot_seconds(samples, uid, slots[-1]) if slots else 0


def is_settled(samples: dict, uid: str, tol: int = SETTLE_TOLERANCE_SEC) -> bool:
    """A person's figure has STOPPED MOVING: the last two captured pulls agree
    within `tol` seconds. With fewer than two pulls we cannot know it settled,
    so we say NOT settled (fail-safe)."""
    slots = _ordered_slots(samples)
    if len(slots) < 2:
        return False
    a = slot_seconds(samples, uid, slots[-2])
    b = slot_seconds(samples, uid, slots[-1])
    return abs(a - b) <= tol


def zero_across_all_slots(samples: dict, uid: str) -> bool:
    slots = _ordered_slots(samples)
    return bool(slots) and all(slot_seconds(samples, uid, s) == 0 for s in slots)


def tracked_ratio(history_secs: dict) -> float | None:
    """Fraction of recent working days on which the person tracked ANY time.
    `history_secs` is {date: seconds}. Returns None when there's too little
    history to judge (so a new joiner's zero is never called 'suspicious')."""
    if not history_secs or len(history_secs) < HISTORY_MIN_DAYS:
        return None
    tracked = sum(1 for v in history_secs.values() if (v or 0) > 0)
    return tracked / len(history_secs)


def settle_gate(uid: str, samples: dict, history_secs: dict,
                low_threshold_sec: int, is_zero: bool = False,
                suspicious_ratio: float = SUSPICIOUS_TRACK_RATIO,
                tol: int = SETTLE_TOLERANCE_SEC) -> tuple[bool, str]:
    """Deterministic first gate for a person the base report WANTS to flag
    (zero or below `low_threshold_sec`). `is_zero` is the caller's own
    classification of the live figure, so the gate still protects a no-track
    person on a day that only has ONE pull captured so far (early rollout, or a
    missed pull) — the moving-check needs two pulls, but the suspicious-zero
    check does not.

    Returns (passed, reason). passed=True → trustworthy enough to hand to the AI
    gate; passed=False → HOLD now (do not report/act)."""
    slots = _ordered_slots(samples)
    if len(slots) >= 2 and not is_settled(samples, uid, tol):
        return False, 'data still arriving (figure changed between the last two pulls)'
    zero = is_zero or (bool(slots) and zero_across_all_slots(samples, uid))
    if zero:
        # A zero is "fully settled" only when all three pulls are in AND agree at
        # zero — including the 08:30 post-boot-up pull. Until then a strong-history
        # zero is HELD (likely late/stuck, the Keetile/Wangu case). Once fully
        # settled at zero, we DON'T auto-hold on history — a genuine absence by
        # someone who normally tracks must still be able to surface, so it's handed
        # to the AI gate to judge (a post-08:30 late upload is then caught by the
        # next-day correction, not by hiding the name forever).
        fully_settled = (len(slots) >= SETTLED_MIN_SLOTS
                         and is_settled(samples, uid, tol)
                         and zero_across_all_slots(samples, uid))
        if not fully_settled:
            ratio = tracked_ratio(history_secs)
            if ratio is not None and ratio >= suspicious_ratio:
                pct = round(ratio * 100)
                return False, (f'zero today but tracked on {pct}% of recent days — '
                               f'likely a late/stuck sync, not an absence')
    return True, 'settled and consistent'


# ── Gate 2: AI CROSS-CHECK (lazy Django import) ──────────────────────────────

def _ai_context_line(name: str, samples: dict, uid: str, history_secs: dict) -> str:
    """One privacy-safe line per candidate for the AI: name + hours only, no
    raw titles, no IDs beyond the display name (which the manager email already
    shows). Everything still passes core.ai_assist's PII firewall downstream."""
    slots = _ordered_slots(samples)
    pulls = ', '.join(f'{s}={round(slot_seconds(samples, uid, s)/3600, 2)}h' for s in slots)
    hist = sorted(history_secs.items())
    hline = ', '.join(f'{d}={round((v or 0)/3600, 1)}h' for d, v in hist)
    return f'- {name}: pulls[{pulls}] recent[{hline}]'


def ai_cross_check(candidates: list, samples: dict, history_by_uid: dict,
                   day: datetime.date) -> dict:
    """Ask DeepSeek AND Gemini, independently, whether each candidate's low/zero
    day is a GENUINE absence or LIKELY LATE DATA. Returns {uid: (ok, reason)}
    where ok=True means 'report'. HOLD (ok=False) only when there is a POSITIVE
    reason: an engine positively answers 'likely_late', or BOTH engines are down
    / return nothing usable (fail-safe). 'genuine', 'uncertain', or an engine that
    simply didn't mention the person REPORT — the settle gate is the real
    false-accusation guard and already ran; see _combine_ai. candidates: [{uid,
    name}]. Duplicate names are held outright (can't be attributed)."""
    if not candidates:
        return {}

    # Duplicate full names cannot be attributed from an AI reply keyed on name
    # (the roster has known dup names — the callers are uid-safe for exactly this
    # reason). HOLD every holder of a duplicated name outright rather than risk
    # cross-attributing a "genuine" verdict to a late-data twin.
    from collections import Counter
    name_counts = Counter((c['name'] or '').strip().lower() for c in candidates)
    dup_names = {n for n, ct in name_counts.items() if ct > 1}
    result = {c['uid']: (False, 'held — duplicate name, cannot attribute AI verdict')
              for c in candidates if (c['name'] or '').strip().lower() in dup_names}
    uniq = [c for c in candidates if (c['name'] or '').strip().lower() not in dup_names]
    if not uniq:
        return result

    try:
        from core.ai_assist import deepseek_complete, gemini_complete, is_safe_for_ai
    except Exception:   # noqa: BLE001 — app not importable (e.g. pure unit context)
        for c in uniq:
            result[c['uid']] = (False, 'AI cross-check unavailable — name held')
        return result

    # Each candidate gets a numeric id the model echoes back — we match on the ID,
    # never on the name the model retypes (a retyped/abbreviated name silently
    # missed and dropped EVERYONE to 'hold', emptying the report — caught on the
    # first live run 2026-08-01).
    lines = [f'[{i}] ' + _ai_context_line(c['name'], samples, c['uid'],
                                          history_by_uid.get(c['uid'], {}))
             for i, c in enumerate(uniq)]
    prompt = (
        'You are auditing a workforce time-tracking report before it is emailed to managers. '
        'Each person below (numbered [N]) was about to be flagged as "did not track / very low '
        f'hours" for {day:%A %d %b %Y}. For EACH, decide whether this is a GENUINE absence/low '
        'day or LIKELY LATE OR STUCK TRACKING DATA (their tracker uploaded late). Late data '
        'looks like a strong recent tracking history with a sudden lone zero, or figures still '
        'changing between pulls; someone who rarely tracks at all is GENUINE, not late data. '
        'Reply ONLY as JSON keyed by the [N] number: '
        '{"verdicts":[{"id":N,"verdict":"genuine|likely_late|uncertain"}]}\n\n'
        + '\n'.join(lines)
    )

    safety = is_safe_for_ai(prompt)     # SafetyReport(safe=..., redacted_text=...)
    if not safety.safe:
        for c in uniq:
            result[c['uid']] = (False, 'held — could not safely share for AI check')
        return result
    if not safety.redacted_text:                 # nothing safe left to send → HOLD, never send raw
        for c in uniq:
            result[c['uid']] = (False, 'held — nothing safe to send for AI check')
        return result
    send_text = safety.redacted_text             # send the REDACTED text, never the raw prompt

    def _run(engine):
        try:
            import json
            data = json.loads(engine(send_text, response_format='json_object', max_tokens=1200))
            out = {}
            for v in (data.get('verdicts') or []):
                try:
                    out[int(v.get('id'))] = (v.get('verdict') or 'uncertain').strip().lower()
                except (TypeError, ValueError):
                    continue
            # An empty verdict map = the engine gave us NOTHING usable (e.g. it
            # didn't echo the [id]s) = treat it as DOWN, not as "ran and cleared
            # everyone". Otherwise a permanently-empty engine hides a real outage
            # of the other one and clears names with no check (Fable review).
            return out or None
        except Exception:   # noqa: BLE001
            return None

    ds = _run(deepseek_complete)
    gm = _run(gemini_complete)
    for i, c in enumerate(uniq):
        result[c['uid']] = _combine_ai(ds.get(i) if ds else None,
                                       gm.get(i) if gm else None,
                                       ds is None and gm is None)
    return result


def _combine_ai(dverdict, gverdict, both_engines_down):
    """(ok, reason) from the two engines' verdicts for one person. HOLD only when
    there is a POSITIVE reason to: both engines failed to run (fail-safe — CFO's
    'if the check can't run, hold the name'), or an engine POSITIVELY flags
    'likely_late'. Everything else — genuine, uncertain, or an engine that just
    didn't mention this person — REPORTS, because the settle gate has already held
    the habitual-tracker-with-a-lone-zero cases and the next-day correction is the
    backstop. Requiring a positive 'genuine' instead over-held real absentees and
    emptied the report (live 2026-08-01)."""
    if both_engines_down:
        return False, 'AI cross-check unavailable — name held'
    if dverdict == 'likely_late' or gverdict == 'likely_late':
        parts = [f'{e}={v}' for e, v in (('DeepSeek', dverdict), ('Gemini', gverdict)) if v]
        return False, 'held — AI flagged likely-late data: ' + ', '.join(parts)
    return True, f'cleared (DeepSeek={dverdict or "n/a"}, Gemini={gverdict or "n/a"})'


# ── Orchestrator ─────────────────────────────────────────────────────────────

def guard_candidates(candidates: list, samples: dict, history_by_uid: dict,
                     day: datetime.date, low_threshold_sec: int = 0,
                     use_ai: bool = True, facts_by_uid: dict | None = None) -> dict:
    """Run all three gates in series over the people the base report would flag.

    candidates: [{uid, name, is_zero}] — the would-be "did not track"/low list;
    is_zero flags a no-track (vs merely low) so the settle gate protects it even
    with a single pull captured.
    facts_by_uid: {uid: facts} for the FACTS gate (see facts_gate / facts_by_td_uid).
    Omitted → no facts known, so gate 0 holds nobody and behaviour is exactly as
    before; callers that can supply it should, because it is the only gate that
    catches "the hours are right but the person is on leave".
    Returns {uid: {'action': 'report'|'hold', 'reason': str, 'name': str}}.
    A candidate must PASS the facts gate, the settle gate AND the AI gate to be
    'report'.
    """
    facts_by_uid = facts_by_uid or {}
    settled_pass, decisions = [], {}
    for c in candidates:
        # Gate 0 FIRST — an explained day is never an accusation, whatever the
        # hours say and whatever an AI thinks of them.
        ok, reason = facts_gate(facts_by_uid.get(c['uid']), day)
        if not ok:
            decisions[c['uid']] = {'action': 'hold', 'reason': reason, 'name': c['name']}
            continue
        ok, reason = settle_gate(c['uid'], samples, history_by_uid.get(c['uid'], {}),
                                 low_threshold_sec, is_zero=c.get('is_zero', False))
        if ok:
            settled_pass.append(c)
        else:
            decisions[c['uid']] = {'action': 'hold', 'reason': reason, 'name': c['name']}

    ai = ai_cross_check(settled_pass, samples, history_by_uid, day) if use_ai else \
        {c['uid']: (True, 'ai skipped') for c in settled_pass}

    for c in settled_pass:
        ok, reason = ai.get(c['uid'], (False, 'AI cross-check unavailable — name held'))
        decisions[c['uid']] = {'action': 'report' if ok else 'hold',
                               'reason': reason, 'name': c['name']}
    return decisions


def held_decisions(pairs, day, client=None, low_threshold_sec: int = 0) -> dict:
    """One-call wrapper for every OTHER people-facing job (CFO 2026-08-01: the
    guardrail applies to ALL communications, not just the 09:00 report and the
    morning brief).

    `pairs` is [(td_uid, name)] the job is about to name or chase. Returns the
    {uid: decision} map; callers normally want
    `{u for u, d in ... if d['action'] == 'hold'}`.

    Pulls the settle samples + recent history itself, honours the
    WORKFORCE_DATA_GUARD_ENABLED / _AI settings, and falls back to the settle
    gate alone if the AI path raises. Fail-safe throughout: any breakage HOLDS
    the name rather than letting a late upload become an accusation.
    """
    from django.conf import settings

    pairs = [(str(u), n) for u, n in pairs if u]
    if not pairs:
        return {}
    if not getattr(settings, 'WORKFORCE_DATA_GUARD_ENABLED', True):
        return {u: {'action': 'report', 'reason': 'guard disabled', 'name': n} for u, n in pairs}

    cands = [{'uid': u, 'name': n, 'is_zero': True} for u, n in pairs]
    all_held = {c['uid']: {'action': 'hold', 'name': c['name'],
                           'reason': 'guardrail could not run — name held'} for c in cands}
    try:
        from hris import exceptions_report
        cl = client
        if cl is None:
            from integrations.timedoctor import TimeDoctorClient
            cl = TimeDoctorClient.from_settings()
        samples, hist = exceptions_report._guard_history_and_samples(cl, day)
    except Exception:    # noqa: BLE001 — no inputs = nothing proven = hold everyone
        # LOUD: a permanently broken guard would otherwise look like a very quiet
        # day forever — every name held, nobody told why (same pattern as
        # eligibility.on_leave_names).
        log.exception('people-data guard could not read its inputs for %s — '
                      'holding all %d name(s)', day, len(cands))
        return all_held
    try:
        return guard_candidates(cands, samples, hist, day, low_threshold_sec=low_threshold_sec,
                                use_ai=getattr(settings, 'WORKFORCE_DATA_GUARD_AI', True))
    except Exception:    # noqa: BLE001 — an AI-path bug must not un-guard a job
        log.exception('people-data guard: AI cross-check path failed for %s — '
                      'falling back to the settle gate alone', day)
        try:
            return guard_candidates(cands, samples, hist, day,
                                    low_threshold_sec=low_threshold_sec, use_ai=False)
        except Exception:    # noqa: BLE001 — settle gate broken too: hold, never accuse
            log.exception('people-data guard: settle gate ALSO failed for %s — '
                          'holding all %d name(s)', day, len(cands))
            return all_held


def held_uids(pairs, day, client=None, low_threshold_sec: int = 0) -> set:
    """The uids `held_decisions` says must NOT be named in a communication."""
    return {u for u, d in held_decisions(pairs, day, client=client,
                                         low_threshold_sec=low_threshold_sec).items()
            if d.get('action') == 'hold'}


# ── THE MIRROR GATE: hours that belong to NOBODY (TD-ORPHAN-01) ──────────────
#
# Every gate above answers ONE question — "are we about to accuse someone
# unfairly?" — so every one of them fails safe by going QUIET: hold the name,
# say nothing. That is correct when the risk is a false accusation.
#
# Bug 5dffc022 (N. Nthite, 3-Sep-2026) was the mirror image, and it walked
# straight through all of them. Nobody accused her. Her Time Doctor client
# re-registered under her machine's Windows SID, so her real 5.73 h landed on an
# account that belonged to no one. Omni reconciles people -> hours and has never
# reconciled hours -> people, so 18.29 h of recorded work sat on unowned accounts
# that day and nothing raised a hand. She found it herself, two days later.
#
# A guard that fails safe by going quiet cannot catch a bug whose only symptom
# IS quiet. So this gate balances the other side of the books, and its fail
# direction is INVERTED: when the AI is unavailable, or an engine dies, or
# nothing can be sent, it RAISES EVERY ROW ANYWAY. Silence is the failure mode
# being guarded against, so silence can never be this gate's fallback.
#
# The AI's role also flips. Above it is a VETO that can withhold a name. Here it
# may only ADD a suggested owner so a human can confirm the link in one click —
# it can never remove a row from the raise list.
#
# And the roster handed to the AI must include people who ALREADY have a
# tracker. That was the precise blind spot: ghost_payroll.ai_screen_ghosts only
# ever offered payroll people with NO Time Doctor account, and she had one, so
# her orphan account was never compared against her.

ORPHAN_MIN_SECONDS = 0     # every second of lost work counts unless a caller says otherwise


def owned_td_uids(extra=None) -> set:
    """Every Time Doctor uid that BELONGS to somebody, as a fact.

    Ownership is a map row pointing at an employee — NOT whether the day's
    report happened to resolve the account. The first live run of the orphan
    gate built this from matcher.employee_for_uid, which is scoped to
    tracking-eligible profiles, and so cried wolf on five people who hold a
    perfectly good confirmed link (Refilwe Ramphaleng, Morati Segwe, Tumelo
    Molefe, Gofiwa Elias, Ntjidzi Nleya). A guard that cries wolf gets ignored,
    which would restore exactly the silence it exists to prevent.

    `extra` folds in whatever the caller already knows is owned. Returns a set of
    str uids; an unreachable DB yields just `extra` (the gate then over-reports,
    which is the correct direction to fail for THIS gate).
    """
    owned = {str(u) for u in (extra or set())}
    try:
        from integrations.models import TimeDoctorUserMap
        owned |= {str(u) for u in TimeDoctorUserMap.objects
                  .filter(employee__isnull=False)
                  .values_list('td_user_id', flat=True)}
    except Exception:      # noqa: BLE001 — fail LOUD: report more, never fewer
        log.debug('owned_td_uids: user map unavailable', exc_info=True)
    return owned


def orphan_hours_gate(payload, owned_uids, min_seconds: int = ORPHAN_MIN_SECONDS) -> list:
    """Time Doctor accounts carrying REAL tracked time that belong to nobody.

    payload:     the daily snapshot rows [{user_id, name, tracked_seconds}, ...].
    owned_uids:  every uid the matcher resolved to a payroll employee, AFTER
                 alias folding — pass fold_uid()-normalised ids or the canonical
                 ones, whichever the caller holds.
    min_seconds: ignore slivers below this (a spare machine idling is noise).

    Returns [{uid, name, seconds, hours}] worst-first. An unowned account with no
    hours is NOT an alarm — nobody lost work. Pure function, no Django.
    """
    owned = {str(u) for u in (owned_uids or set())}
    out = []
    for row in (payload or []):
        uid = str(row.get('user_id') or '')
        if not uid or uid in owned:
            continue
        secs = int(row.get('tracked_seconds') or 0)
        if secs <= 0 or secs < min_seconds:
            continue
        out.append({'uid': uid, 'name': (row.get('name') or '').strip() or uid,
                    'seconds': secs, 'hours': round(secs / 3600, 2)})
    out.sort(key=lambda r: r['seconds'], reverse=True)
    return out


def _names_corroborate(td_name: str, payroll_name: str) -> bool:
    """Deterministic sanity check on an AI-suggested owner.

    Requires either an exact normalised match, or a >= 2-token overlap so
    'Amantle Thake' -> 'Amantle Adelaide Thake' passes while 'Refilwe
    Ramphaleng' -> 'Caroline Refilwe Otukile' (one shared first name) does not.
    Same discipline as the TDMatcher's token-subset pass, which never guesses on
    a single token either.
    """
    try:
        from integrations.td_matching import name_tokens, norm_name
    except Exception:      # noqa: BLE001 — can't verify, so don't assert an owner
        return False
    a, b = norm_name(td_name or ''), norm_name(payroll_name or '')
    if not a or not b:
        return False
    if a == b:
        return True
    return len(name_tokens(a) & name_tokens(b)) >= 2


def screen_orphan_hours(orphans: list, payroll_names: list, day,
                        use_ai: bool = True, engines=None) -> dict:
    """Ask DeepSeek + Gemini which payroll person each orphan account belongs to.

    ADVISORY ONLY. Returns {'raise': [...], 'ai_unavailable': bool} where 'raise'
    is EVERY orphan handed in — the AI can only decorate a row with
    `likely_owner`, never drop one. `payroll_names` must be the WHOLE payroll
    roster, including people who already hold a matched Time Doctor account.

    `engines` is for tests; production uses DeepSeek then Gemini off the Claude
    bill, behind core.ai_assist's PII firewall.
    """
    rows = [dict(o) for o in (orphans or [])]
    if not rows:
        return {'raise': [], 'ai_unavailable': False}

    names = [n for n in (payroll_names or []) if (n or '').strip()]
    if not use_ai or not names:
        return {'raise': rows, 'ai_unavailable': True}

    if engines is None:
        try:
            from core.ai_assist import deepseek_complete, gemini_complete
            engines = [deepseek_complete, gemini_complete]
        except Exception:      # noqa: BLE001 — no AI available: raise everything
            log.debug('orphan-hours screen: ai_assist unavailable', exc_info=True)
            return {'raise': rows, 'ai_unavailable': True}

    numbered = '\n'.join(f'[{i}] {r["name"]} — {r["hours"]}h tracked'
                         for i, r in enumerate(rows))
    prompt = (
        'Time Doctor accounts below recorded real working time but are not linked '
        'to anyone on payroll, so that work is currently credited to nobody. For '
        'each numbered account, name the payroll person it most likely belongs to '
        '(a second machine, a spelling or married-name variant, or a device/SID '
        'login). The person may ALREADY have another Time Doctor account — that is '
        'the common case, so do not rule someone out for that. Answer only from '
        'the payroll list; omit the entry if nothing is a genuine likeness. Reply '
        'ONLY as JSON: {"verdicts":[{"id":N,"owner":"<exact payroll name>"}]}\n\n'
        f'Unlinked Time Doctor accounts:\n{numbered}\n\n'
        f'Payroll people:\n' + '\n'.join(f'- {n}' for n in names) + '\n'
    )

    send_text = prompt
    try:
        from core.ai_assist import is_safe_for_ai
        safety = is_safe_for_ai(prompt)
        if not safety.safe or not safety.redacted_text:
            return {'raise': rows, 'ai_unavailable': True}
        send_text = safety.redacted_text
    except Exception:          # noqa: BLE001 — firewall missing: don't send, still raise
        log.debug('orphan-hours screen: PII firewall unavailable', exc_info=True)
        return {'raise': rows, 'ai_unavailable': True}

    allowed = {n.strip().lower(): n.strip() for n in names}
    got_any = False
    for engine in engines:
        try:
            import json
            data = json.loads(engine(send_text, response_format='json_object',
                                     max_tokens=800))
        except Exception:      # noqa: BLE001 — this engine is down, try the next
            continue
        verdicts = (data or {}).get('verdicts') or []
        if not verdicts:
            continue
        got_any = True
        for v in verdicts:
            try:
                idx = int(v.get('id'))
            except (TypeError, ValueError):
                continue
            owner = allowed.get(str(v.get('owner') or '').strip().lower())
            # On payroll is NOT enough. The first live run offered 'Kelvin
            # Kimani' as 'Oitiretse Kao Galotshoge' and 'User' as 'Kotswana
            # Kotswana' — both real payroll names, both nonsense. A wrong owner
            # beside real hours is the worst thing this guard could produce: a
            # human clicks confirm and one person's work is credited to another.
            # So the AI proposes and arithmetic disposes.
            if (owner and 0 <= idx < len(rows)
                    and not rows[idx].get('likely_owner')
                    and _names_corroborate(rows[idx]['name'], owner)):
                rows[idx]['likely_owner'] = owner

    return {'raise': rows, 'ai_unavailable': not got_any}
