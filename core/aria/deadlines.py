"""
core/aria/deadlines.py — Botswana statutory compliance calendar for ARIA.

CFO directive 2026-05-22: ARIA must remind on VAT, BURS, NBFIRA, PAYE,
WHT, SDL deadlines (etc). This module computes upcoming deadlines from
pure date math — no DB table yet (kept light; promoted to model in a
later phase).

Authoritative sources (cross-checked):
  - BURS Income Tax Act (Cap 52:01) + 2024 Tax Card
  - BURS VAT Act (Cap 50:03)
  - NBFIRA Insurance Industry Act 2015 + Returns Schedule
  - Workers' Compensation Act
  - Vocational Training Levy / Skills Development Levy

Alpha Direct fiscal year ends 30-Jun (FY label = ending year, e.g. FY26
runs Jul-2025 → Jun-2026).

Function `next_deadlines(today, n=10)` returns the next N deadlines
sorted ascending by due_date, each annotated with:
  - name        (human label)
  - authority   (BURS / NBFIRA / Min of Labour)
  - category    (vat / paye / wht / cit / nbfira_quarterly / etc.)
  - due_date    (datetime.date)
  - days_left   (int; negative = overdue)
  - frequency   (monthly / quarterly / annual)
  - basis       (period description)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import List
from django.utils import timezone


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FY_END_MONTH = 6   # Alpha Direct fiscal year ends 30-Jun


@dataclass
class Deadline:
    name: str
    authority: str
    category: str
    due_date: date
    days_left: int
    frequency: str
    basis: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d['due_date'] = self.due_date.isoformat()
        return d


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def _last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _next_after(today: date, candidate: date, freq: str) -> date:
    """Roll the candidate forward until it's strictly after `today`."""
    if candidate > today:
        return candidate
    # Roll forward according to frequency
    if freq == 'monthly':
        m = candidate.month + 1
        y = candidate.year + (1 if m > 12 else 0)
        m = ((m - 1) % 12) + 1
        return _next_after(today, candidate.replace(year=y, month=m, day=min(candidate.day, _last_day(y, m).day)), freq)
    if freq == 'quarterly':
        return _next_after(today, candidate.replace(year=candidate.year, month=candidate.month) + _q_step(candidate), freq)
    if freq == 'annual':
        return _next_after(today, candidate.replace(year=candidate.year + 1), freq)
    return candidate


def _q_step(d: date) -> timedelta:
    """Step a date roughly 3 months forward, preserving day-of-month."""
    new_month = d.month + 3
    new_year  = d.year + (new_month - 1) // 12
    new_month = ((new_month - 1) % 12) + 1
    last = _last_day(new_year, new_month)
    new_day = min(d.day, last.day)
    return date(new_year, new_month, new_day) - d


# ---------------------------------------------------------------------------
# Compliance schedule
# ---------------------------------------------------------------------------
def _generate(today: date) -> List[Deadline]:
    """Compute the next instance of every recurring deadline."""
    items: list[Deadline] = []
    add = items.append

    def add_dl(name, authority, category, due, freq, basis):
        add(Deadline(
            name=name, authority=authority, category=category,
            due_date=due, days_left=(due - today).days,
            frequency=freq, basis=basis,
        ))

    # ── BURS — monthly returns ────────────────────────────────────────
    # PAYE (ITW7A): by 15th of following month
    paye = _next_after(today, date(today.year, today.month, 15), 'monthly')
    prev_month_label = _prev_period_label(paye, months=1)
    add_dl('PAYE return (ITW7A)', 'BURS', 'paye', paye, 'monthly',
           f'For payroll month {prev_month_label}')

    # Withholding Tax return: by 15th of following month
    wht = _next_after(today, date(today.year, today.month, 15), 'monthly')
    add_dl('Withholding Tax return', 'BURS', 'wht', wht, 'monthly',
           f'For payment month {prev_month_label}')

    # Skills Development Levy: by 15th of following month
    sdl = _next_after(today, date(today.year, today.month, 15), 'monthly')
    add_dl('Skills Development Levy', 'Min of Labour', 'sdl', sdl, 'monthly',
           f'For payroll month {prev_month_label}')

    # ── BURS — VAT (monthly, due 25th of following month) ────────────
    vat = _next_after(today, date(today.year, today.month, 25), 'monthly')
    add_dl('VAT 200 return + payment', 'BURS', 'vat', vat, 'monthly',
           f'For VAT period {prev_month_label}')

    # ── BURS — corporate tax provisional (ITA22) ─────────────────────
    # Four equal instalments at 30 Sep, 31 Dec, 31 Mar, 30 Jun (FY-end).
    for m in (9, 12, 3, 6):
        # Resolve next occurrence of last-day-of(m).
        y = today.year
        d = _last_day(y, m)
        while d <= today:
            y += 1
            d = _last_day(y, m)
        idx = {9: 'Q1', 12: 'Q2', 3: 'Q3', 6: 'Q4'}[m]
        add_dl(f'CIT provisional payment ({idx})', 'BURS', 'cit_provisional',
               d, 'annual', f'Quarterly instalment {idx} for FY ending Jun')

    # ── BURS — corporate tax return (ITA22) ──────────────────────────
    # Annual return: 4 months after FY-end → 31 Oct each year.
    cit_y = today.year
    cit_due = date(cit_y, 10, 31)
    if cit_due <= today:
        cit_due = date(cit_y + 1, 10, 31)
    add_dl('CIT annual return (ITA22)', 'BURS', 'cit_annual', cit_due, 'annual',
           f'For FY ending {cit_y - 1 if today.month < 7 else cit_y}-06-30')

    # ── NBFIRA — quarterly returns (45 days after quarter-end) ───────
    for qm in (3, 6, 9, 12):
        y = today.year
        q_end = _last_day(y, qm)
        nbf_due = q_end + timedelta(days=45)
        while nbf_due <= today:
            y += 1
            q_end = _last_day(y, qm)
            nbf_due = q_end + timedelta(days=45)
        q_label = {3: 'Q3 (Jan-Mar)', 6: 'Q4 (Apr-Jun)',
                   9: 'Q1 (Jul-Sep)', 12: 'Q2 (Oct-Dec)'}[qm]
        add_dl(f'NBFIRA quarterly return — {q_label}', 'NBFIRA', 'nbfira_quarterly',
               nbf_due, 'quarterly', f'Quarter ending {q_end.isoformat()}')

    # ── NBFIRA — capital adequacy (quarterly, 30 days after Q end) ───
    for qm in (3, 6, 9, 12):
        y = today.year
        q_end = _last_day(y, qm)
        cap_due = q_end + timedelta(days=30)
        while cap_due <= today:
            y += 1
            q_end = _last_day(y, qm)
            cap_due = q_end + timedelta(days=30)
        add_dl(f'NBFIRA capital adequacy ({_q_label(qm)})', 'NBFIRA',
               'nbfira_capital', cap_due, 'quarterly',
               f'Capital position as at {q_end.isoformat()}')

    # ── NBFIRA — annual return (4 months after FY-end) ───────────────
    nbf_annual = date(today.year, 10, 31)
    if nbf_annual <= today:
        nbf_annual = date(today.year + 1, 10, 31)
    add_dl('NBFIRA annual return', 'NBFIRA', 'nbfira_annual', nbf_annual,
           'annual', f'For FY ending {nbf_annual.year - 1}-06-30')

    # ── Workers' Compensation annual (30 April) ──────────────────────
    wc = date(today.year, 4, 30)
    if wc <= today:
        wc = date(today.year + 1, 4, 30)
    add_dl("Workers' Compensation annual", 'Min of Labour', 'workmens_comp',
           wc, 'annual', 'Annual wage declaration + premium')

    # ── Annual Financial Statements to BURS (4 months after FY-end) ──
    afs = date(today.year, 10, 31)
    if afs <= today:
        afs = date(today.year + 1, 10, 31)
    add_dl('Annual Financial Statements (BURS)', 'BURS', 'afs', afs, 'annual',
           f'For FY ending {afs.year - 1}-06-30')

    return items


def _q_label(month: int) -> str:
    return {3: 'Q3', 6: 'Q4', 9: 'Q1', 12: 'Q2'}.get(month, '?')


def _prev_period_label(due: date, months: int = 1) -> str:
    """Human label for the period this deadline covers."""
    m = due.month - months
    y = due.year
    while m <= 0:
        m += 12
        y -= 1
    return f'{y}-{m:02d}'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def next_deadlines(today: date | None = None, n: int = 10,
                   urgency_days: int = 14) -> List[dict]:
    """Return the next `n` deadlines sorted ascending by due_date."""
    today = today or timezone.localdate()
    items = sorted(_generate(today), key=lambda d: d.due_date)[:n]
    return [d.to_dict() for d in items]


def urgent_deadlines(today: date | None = None, urgency_days: int = 7) -> List[dict]:
    """Subset of next_deadlines whose due_date is within `urgency_days`."""
    today = today or timezone.localdate()
    all_items = next_deadlines(today, n=50)
    return [d for d in all_items if 0 <= d['days_left'] <= urgency_days]


def overdue_deadlines(today: date | None = None) -> List[dict]:
    """Deadlines whose due_date has passed (days_left < 0). None typically —
    these are computed forward; this is here so the prompt can flag 'caught
    up'."""
    today = today or timezone.localdate()
    items = _generate(today)
    out = [d.to_dict() for d in items if d.days_left < 0]
    return sorted(out, key=lambda d: d['due_date'])
