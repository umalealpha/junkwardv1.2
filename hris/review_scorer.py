"""
hris/review_scorer.py — the ONE scorer behind every performance output.

CFO 2026-09-09. Start at 8/10, deduct per rule, floor at 3. It is built once and
feeds three places (decision 27): the employee's own monthly review auto-posted
on the 10th, the CEO's Sunday Dashboard, and the Time Doctor cheat escalation
inside the CEO's 06:30 brief. Two scorers would drift, and the day the CEO's
Sunday email calls somebody dead weight while that person's own review says
"Meets" is the day the whole thing stops being defensible.

The module is deliberately PURE: it takes a facts dict and returns a score. Every
number it needs is gathered by review_facts.py and handed in. That keeps the
rules testable without prod, and it means a Graphite outage degrades one rule
into "not scored" instead of scoring somebody wrongly.

THE RULES (CFO's numbering; his rule 4 was a duplicate of rule 2 and is dropped)

  1   first activity after 08:15 on 3+ days                            -2
  1b  forgiven by an in-time late notice, a logged client visit, approved
      leave, or same-day sick leave — but only the FIRST 3 late days of a
      month; from the 4th the day counts even when notified
  2   5+ days below target (6.5h; 4.5h Exco) unexplained               -2
  3   3+ overdue tasks on the 10th                                     -1
  3b  skipped team feedback — its OWN mark, never inside the task count  -1
  5   policy left on quote while the premium was collected             -2  (the
      underwriter, never the agent)
  6   motor claim >3 working days old with no tracker record, or a stage
      not moved in >10 working days (FY27, from 1 July 2026)           -2
  7   Exco vs the GWP budget, cumulative from 1 July            flat 5/10
  8   RealPay / system faults — NOT a staff review mark, except the one
      owner the CFO named on 2026-09-09                                -3
  9   did not open the screens the job needs (from 1 September)        -2
  10  one confirmed frozen-screen day                          straight to 3
  11  IT — four factors                                            -2 each
  12  Unicoin — four factors                                       -2 each

PRECEDENCE, and why it is this way round. Rule 10 is dishonesty rather than
underperformance, so the CFO put it above everything: a good month must not
average it away. Rule 7 is a flat Exco mark that replaces the points maths. So
10 beats 7 beats the arithmetic. One Exco member is exempt from rule 10 and
from nothing else (CFO 2026-09-09: "others lock in"); the collector supplies
that as `integrity_exempt` so no name lives in this module.

GUARDRAILS, all load-bearing:
  * Anyone on approved leave for a day is never flagged for that day.
  * Anyone who cannot be matched with certainty gets NO automatic review at all
    — they go to an exceptions list. Never name-match (f-never-namematch-td).
  * A rule whose data is not ready is recorded as NOT SCORED, with the reason,
    and deducts nothing. Silence is not a pass and a gap is not a failure.
  * Facts only. Never health, never a protected characteristic. Time Doctor
    window titles are customer data and are never read.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

BASE_SCORE  = 8
FLOOR_SCORE = 3

# CFO 2026-09-09. Rule 10 lands here regardless of the arithmetic.
INTEGRITY_SCORE = 3
# Rule 7. Exco missing the cumulative GWP budget lands here.
EXCO_MISS_SCORE = 5

# Only flags from the Friday the CFO announced the watch count (2026-09-04).
INTEGRITY_CUTOFF = dt.date(2026, 9, 4)

# Rule 1b: how many late mornings a notice can buy back in one month.
FORGIVEN_LATE_DAYS = 3
# Rule 1: late days in the month before it costs the two points.
LATE_DAYS_BREACH = 3
# Rule 2: unexplained short days before it costs the two points.
LOW_HOURS_BREACH = 5
# Rule 3: overdue tasks on the 10th before it costs the point.
OVERDUE_BREACH = 3

RATING_MEETS     = 'ME'
RATING_PARTIAL   = 'PA'
RATING_BELOW     = 'BE'


def rating_for(points: int) -> str:
    """8-10 Meets · 6-7 Partially meets · <=5 Below (CFO 2026-09-09)."""
    if points >= 8:
        return RATING_MEETS
    if points >= 6:
        return RATING_PARTIAL
    return RATING_BELOW


@dataclass
class Mark:
    """One rule's verdict on one person. `points` is negative or zero."""
    rule: str
    points: int
    headline: str
    evidence: str = ''


@dataclass
class NotScored:
    """A rule that could not run. Deducts nothing and says why, out loud."""
    rule: str
    why: str


@dataclass
class Score:
    points: int
    rating: str
    marks: list = field(default_factory=list)
    not_scored: list = field(default_factory=list)
    override: str = ''

    @property
    def is_below(self) -> bool:
        return self.rating == RATING_BELOW

    def as_dict(self) -> dict:
        return {
            'points': self.points,
            'rating': self.rating,
            'override': self.override,
            'marks': [vars(m) for m in self.marks],
            'not_scored': [vars(n) for n in self.not_scored],
        }


def forgiven_late_days(late_dates, excused_dates, cap=FORGIVEN_LATE_DAYS):
    """Rule 1b. Returns (counted_late_days, forgiven_dates).

    The cap is applied in DATE ORDER, so it is the first three excused mornings
    of the month that are forgiven — not whichever three the code happened to
    iterate first. Without an ordering this is non-deterministic between runs
    and two screens can disagree about who was forgiven.
    """
    late   = sorted(set(late_dates or ()))
    excuse = set(excused_dates or ())
    forgiven, counted = [], []
    for d in late:
        if d in excuse and len(forgiven) < cap:
            forgiven.append(d)
        else:
            counted.append(d)
    return counted, forgiven


def score(facts: dict) -> Score:
    """Score one person's month. `facts` comes from review_facts.collect().

    Never raises on a missing key: an absent fact is an unrun rule, not a zero.
    """
    marks: list = []
    unscored: list = []

    def pending(rule, why):
        unscored.append(NotScored(rule=rule, why=why))

    # ---- rule 1 + 1b: punctuality -----------------------------------------
    if 'late_dates' in facts:
        counted, forgiven = forgiven_late_days(facts.get('late_dates'),
                                               facts.get('late_excused_dates'))
        if len(counted) >= LATE_DAYS_BREACH:
            why = f'{len(counted)} mornings started after 08:15'
            if forgiven:
                why += f' ({len(forgiven)} more forgiven — notified in time)'
            marks.append(Mark('1', -2, why,
                              evidence=', '.join(d.isoformat() for d in counted)))
    else:
        pending('1', 'no Time Doctor arrival data for the month')

    # ---- rule 2: hours ------------------------------------------------------
    if 'low_hours_days' in facts:
        n = int(facts.get('low_hours_days') or 0)
        if n >= LOW_HOURS_BREACH:
            marks.append(Mark('2', -2,
                              f'{n} days below the daily target with no accepted '
                              f'explanation',
                              evidence=facts.get('low_hours_evidence', '')))
    else:
        pending('2', 'no workday records for the month')

    # ---- rule 3: outstanding work ------------------------------------------
    if 'overdue_tasks' in facts:
        n = int(facts.get('overdue_tasks') or 0)
        if n >= OVERDUE_BREACH:
            marks.append(Mark('3', -1, f'{n} tasks still overdue on the 10th',
                              evidence=facts.get('overdue_evidence', '')))
    else:
        pending('3', 'task board not read')

    # Rule 3b, CFO 2026-09-09: a manager who skipped their team's feedback is
    # marked for THAT, by name — not hidden inside a task total, and not counted
    # twice (19 of the 46 overdue tasks are Omni's own feedback nudges, so the
    # collector strips them out of `overdue_tasks` before it gets here).
    skipped = int(facts.get('feedback_skipped') or 0)
    if skipped:
        marks.append(Mark('3b', -1,
                          f'did not complete monthly feedback for {skipped} of '
                          f'their team',
                          evidence=facts.get('feedback_skipped_evidence', '')))

    # ---- rule 5: policy on quote, premium collected -------------------------
    if 'quote_but_paid' in facts:
        n = int(facts.get('quote_but_paid') or 0)
        if n:
            marks.append(Mark('5', -2,
                              f'{n} policies left on quote while the premium was '
                              f'collected',
                              evidence=facts.get('quote_but_paid_evidence', '')))
    else:
        pending('5', 'awaiting the Graphite quote-vs-collected extract '
                     '(task raised with the systems owner, CFO 2026-09-09)')

    # ---- rule 6: motor claims ----------------------------------------------
    if 'claim_breaches' in facts:
        n = int(facts.get('claim_breaches') or 0)
        if n:
            marks.append(Mark('6', -2,
                              f'{n} FY27 motor claims with no tracker record after '
                              f'3 working days, or no movement in 10',
                              evidence=facts.get('claim_breach_evidence', '')))
    elif facts.get('is_claims_handler'):
        pending('6', 'Graphite claims extract unavailable this run')

    # ---- rule 8: the system-fault owner ------------------------------------
    # NOT a staff review mark. Eight of the nine developers are ADRisk Global or
    # Risk Software Africa, and the standing output is a monthly report to the
    # COO. The single exception is the owner the CFO named on 2026-09-09.
    if facts.get('system_fault_owner') and facts.get('system_fault_open'):
        marks.append(Mark('8', -3,
                          'RealPay / system faults left open',
                          evidence=facts.get('system_fault_evidence', '')))

    # ---- rule 9: did not open the screens the job needs ---------------------
    if facts.get('expected_screens') and 'screens_opened' in facts:
        missed = [s for s in facts['expected_screens']
                  if s not in set(facts.get('screens_opened') or ())]
        if missed:
            marks.append(Mark('9', -2,
                              f'never opened {len(missed)} of the screens the role '
                              f'needs',
                              evidence=', '.join(sorted(missed))))
    else:
        pending('9', 'no approved expected-screens list for this role yet')

    # ---- rules 11 / 12: the four-factor departments -------------------------
    for rule, key in (('11', 'it_factors'), ('12', 'unicoin_factors')):
        factors = facts.get(key)
        if factors is None:
            continue
        for name, breached in sorted(dict(factors).items()):
            if breached:
                marks.append(Mark(rule, -2, name))

    # ---- arithmetic ---------------------------------------------------------
    points = BASE_SCORE + sum(m.points for m in marks)
    points = max(FLOOR_SCORE, min(BASE_SCORE, points))
    override = ''

    # ---- rule 7: Exco vs budget, flat, above the arithmetic -----------------
    if facts.get('is_exco') and facts.get('gwp_missed_budget'):
        points = EXCO_MISS_SCORE
        override = 'rule 7'
        marks.append(Mark('7', 0,
                          'Exco — cumulative GWP is behind the FY27 budget',
                          evidence=facts.get('gwp_evidence', '')))
    elif facts.get('is_exco') and 'gwp_missed_budget' not in facts:
        pending('7', 'cumulative GWP against budget not read this run')

    # ---- rule 10: integrity, above everything -------------------------------
    if facts.get('integrity_suspicious_days'):
        # Who is exempt is a roster decision, not a rule. The collector reads it
        # from settings and passes the answer, so this module never carries a
        # person's name and the exemption can change without a code release.
        if facts.get('integrity_exempt'):
            unscored.append(NotScored('10', 'exempt from rule 10 by the CFO '
                                            '2026-09-09'))
        else:
            points = INTEGRITY_SCORE
            override = 'rule 10'
            days = facts['integrity_suspicious_days']
            marks.append(Mark('10', 0,
                              f'{len(days)} confirmed frozen-screen day(s)',
                              evidence=', '.join(str(d) for d in days)))

    return Score(points=points, rating=rating_for(points), marks=marks,
                 not_scored=unscored, override=override)
