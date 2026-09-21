"""One date parser for the whole subrogation register.

WHY THIS EXISTS
---------------
Ageing and prescription both read the same spreadsheet column ("Date
Appointed"), but each was written with its own private parser, and the two
quietly disagreed:

  * An Excel serial arriving as a float (45900.0) was a real date to ageing's
    parser only when it was an int. As a float it became "no date appointed",
    which silently moved live cases into the P21.8m unaged pile.
  * A datetime crashed the prescription clock outright (datetime minus date
    raises TypeError), and prescription accepted neither Excel serials nor the
    "YYYY-MM-DD HH:MM:SS" strings that fill that very column.

So the same case could be aged correctly and still report UNKNOWN prescription
risk. Two parsers for one column is the defect; this module is the fix.

CONTRACT
--------
Return a `datetime.date`, or None. Never raise. The register is a spreadsheet
maintained by hand since 2015 — a cell may hold a date, a datetime, an ISO
string, an Excel serial number, the words "No Appointment Date Set", or nothing.

This module only reads a date. It does NOT judge it. A future date parses
normally here; whether a future appointment date is acceptable is the caller's
decision (ageing rejects it, because Keetile's specification says no future
dates).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

# Excel's day zero. Excel wrongly treats 1900 as a leap year, so the usable
# epoch for any modern date is 1899-12-30, not 1899-12-31.
_EXCEL_EPOCH = date(1899, 12, 30)

# A parsed date outside this window is not a real appointment date — it is a
# stray number in a date column (serial 0 is 1899-12-30, and a bare row index
# would land in the 1900s). Rejecting these stops nonsense reaching the ageing
# buckets and the prescription clock.
_EARLIEST = date(1990, 1, 1)
_LATEST = date(2100, 1, 1)

# Text that appears in the date column meaning "there isn't one".
_BLANK_WORDS = {'', 'n/a', 'na', 'none', 'null', 'tba', 'tbc', '-', '--', 'unknown'}

_STRING_FORMATS = ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S')


def _in_range(value: date | None) -> date | None:
    """Keep only dates that could plausibly be a real appointment date."""
    if value is None:
        return None
    return value if _EARLIEST <= value <= _LATEST else None


def _from_serial(number: float) -> date | None:
    """Convert an Excel serial day-number to a date.

    Excel stores 2025-08-31 09:00 as 45900.375 — the fraction is the time of
    day, which we discard. Anything not finite, or so large it overflows the
    date type, is not a date.
    """
    if math.isnan(number) or math.isinf(number):
        return None
    try:
        return _EXCEL_EPOCH + timedelta(days=int(number))
    except (ValueError, OverflowError, OSError):
        return None


def parse_register_date(value) -> date | None:
    """Read one date out of the subrogation register. Never raises.

    Accepts a date, a datetime, an ISO date or datetime string, or an Excel
    serial number as either int or float. Returns None for blanks, free text
    and anything else.
    """
    if value is None:
        return None

    # bool is a subclass of int in Python, so True would otherwise be read as
    # Excel serial 1 (31 December 1899).
    if isinstance(value, bool):
        return None

    # datetime must be tested BEFORE date — it is a subclass of date, and
    # returning it unchanged is what crashed the prescription clock.
    if isinstance(value, datetime):
        return _in_range(value.date())

    if isinstance(value, date):
        return _in_range(value)

    if isinstance(value, (int, float)):
        return _in_range(_from_serial(float(value)))

    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _BLANK_WORDS:
            return None
        for fmt in _STRING_FORMATS:
            try:
                return _in_range(datetime.strptime(text, fmt).date())
            except ValueError:
                continue
        # A serial number that arrived as text ("45900").
        try:
            return _in_range(_from_serial(float(text)))
        except (ValueError, TypeError):
            return None
        return None

    # Lists, dicts, and anything else are not dates.
    return None
