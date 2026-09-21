"""
finance_report/omni_sources.py — the two premium lines that live in Omni, not in
an uploaded workbook.

Union Legal Insurance and Health Insurance each feed one figure a month, and the
CFO's instruction (31-Aug-2026) is to take them from Omni rather than type them
in: Union Legal from the BONU premiums schedule, Health from the health premium
bordereaux. So this reads those two, by month, and hands back rows the report
adds to its premium side.

Read-only. Both sources are already-loaded operational data; nothing here writes
or posts. If a source is empty the line simply comes back as nothing, and the
report shows a zero for it rather than failing — the same as an unentered manual
line did before.
"""
from __future__ import annotations

from decimal import Decimal

from .engine import HEALTH_INSURANCE, UNION_LEGAL, money

_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
           'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
_MONTH_NUM = {m: i + 1 for i, m in enumerate(_MONTHS)}


def _month_key(year: int, month_num: int) -> str:
    return f'{year:04d}-{month_num:02d}'


def union_legal_rows(periods):
    """Union Legal premium by month, from the BONU premiums schedule.

    The BONU schedule holds a month NAME (jan…dec) and a booked premium, with no
    year on the row. So the caller passes the reporting period ('YYYY-MM' list)
    and each booked month is matched to the one entry in that period carrying the
    same month — which is unambiguous for a normal 12-month financial year.

    Returns a list of {'month','line','amount'} for engine.manual rows, plus a
    short provenance dict the report surfaces so the figure is never silently
    trusted.
    """
    from bonu.schedule_insights import premium_gaps

    try:
        gaps = premium_gaps()
    except Exception:      # noqa: BLE001 - a missing/empty schedule is not an error here
        return [], {'available': False, 'source': 'BONU premiums schedule'}

    if not gaps.get('available'):
        return [], {'available': False, 'source': 'BONU premiums schedule'}

    # month-name(lower3) -> the YYYY-MM in the requested period, if exactly one.
    want = {}
    for ym in periods or []:
        try:
            _, mm = ym.split('-')
            name = _MONTHS[int(mm) - 1]
        except (ValueError, IndexError):
            continue
        want.setdefault(name, []).append(ym)

    rows, matched, unmatched = [], 0, []
    for entry in gaps.get('months', []):
        amount = money(entry.get('amount'))
        if amount == 0:
            continue
        name = (entry.get('month') or '').strip().lower()[:3]
        targets = want.get(name)
        if periods and not targets:
            unmatched.append(entry.get('month'))
            continue
        if targets and len(targets) == 1:
            ym = targets[0]
        elif not periods:
            # No period given: fall back to the current FY is not knowable here,
            # so skip — the report defaults its period from the premium board.
            unmatched.append(entry.get('month'))
            continue
        else:
            unmatched.append(entry.get('month'))       # month appears twice in the window
            continue
        rows.append({'month': ym, 'line': UNION_LEGAL, 'amount': str(amount)})
        matched += 1

    return rows, {
        'available': True,
        'source': 'BONU premiums schedule',
        'months_matched': matched,
        'months_unmatched': [m for m in unmatched if m],
    }


def health_rows(periods):
    """Health Insurance premium by month, from the health premium bordereaux.

    Each REVENUE upload carries its period year and month and a gross premium
    total; summed per (year, month) that is the Health line, keyed to 'YYYY-MM'.
    A superseded or failed upload is left out.
    """
    from healthcare.models import HealthcareUpload

    keep = set(periods) if periods else None
    by_month: dict[str, Decimal] = {}
    qs = (HealthcareUpload.objects
          .filter(kind=HealthcareUpload.Kind.REVENUE,
                  status=HealthcareUpload.Status.PARSED)
          .exclude(period_year__isnull=True)
          .exclude(period_month__isnull=True))
    for up in qs.only('period_year', 'period_month', 'gross_amount', 'superseded'):
        if getattr(up, 'superseded', False):
            continue
        ym = _month_key(up.period_year, up.period_month)
        if keep is not None and ym not in keep:
            continue
        by_month[ym] = by_month.get(ym, Decimal('0')) + (up.gross_amount or Decimal('0'))

    rows = [{'month': ym, 'line': HEALTH_INSURANCE, 'amount': str(money(amt))}
            for ym, amt in sorted(by_month.items())]
    return rows, {
        'available': bool(rows),
        'source': 'Health premium bordereaux (Revenue uploads)',
        'months_matched': len(rows),
    }
