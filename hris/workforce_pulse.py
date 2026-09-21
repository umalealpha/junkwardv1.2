"""
hris/workforce_pulse.py

Team-level "pulse" stats for the manager Exceptions report (CFO 2026-07-16 —
"what else can we produce to excite the managers"):

  * momentum       — are we up or down vs the comparison period, and who improved
  * team_leaderboard — department-vs-department, ranked by productive hours/head
  * focus_stats    — the deepest unbroken PRODUCTIVE block per person + the
                     team's sharpest hour of the day
  * shortfall_board — who missed their own daily productive target, worst first

Pure functions over Time Doctor data ALREADY pulled by exceptions_report — no
network here, no window/app titles (AD-POL-AI-GOV-001), unit-testable against
captured samples. Momentum/leaderboard rank on tracked or productive HOURS only.
"""
from __future__ import annotations

import datetime
from collections import defaultdict

LOCAL_OFFSET = datetime.timedelta(hours=2)   # Botswana / SAST = UTC+2


def _h(sec):
    return round((sec or 0) / 3600, 2)


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.strptime(str(ts)[:19], '%Y-%m-%dT%H:%M:%S')
    except Exception:    # noqa: BLE001
        return None


def momentum(this_secs_by_uid, prev_secs_by_uid, name_by_uid, *, top=3):
    """Team tracked hours this period vs the comparison period + who improved most.

    *_secs_by_uid: {td_uid: tracked seconds}. Returns team hours both periods, the
    percentage change (None when there is no prior baseline), and the top movers
    (positive deltas only — momentum is meant to celebrate, not name-and-shame)."""
    this_total = sum(this_secs_by_uid.values())
    prev_total = sum(prev_secs_by_uid.values())
    pct = round(100 * (this_total - prev_total) / prev_total, 1) if prev_total else None
    deltas = []
    for uid, name in name_by_uid.items():
        d = this_secs_by_uid.get(uid, 0) - prev_secs_by_uid.get(uid, 0)
        if d > 0 and name:
            deltas.append((name, _h(d)))
    deltas.sort(key=lambda t: t[1], reverse=True)
    return {'this_h': _h(this_total), 'prev_h': _h(prev_total), 'pct': pct,
            'up': (pct is None or pct >= 0), 'most_improved': deltas[:top]}


def team_leaderboard(matched_uids, secs_by_uid, prod_by_uid, dept_by_uid, *, min_size=3,
                     always_show=None):
    """Rank departments by AVERAGE productive hours per tracked head — fair across
    team sizes (a 13-person team shouldn't 'win' just for being big). Only people
    who actually tracked (sec > 0) count toward a team's average. Teams with fewer
    than `min_size` trackers are NOT shown — a team of one would just expose an
    individual, and tiny teams are noise. Returns (rows_sorted_best_first, hidden)
    where hidden = number of trackers in unranked / unassigned teams.

    `always_show` = department names that must appear even when below `min_size`
    (CFO 2026-07-23: the Executive / C-Suite team is only 3 people, so it was
    hidden — this pins it in regardless). Pinned rows carry `pinned=True`, are
    NOT medal-ranked, and always sit at the BOTTOM so the real competitive
    ranking is unchanged. Their heads are not counted as `hidden`."""
    always = {(d or '').strip() for d in (always_show or ())}
    agg = defaultdict(lambda: {'heads': 0, 'sec': 0, 'prod': 0.0})
    for uid in matched_uids:
        sec = secs_by_uid.get(uid, 0)
        if sec <= 0:
            continue
        a = agg[(dept_by_uid.get(uid) or '').strip()]
        a['heads'] += 1
        a['sec'] += sec
        a['prod'] += (prod_by_uid.get(uid) or 0)
    rows, pinned, hidden = [], [], 0
    for dept, a in agg.items():
        row = {'dept': dept, 'heads': a['heads'],
               'avg_prod_h': round(a['prod'] / a['heads'], 2),
               'avg_h': _h(a['sec'] / a['heads']),
               'total_h': _h(a['sec'])} if a['heads'] else None
        if not dept or a['heads'] < min_size:
            if dept and dept in always and a['heads'] >= 1:
                row['pinned'] = True
                pinned.append(row)
            else:
                hidden += a['heads']
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r['avg_prod_h'], r['avg_h']), reverse=True)
    rows += pinned
    return rows, hidden


def _hour_label(h):
    def fmt(x):
        ap = 'am' if (x % 24) < 12 else 'pm'
        return f'{(x % 12) or 12}{ap}'
    return f'{fmt(h)}–{fmt(h + 1)}'


def focus_stats(timeuse, name_by_uid, matched_uids, ordered_uids=None, *,
                gap_secs=300, top=3, prod_secs_by_uid=None):
    """Deepest unbroken PRODUCTIVE block per matched person + the team's sharpest
    (most productive) hour of the day.

    PRODUCTIVE ONLY (CFO 2026-07-30). This used to run off the worklog, i.e. any
    tracked second on any machine, including manually-typed rows, and it reported
    the wall-clock SPAN of the merged block. Unami Butale's 29 July came out as
    "10h 06m unbroken" in the manager email — nobody sits unbroken for ten hours,
    and the figure disagreed with the productive hours printed two boxes above it.
    The rule everywhere else in this report is productive hours, so focus follows
    it: only Time Doctor rows scored PRODUCTIVE count, so the block can never
    exceed the person's productive time.

    `timeuse` is the raw /activity/timeuse payload — one bucket per requested
    user id, rows carrying start / time / score but NO userId (same quirk
    integrations.timedoctor.aggregate works around), so `ordered_uids` must be the
    ordered id list passed to the API for attribution. Titles are never read
    (AD-POL-AI-GOV-001).

    Blocks are computed PER MACHINE and a person keeps their best machine's block
    — never a merge across machines. Two laptops running the same afternoon carry
    no overlap information, so merging them would invent focus that never
    happened (the same trap that produced the 21.64h day, CFO 2026-07-29).

    A block is the SUM of productive seconds in the stretch, not the wall-clock
    span of it. Measuring the span let the ≤`gap_secs` breaks between productive
    rows count as focus, which on live 29-Jul data pushed two people slightly
    ABOVE their own printed productive hours (Leungo 7.39h vs 7.10h) — a number
    that contradicts the same email is a number nobody trusts. `prod_secs_by_uid`
    (optional) caps each block at the person's published productive seconds.

    Returns {top_focus:[(name, block_secs)], best_hour, best_hour_label,
    by_hour[24]} — by_hour is PRODUCTIVE seconds per Botswana hour."""
    from integrations.timedoctor import PRODUCTIVE_SCORES
    from integrations.td_matching import fold_uid

    matched = set(matched_uids)
    ordered = list(ordered_uids or [])
    sessions = defaultdict(list)          # raw uid (one machine) -> [(start, secs)]
    by_hour = [0] * 24
    for idx, bucket in enumerate(timeuse or []):
        rows = bucket if isinstance(bucket, list) else [bucket]
        bucket_uid = next((r.get('userId') for r in rows
                           if isinstance(r, dict) and r.get('userId')), None)
        if bucket_uid is None and idx < len(ordered):
            bucket_uid = ordered[idx]
        for r in rows:
            if not isinstance(r, dict):
                continue
            raw_uid = r.get('userId') or bucket_uid
            st = _parse(r.get('start'))
            secs = int(r.get('time') or 0)
            if raw_uid is None or not st or secs <= 0:
                continue
            if r.get('score') not in PRODUCTIVE_SCORES:
                continue
            if fold_uid(raw_uid) not in matched:
                continue
            sessions[raw_uid].append((st, secs))
            by_hour[(st + LOCAL_OFFSET).hour] += secs

    best_by_person = {}                   # folded uid -> longest block secs
    for raw_uid, sess in sessions.items():
        sess.sort(key=lambda t: t[0])
        longest = run = 0
        cur_end = None
        for st, secs in sess:
            end = st + datetime.timedelta(seconds=secs)
            if cur_end is not None and (st - cur_end).total_seconds() <= gap_secs:
                run += secs                       # same stretch — add the productive time
            else:
                run = secs                        # new stretch
            cur_end = end if cur_end is None else max(cur_end, end)
            longest = max(longest, run)
        uid = fold_uid(raw_uid)
        if longest > best_by_person.get(uid, 0):
            best_by_person[uid] = longest

    # Never print a focus figure larger than the productive hours this same email
    # prints two boxes higher up. Summing productive seconds (above) already keeps
    # the block inside one machine's productive total, but `productive_hours` comes
    # from the BUSIEST-BY-CLOCK machine, which is not always the machine that holds
    # the best block — so cap against the published number and stay consistent.
    if prod_secs_by_uid:
        for uid in list(best_by_person):
            cap = prod_secs_by_uid.get(uid)
            if cap is not None:
                best_by_person[uid] = min(best_by_person[uid], int(cap))

    best = [(name_by_uid.get(uid), secs) for uid, secs in best_by_person.items()
            if name_by_uid.get(uid) and secs > 0]
    best.sort(key=lambda t: t[1], reverse=True)

    peak = max(range(24), key=lambda h: by_hour[h]) if any(by_hour) else None
    return {'top_focus': best[:top], 'best_hour': peak,
            'best_hour_label': _hour_label(peak) if peak is not None else None,
            'by_hour': by_hour}


def shortfall_board(matched_uids, prod_by_uid, name_by_uid, target_by_uid, *,
                    bottom=5, streaks=None):
    """Who came up SHORT of their own daily productive-hours target, worst first.

    The counterweight to the leaderboard (CFO 2026-07-30 — "how do we make people
    feel bad about not working according to Time Doctor"): the top of the report
    celebrates by name, so the bottom names too. Facts only — the person's own
    target, their productive hours, the gap, and how many days in a row they have
    been short. `target_by_uid` is per-person because managers / EXCO / FM are on
    the lighter 4.5h weekday rate and must be judged against THEIR number.

    People who did not track at all are NOT here — they are already named in the
    "did not track" and 3-day alarm sections, and would otherwise be shamed twice
    for the same day. Returns [{name, prod_h, target_h, gap_h, streak}]."""
    streaks = streaks or {}
    rows = []
    for uid in matched_uids:
        name = name_by_uid.get(uid)
        prod = prod_by_uid.get(uid)
        target = target_by_uid.get(uid)
        if not name or prod is None or not target:
            continue
        gap = round(float(target) - float(prod), 2)
        if gap <= 0:
            continue
        rows.append({'name': name, 'prod_h': round(float(prod), 2),
                     'target_h': round(float(target), 2), 'gap_h': gap,
                     'streak': int(streaks.get(uid) or 1)})
    rows.sort(key=lambda r: (-r['gap_h'], r['name']))
    return rows[:bottom]
