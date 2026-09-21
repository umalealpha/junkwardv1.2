"""rewards/health_points.py — Health Points scoring (Alpha Rewards / Project Nexus).

Pure functions that convert consented health activity (steps) into Alpha
Rewards points, mirroring the telematics DrivingScore precedent. No model
writes, no I/O — call these from the API view (rewards/api_views.py).

DPA boundary (Botswana Data Protection Act):
  - Health activity is SPECIAL-CATEGORY personal data.
  - It is used for REWARDS ONLY. It must never flow into underwriting,
    pricing, or claims. Keep that boundary explicit here and at the call site.
  - Callers resolve a RewardMember by id; never pass or log a customer name
    through this module.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

# --- Steps → points -------------------------------------------------------
# Conservative launch weights. 1 point per full 1,000 steps, capped per day so
# a single big day can't run away with the programme's point liability.
STEPS_PER_POINT = 1000
DAILY_POINTS_CAP = 15
# TODO(human): confirm final weights with CFO (points-per-1000-steps, daily cap,
# and whether sleep/workouts also earn) before this goes live. These are
# placeholders chosen to be deliberately stingy until sign-off.


def steps_to_points(steps: int) -> int:
    """Daily steps → reward points, integer, floored, capped.

    >>> steps_to_points(0)        # no activity
    0
    >>> steps_to_points(999)      # under one full thousand
    0
    >>> steps_to_points(4500)     # 4 full thousands
    4
    >>> steps_to_points(20000)    # would be 20, capped at 15
    15
    """
    if not steps or steps < 0:
        return 0
    points = int(steps) // STEPS_PER_POINT
    return min(points, DAILY_POINTS_CAP)


# --- Points balance → tier ------------------------------------------------
# Thresholds map a member's cumulative points_balance onto the existing
# RewardMember.Tier ladder (bronze/silver/gold/platinum). These are the model's
# stored tier codes and are the source of truth for persisted data; the mobile
# app may display Setswana labels, but storage stays on these codes.
# TODO(human): confirm final tier thresholds with CFO.
TIER_THRESHOLDS = (
    # (minimum points_balance, tier code) — highest first for simple lookup.
    (3000, 'platinum'),
    (1500, 'gold'),
    (500,  'silver'),
    (0,    'bronze'),
)


def tier_for(points_balance: int) -> str:
    """Return the RewardMember.Tier code earned by a cumulative balance."""
    bal = int(points_balance or 0)
    for floor, tier in TIER_THRESHOLDS:
        if bal >= floor:
            return tier
    return 'bronze'


# --- Streak ---------------------------------------------------------------
def streak_days(member) -> int:
    """Count consecutive days (ending today) that have a HealthMetric row.

    A "day with activity" is any HealthMetric for the member on that date —
    we do not require points were awarded, only that data was recorded. The
    streak breaks on the first missing calendar day walking backward from
    today. Import is local to avoid a circular import with models.
    """
    from .models import HealthMetric

    dates = set(
        HealthMetric.objects.filter(member=member).values_list('date', flat=True)
    )
    if not dates:
        return 0

    today = timezone.localdate()
    # A streak only counts if it reaches today or yesterday (grace for a not-
    # yet-synced today). Otherwise the chain is already broken.
    if today not in dates and (today - timedelta(days=1)) not in dates:
        return 0

    streak = 0
    cursor = today if today in dates else today - timedelta(days=1)
    while cursor in dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


# --- Streak multiplier ----------------------------------------------------
# A longer unbroken streak multiplies the points earned that day — the core
# "consistency beats volume" gamification lever. Stingy launch weights; the
# award path applies these to base points and floors the result to an int.
# TODO(human): confirm final streak-multiplier tiers with CFO before go-live.
STREAK_MULTIPLIER_TIERS = (
    # (minimum streak length in days, multiplier) — highest first.
    (100, Decimal('2.00')),
    (30,  Decimal('1.50')),
    (7,   Decimal('1.25')),
    (0,   Decimal('1.00')),
)


def streak_multiplier(streak: int) -> Decimal:
    """Points multiplier earned by a current streak length.

    >>> streak_multiplier(0)
    Decimal('1.00')
    >>> streak_multiplier(6)
    Decimal('1.00')
    >>> streak_multiplier(7)
    Decimal('1.25')
    >>> streak_multiplier(30)
    Decimal('1.50')
    >>> streak_multiplier(100)
    Decimal('2.00')
    """
    s = int(streak or 0)
    for floor, mult in STREAK_MULTIPLIER_TIERS:
        if s >= floor:
            return mult
    return Decimal('1.00')


def effective_streak(active_dates, today, freeze_tokens: int = 0) -> dict:
    """Freeze-aware consecutive-day streak ending at ``today`` (yesterday grace).

    Pure and timezone-agnostic: the caller must pass ``today`` and the set of
    active calendar dates already resolved in the MEMBER's local timezone
    (e.g. ``timezone.localdate()`` server-side, or the device's local date) —
    that is where DST / cross-timezone correctness is handled. Because this
    works on ``date`` objects, not datetimes, it is immune to DST hour shifts.

    A "Streak Freeze" (forgiveness token) bridges a single missed calendar day
    so one slip does not reset the chain. A token is consumed only to span a gap
    that has an earlier active day to continue to — never to extend a finished
    streak into empty history.

    Returns ``{'streak': int, 'freezes_used': int, 'active': bool}``.
    """
    dates = set(active_dates)
    if not dates:
        return {'streak': 0, 'freezes_used': 0, 'active': False}

    yesterday = today - timedelta(days=1)
    if today in dates:
        cursor = today
    elif yesterday in dates:
        cursor = yesterday          # today not yet synced — grace, no token spent
    else:
        return {'streak': 0, 'freezes_used': 0, 'active': False}

    streak = 0
    used = 0
    tokens = int(freeze_tokens or 0)

    def has_earlier(c) -> bool:
        return any(d < c for d in dates)

    while cursor in dates or (tokens > 0 and has_earlier(cursor)):
        if cursor in dates:
            streak += 1
        else:
            tokens -= 1            # bridge a missed day with a forgiveness token
            used += 1
        cursor -= timedelta(days=1)

    return {'streak': streak, 'freezes_used': used, 'active': True}
