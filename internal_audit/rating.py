"""Finding rating — Likelihood (5) x Impact (5) matrix.

Rating is AUTO-COMPUTED from likelihood x impact and is never typed by the
auditor (spec Module 5). Score bands on the 1..25 product:

    1-4    Low
    5-9    Medium
    10-15  High
    16-25  Critical

Re-test cadence is driven by the rating, not chosen ad hoc (spec Module 7):

    Critical / High  -> quarterly   (90 days)
    Medium           -> semi-annual (182 days)
    Low              -> annual       (365 days)
"""
from __future__ import annotations

RATING_LOW = 'low'
RATING_MEDIUM = 'medium'
RATING_HIGH = 'high'
RATING_CRITICAL = 'critical'


def compute_rating(likelihood: int, impact: int) -> str:
    score = int(likelihood) * int(impact)
    if score <= 4:
        return RATING_LOW
    if score <= 9:
        return RATING_MEDIUM
    if score <= 15:
        return RATING_HIGH
    return RATING_CRITICAL


def retest_interval_days(rating: str) -> int:
    return {
        RATING_CRITICAL: 90,
        RATING_HIGH: 90,
        RATING_MEDIUM: 182,
        RATING_LOW: 365,
    }.get(rating, 365)
