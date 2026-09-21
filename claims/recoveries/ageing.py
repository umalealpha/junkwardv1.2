"""Subrogation recovery ageing analysis.

Tracks the age of outstanding recovery cases in days since the date
appointed. Keetile Mokhendo's requested buckets: 0-90, 91-180, 181-365,
1-2 years, over 2 years.

Of the 833 open recovery cases, 542 have no date appointed at all — they
carry P21.8m and must be visible in reporting, not dropped as errors.
"""
from __future__ import annotations

from datetime import date
from enum import Enum

from claims.recoveries.dates import parse_register_date


class AgeBucket(Enum):
    """Ageing buckets for subrogation recovery claims.

    Each bucket groups outstanding recovery cases by days elapsed since
    the date appointed. NO_DATE is a real bucket, not an error state,
    because 542 of 833 open cases have never had a date appointed.
    """
    B0_90 = '0-90 days'
    B91_180 = '91-180 days'
    B181_365 = '181-365 days'
    B1_2Y = '1-2 years'
    OVER_2Y = 'Over 2 years'
    NO_DATE = 'No date appointed'

    @property
    def label(self) -> str:
        """Human-readable label for the bucket.

        This label appears directly on the CFO's screen in reports.
        """
        return self.value


def days_since_appointed(value, as_at: date) -> int | None:
    """Calculate days elapsed since the recovery case was appointed.

    Returns None if the appointed date cannot be parsed or if it is in
    the future (which signals a data entry error, not a negative age).

    Args:
        value: the appointed date as date, datetime, ISO string,
               Excel serial integer, None, or unparseable text
        as_at: the reference date for age calculation

    Returns:
        The number of days elapsed (always >= 0), or None if value
        is None, unparseable, or in the future.
    """
    appointed = _parse_date(value)
    if appointed is None or appointed > as_at:
        return None
    return (as_at - appointed).days


def age_bucket(value, as_at: date) -> AgeBucket:
    """Determine which ageing bucket a recovery case belongs to.

    Bucket boundaries are inclusive at the top: 0-90 days, 91-180 days,
    181-365 days, 366-730 days (1-2 years), and 731+ days (over 2 years).

    A future appointed date signals a keying error and is treated as
    NO_DATE, not as a fresh case. Unparseable text also returns NO_DATE,
    not an exception.

    Args:
        value: the appointed date as date, datetime, ISO string,
               Excel serial integer, None, or unparseable text
        as_at: the reference date for age calculation

    Returns:
        AgeBucket enum member. Always returns a valid bucket;
        NO_DATE for None, unparseable text, or future dates.
    """
    appointed = _parse_date(value)

    # No date, unparseable, or future date → NO_DATE
    if appointed is None or appointed > as_at:
        return AgeBucket.NO_DATE

    days = (as_at - appointed).days

    # Buckets with inclusive upper bounds
    if days <= 90:
        return AgeBucket.B0_90
    elif days <= 180:
        return AgeBucket.B91_180
    elif days <= 365:
        return AgeBucket.B181_365
    elif days <= 730:
        return AgeBucket.B1_2Y
    else:
        return AgeBucket.OVER_2Y


# Reading a date is shared with prescription.py — see dates.py for why. This
# module's own rule (no future dates) is applied by the callers above, because
# that is a policy decision about appointments, not part of reading a cell.
_parse_date = parse_register_date
