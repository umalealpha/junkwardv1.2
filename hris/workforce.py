"""
hris/workforce.py

Pure, dependency-free rules for the Workforce Daily Brief + hours-justification
tool (CFO 2026-07-14). Kept free of Django imports so the rules can be unit
tested against plain dates with no database.

Company working-time policy (CFO 2026-07-14, corrected 2026-07-14 eve):
  - Weekday (Mon-Fri): 6.5 required hours.
  - Saturday: half day → 3 required hours from 2026-08-01 (4h before that;
    the rule is effective-dated so a re-run over an earlier week still judges
    people against what was asked of them at the time).
  - Sunday: no work → 0 required hours.
  - Public holidays: a PAID public holiday is a day off → 0 required hours.
    An UNPAID public holiday (flagged is_working_day) → staff work → the normal
    weekday/Saturday rule applies.
  - External meetings used to justify a shortfall are capped at 1.5h (90 min).

Manager weekday rule (CFO 2026-07-23): managers, EXCO and the Finance Manager
carry a lighter WEEKDAY requirement of 4.5h (Saturday and Sunday/holidays are
the same for everyone). WHO counts as a manager is decided in hris.workforce_roles (a closed,
env-tunable set) — this module only takes the resulting `is_manager` boolean so
it stays Django-free and unit-testable.

A day's tracked hours (from Time Doctor) plus any JUSTIFIED shortfall
(approved leave, a valid external meeting / client visit) are compared to the
required hours:
  - tracked >= required                     → 'met'
  - tracked + justified >= required         → 'justified'
  - otherwise                               → 'unjustified'
Justified hours count as working; unjustified hours count as non-working.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable

WORKDAY_HOURS         = Decimal('6.5')   # CFO 2026-07-14 eve: compulsory 6.5 (was 7.5)
MANAGER_WORKDAY_HOURS = Decimal('4.5')   # CFO 2026-07-23: managers/EXCO/FM weekday requirement
SATURDAY_HOURS        = Decimal('3')     # CFO 2026-08-01: 3h on Saturday (was 4)
SATURDAY_HOURS_LEGACY = Decimal('4')     # what was asked of people BEFORE that date
SATURDAY_3H_FROM      = date(2026, 8, 1)  # the day the 3h rule takes effect
SUNDAY_HOURS          = Decimal('0')
MEETING_CAP_MINUTES   = 90          # external meeting justification cap = 1.5h

SATURDAY = 5                        # date.weekday(): Mon=0 … Sat=5, Sun=6
SUNDAY   = 6


def saturday_hours(d: date) -> Decimal:
    """Saturday requirement EFFECTIVE ON `d` — 4h before 2026-08-01, 3h from it.

    Effective-dated on purpose: a report or appraisal re-run over an earlier
    week must judge people against what was actually asked of them at the time.
    The July 2026 audit is live, so silently re-scoring July Saturdays at 3h
    would rewrite history in people's favour but wrongly."""
    return SATURDAY_HOURS if d >= SATURDAY_3H_FROM else SATURDAY_HOURS_LEGACY


def base_required_hours(d: date, is_manager: bool = False) -> Decimal:
    """Required hours for a normal (non-holiday) date under company policy.

    `is_manager=True` applies the lighter 4.5h WEEKDAY requirement (CFO
    2026-07-23). Saturday (3h from 2026-08-01, 4h before) and Sunday (0h) are
    the same for everyone."""
    wd = d.weekday()
    if wd == SUNDAY:
        return SUNDAY_HOURS
    if wd == SATURDAY:
        return saturday_hours(d)
    return MANAGER_WORKDAY_HOURS if is_manager else WORKDAY_HOURS


def required_hours_for_date(d: date, holiday_off_dates: Iterable[date] = (),
                            is_manager: bool = False) -> Decimal:
    """Required hours for `d`, given the set of public-holiday dates on which
    staff do NOT work (i.e. holidays not flagged as a working day).

    A non-working holiday → 0. Any other date (including a holiday flagged as a
    working day) uses the normal weekday/Saturday/Sunday rule. `is_manager`
    applies the manager weekday rate (see base_required_hours).
    """
    if d in set(holiday_off_dates):
        return Decimal('0')
    return base_required_hours(d, is_manager=is_manager)


def weekly_required_hours(monday: date, holiday_off_dates: Iterable[date] = (),
                          is_manager: bool = False, days: int = 6) -> Decimal:
    """Total required hours across a working week starting at `monday` (default
    Mon–Sat, 6 days). Honours public holidays and the manager weekday rate, so a
    manager's full week is 5×4.5 + 3 (Sat) = 25.5h vs 5×6.5 + 3 = 35.5h from
    2026-08-01 (26.5h / 36.5h for weeks before that — see saturday_hours)."""
    off = set(holiday_off_dates)
    return sum((required_hours_for_date(monday + timedelta(days=i), off, is_manager=is_manager)
                for i in range(days)), Decimal('0'))


def classify_day(required: Decimal, tracked: Decimal, justified: Decimal = Decimal('0')) -> str:
    """Classify a single day. Returns one of:
    'not_required' | 'met' | 'justified' | 'unjustified'.
    `justified` is the shortfall hours the employee has justified (leave /
    valid meeting / client visit)."""
    required  = Decimal(required or 0)
    tracked   = Decimal(tracked or 0)
    justified = Decimal(justified or 0)
    if required <= 0:
        return 'not_required'
    if tracked >= required:
        return 'met'
    if tracked + justified >= required:
        return 'justified'
    return 'unjustified'


def shortfall_hours(required: Decimal, tracked: Decimal) -> Decimal:
    """How many hours short of the requirement (never negative)."""
    gap = Decimal(required or 0) - Decimal(tracked or 0)
    return gap if gap > 0 else Decimal('0')


def meeting_minutes_valid(minutes: int) -> bool:
    """External-meeting justification must be > 0 and within the 1.5h cap."""
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return False
    return 0 < m <= MEETING_CAP_MINUTES
