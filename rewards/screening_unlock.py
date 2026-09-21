"""Pure benefit-lock logic for the Omni pulse-check annual screening unlock.

This module decides when a member qualifies for a free annual screening based
on pulse checks completed. It is intentionally free of commercial terms
(money, currency, premium, credit or discount values) and contains no Django
or database coupling: it receives plain Python values and returns plain dicts.

Bad or missing data always closes a benefit; it never opens one.
"""

from datetime import date, timedelta

# Number of pulse checks required to unlock a screening.
REQUIRED_SCANS = 10

# Minimum number of days between two unlocked screenings.
WINDOW_DAYS = 365


def screening_state(scan_count, last_unlock, today):
    """Return the screening benefit state for a member on a given day.

    Args:
        scan_count: int — number of pulse checks completed (may be None).
        last_unlock: date or None — date of the last unlocked screening.
        today: date — the day to evaluate.

    Returns:
        dict with keys:
            unlocked: bool
            remaining: int (0 when unlocked)
            nextEligible: date or None — when another screening may unlock.
            label: str — short plain English description, never empty.
    """
    count = _normalise_scan_count(scan_count)
    today = _normalise_date(today)

    if count is None:
        return _locked_result(REQUIRED_SCANS, None,
                              f'No pulse checks recorded yet — {REQUIRED_SCANS} needed.')

    if last_unlock is not None:
        last = _normalise_date(last_unlock)
        # A missing or future-dated claim date is bad data: close the benefit.
        if last is None or last > today:
            return _locked_result(REQUIRED_SCANS, None,
                                  'We cannot confirm your last screening — please contact us.')
        if (today - last).days < WINDOW_DAYS:
            next_date = last + timedelta(days=WINDOW_DAYS)
            return _locked_result(
                max(0, REQUIRED_SCANS - count), next_date,
                f'Screening already claimed this year. Next one from {next_date.isoformat()}.')

    if count >= REQUIRED_SCANS:
        return {
            'unlocked': True,
            'remaining': 0,
            'nextEligible': None,
            'label': f'Free screening unlocked — {count} pulse checks done.',
        }

    remaining = REQUIRED_SCANS - count
    return _locked_result(
        remaining, None,
        f'{remaining} more pulse check{"s" if remaining != 1 else ""} '
        f'to unlock your free screening ({count} of {REQUIRED_SCANS} done).')


def _normalise_scan_count(value):
    """Return the count if it is a non-negative int, otherwise None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _normalise_date(value):
    """Return the date if it is a date instance, otherwise None."""
    if isinstance(value, date):
        return value
    return None


def _locked_result(remaining, next_eligible, label):
    """Build a locked-state result dict."""
    return {
        'unlocked': False,
        'remaining': 0 if remaining < 0 else remaining,
        'nextEligible': next_eligible,
        'label': label,
    }