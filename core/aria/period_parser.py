"""
core/aria/period_parser.py — natural-language period → (start, end) helper.

ARIA tool-loop tools (typically get_pl_summary, get_tb_status) take ISO
date ranges, but the user types things like "March 2026" or "FY25". This
module turns those phrases into precise (start_date, end_date) tuples,
respecting Alpha Direct's fiscal year (1 Jul – 30 Jun).

Supported phrases (case-insensitive, whitespace-tolerant):

    Fiscal-year forms
        "FY25"                  → 2024-07-01 .. 2025-06-30
        "FY26"                  → 2025-07-01 .. 2026-06-30
        "FY25 12M"              → 2024-07-01 .. 2025-06-30
        "FY26 9M"               → 2025-07-01 .. 2026-03-31
        "FY26 6M"               → 2025-07-01 .. 2025-12-31
        "FY2025"                → 2024-07-01 .. 2025-06-30
        "fiscal year 2025"      → 2024-07-01 .. 2025-06-30

    Month forms
        "Mar 2026" / "March 2026" → 2026-03-01 .. 2026-03-31
        "2026-03"                 → 2026-03-01 .. 2026-03-31

    Quarter forms (FISCAL quarters — Q1 = Jul-Sep)
        "Q1 FY26"                 → 2025-07-01 .. 2025-09-30
        "Q2 FY26"                 → 2025-10-01 .. 2025-12-31
        "Q3 FY26"                 → 2026-01-01 .. 2026-03-31
        "Q4 FY26"                 → 2026-04-01 .. 2026-06-30
        "FY26 Q1"                 → same as above

    Relative forms (anchored to *today* or the passed-in `today` arg)
        "this month"              → first..last day of today's month
        "last month"
        "this quarter"            → fiscal quarter of `today`
        "last quarter"
        "this year" / "this fy"   → fiscal-year-to-date of `today`
        "last year" / "last fy"   → previous full fiscal year
        "ytd" / "fytd"            → fiscal-year-to-date (start of FY .. today)
        "mtd"                     → first of current month .. today
        "qtd"                     → start of fiscal quarter .. today

Raises ValueError if the phrase can't be parsed — callers should catch
this and report it to the user verbatim so ARIA can ask a follow-up.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Tuple
from django.utils import timezone


# ---------------------------------------------------------------------------
# Fiscal-year helpers
# ---------------------------------------------------------------------------

FY_END_MONTH = 6   # June. Alpha Direct fiscal year ends 30-Jun.


def _fy_to_range(fy_year: int, n_months: int = 12) -> Tuple[date, date]:
    """Given an FY label like 25 (FY25), return (start, end) for `n_months`.

    FY25 means the fiscal year that ENDS in calendar 2025 — i.e. runs
    2024-07-01 to 2025-06-30. n_months trims the tail (FY26 9M ends
    2026-03-31).
    """
    if fy_year < 100:
        fy_year += 2000          # FY25 → 2025
    start = date(fy_year - 1, FY_END_MONTH + 1, 1)   # 1 Jul (prior cal year)
    if n_months < 1 or n_months > 12:
        raise ValueError(f'n_months out of range: {n_months}')
    # End = first of (start_month + n_months) minus one day.
    end_month_total = start.month + n_months - 1
    end_year  = start.year + (end_month_total - 1) // 12
    end_month = ((end_month_total - 1) % 12) + 1
    last_day  = calendar.monthrange(end_year, end_month)[1]
    end = date(end_year, end_month, last_day)
    return start, end


def _fiscal_year_of(d: date) -> int:
    """Return the 2-digit fiscal-year label for date `d`.

    Date 2025-08-15 → FY26 (returns 26).
    Date 2025-03-15 → FY25 (returns 25).
    """
    if d.month > FY_END_MONTH:
        return (d.year + 1) % 100
    return d.year % 100


def _fy_start(fy_year: int) -> date:
    if fy_year < 100:
        fy_year += 2000
    return date(fy_year - 1, FY_END_MONTH + 1, 1)


def _last_day_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _fiscal_quarter_range(fy_year: int, q: int) -> Tuple[date, date]:
    """Q1 = Jul-Sep, Q2 = Oct-Dec, Q3 = Jan-Mar, Q4 = Apr-Jun."""
    if q not in (1, 2, 3, 4):
        raise ValueError(f'fiscal quarter out of range: {q}')
    if fy_year < 100:
        fy_year += 2000
    # Q1 starts in (fy_year-1), Jul. Each quarter += 3 months.
    start_month_total = FY_END_MONTH + 1 + (q - 1) * 3   # 7, 10, 13, 16
    start_year  = fy_year - 1 + (start_month_total - 1) // 12
    start_month = ((start_month_total - 1) % 12) + 1
    start = date(start_year, start_month, 1)
    end_month_total = start_month_total + 2
    end_year  = fy_year - 1 + (end_month_total - 1) // 12
    end_month = ((end_month_total - 1) % 12) + 1
    end = _last_day_of_month(end_year, end_month)
    return start, end


def _fiscal_quarter_of(d: date) -> int:
    """Return 1..4 — which fiscal quarter `d` falls in."""
    # Map month → fiscal quarter (FY starts in Jul = month 7).
    m = d.month
    if m in (7, 8, 9):    return 1
    if m in (10, 11, 12): return 2
    if m in (1, 2, 3):    return 3
    return 4              # 4, 5, 6


# ---------------------------------------------------------------------------
# Month-name table
# ---------------------------------------------------------------------------

_MONTH_NAMES = {
    'jan': 1,  'january':   1,
    'feb': 2,  'february':  2,
    'mar': 3,  'march':     3,
    'apr': 4,  'april':     4,
    'may': 5,
    'jun': 6,  'june':      6,
    'jul': 7,  'july':      7,
    'aug': 8,  'august':    8,
    'sep': 9,  'sept':      9,  'september': 9,
    'oct': 10, 'october':   10,
    'nov': 11, 'november':  11,
    'dec': 12, 'december':  12,
}


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_RE_FY_NM = re.compile(
    r'^(?:fy|fiscal\s*year\s*)\s*(\d{2,4})(?:\s*[-_/]?\s*(\d{1,2})\s*m)?$',
    re.IGNORECASE,
)
_RE_FY_Q  = re.compile(
    r'^(?:fy\s*(\d{2,4})\s*q\s*([1-4])|q\s*([1-4])\s*fy\s*(\d{2,4}))$',
    re.IGNORECASE,
)
_RE_MONTH_NAME = re.compile(
    r'^([a-z]{3,9})\s*[-,/]?\s*(\d{2,4})$',
    re.IGNORECASE,
)
_RE_YYYY_MM = re.compile(r'^(\d{4})\s*[-/]\s*(\d{1,2})$')
_RE_ISO_DATE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def parse_period_phrase(phrase: str, *, today: date | None = None) -> Tuple[date, date]:
    """Translate a natural-language period phrase into (start_date, end_date).

    Args:
        phrase: free-text ARIA picked up from the user, e.g. "March 2026",
            "FY26 9M", "this quarter".
        today:  override for "today" — useful in tests. Defaults to
            Botswana's today (`timezone.localdate()`), never the UTC date
            the server clock reports.

    Returns:
        (start_date, end_date) — both inclusive, in Alpha Direct's BWP
        Jul–Jun fiscal calendar.

    Raises:
        ValueError if the phrase can't be parsed.
    """
    if phrase is None:
        raise ValueError('period phrase is required')

    s = re.sub(r'\s+', ' ', str(phrase).strip().lower())
    if not s:
        raise ValueError('period phrase is empty')

    today = today or timezone.localdate()

    # ── 1. Relative phrases ────────────────────────────────────────────
    relative = _try_relative(s, today)
    if relative:
        return relative

    # ── 2. FY with optional n-month tail ───────────────────────────────
    m = _RE_FY_NM.match(s)
    if m:
        fy = int(m.group(1))
        n_months = int(m.group(2)) if m.group(2) else 12
        return _fy_to_range(fy, n_months)

    # ── 3. FY quarter ──────────────────────────────────────────────────
    m = _RE_FY_Q.match(s)
    if m:
        if m.group(1):  # "FY26 Q1"
            fy = int(m.group(1)); q = int(m.group(2))
        else:           # "Q1 FY26"
            q  = int(m.group(3)); fy = int(m.group(4))
        return _fiscal_quarter_range(fy, q)

    # ── 4. Month-name year — "Mar 2026", "March 2026" ──────────────────
    m = _RE_MONTH_NAME.match(s)
    if m:
        name = m.group(1).lower()
        if name in _MONTH_NAMES:
            month = _MONTH_NAMES[name]
            year  = int(m.group(2))
            if year < 100:
                # "Mar 26" -> 2026 (this century)
                year += 2000
            start = date(year, month, 1)
            end   = _last_day_of_month(year, month)
            return start, end

    # ── 5. ISO YYYY-MM ─────────────────────────────────────────────────
    m = _RE_YYYY_MM.match(s)
    if m:
        year  = int(m.group(1))
        month = int(m.group(2))
        if 1 <= month <= 12:
            return date(year, month, 1), _last_day_of_month(year, month)

    # ── 6. Plain ISO date — treat as a single-day period ──────────────
    m = _RE_ISO_DATE.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        the_day = date(y, mo, d)
        return the_day, the_day

    raise ValueError(f'cannot parse period phrase: {phrase!r}')


# ---------------------------------------------------------------------------
# Relative-phrase resolver
# ---------------------------------------------------------------------------

def _try_relative(s: str, today: date) -> Tuple[date, date] | None:
    """Resolve relative phrases against `today`. Returns None on miss."""

    if s in ('today',):
        return today, today
    if s in ('yesterday',):
        from datetime import timedelta
        d = today - timedelta(days=1)
        return d, d

    if s in ('this month', 'current month', 'mtd', 'month to date'):
        start = today.replace(day=1)
        if s == 'mtd' or s == 'month to date':
            return start, today
        return start, _last_day_of_month(today.year, today.month)

    if s in ('last month', 'previous month', 'prior month'):
        if today.month == 1:
            y, m = today.year - 1, 12
        else:
            y, m = today.year, today.month - 1
        return date(y, m, 1), _last_day_of_month(y, m)

    if s in ('this quarter', 'current quarter', 'qtd', 'quarter to date'):
        fy = _fiscal_year_of(today)
        q  = _fiscal_quarter_of(today)
        q_start, q_end = _fiscal_quarter_range(fy, q)
        if s in ('qtd', 'quarter to date'):
            return q_start, today
        return q_start, q_end

    if s in ('last quarter', 'previous quarter', 'prior quarter'):
        fy = _fiscal_year_of(today)
        q  = _fiscal_quarter_of(today)
        if q == 1:
            return _fiscal_quarter_range(fy - 1, 4)
        return _fiscal_quarter_range(fy, q - 1)

    if s in ('this year', 'this fy', 'current fy', 'current fiscal year',
             'fytd', 'fy to date', 'ytd', 'year to date'):
        fy = _fiscal_year_of(today)
        start = _fy_start(fy)
        if s in ('fytd', 'fy to date', 'ytd', 'year to date'):
            return start, today
        return _fy_to_range(fy)

    if s in ('last year', 'last fy', 'previous fy', 'prior fy',
             'previous fiscal year', 'prior fiscal year'):
        fy = _fiscal_year_of(today)
        return _fy_to_range(fy - 1)

    return None
