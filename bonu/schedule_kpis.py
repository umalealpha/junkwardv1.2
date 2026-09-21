"""bonu/schedule_kpis.py — the numbers Omni computes from the schedule.

The insight the CFO surfaced on 13-Aug: the schedule can carry a wrong total on a
single row and every ratio on the tab quietly follows it. Kutlo's "Premiums (- VAT
& Commission)" total was overstated by 6,499,670.08, so his loss ratio read 51%
against a real 89% and his combined 66% against a real 116%.

So Omni derives the ratios itself, from the same underlying cells, and reports
them beside the author's — the same "our figure beside the author's" control the
sheet totals now use. A disagreement is visible instead of silently taken on
trust.

Also includes the break-even premium, because the natural next question is "how
much more premium would this scheme need to stop losing money" — a figure the
book would need every negotiation, and Omni already holds every input.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from .models import BonuScheduleSheet
from .schedule import _money
from .schedule_validate import _period_columns


ZERO = Decimal('0')


def _q(x, p='0.01') -> Decimal:
    return Decimal(str(x)).quantize(Decimal(p), rounding=ROUND_HALF_UP)


def _fmt_money(x) -> str:
    """Money as a grouped string with thousands commas + 2dp, e.g. 11,912,578.57
    (CFO 2026-08-30). Money figures only — ratios/percentages must stay ungrouped.
    The 'differ' red-gap flags compare the Decimal values, not these strings, so
    formatting here does not change which cells are flagged as disagreeing.

    Named distinctly from the imported Decimal parser `_money` (bonu.schedule):
    this one FORMATS for display, that one PARSES cells for arithmetic. An earlier
    version named this `_money` too, shadowing the import, so `_months_sum` summed
    formatted strings and crashed (Decimal + str)."""
    try:
        return f"{_q(x):,.2f}"
    except (TypeError, ValueError, ArithmeticError):
        return str(x)


def _summary_row_value(summary: BonuScheduleSheet, label: str,
                       col: str) -> Optional[Decimal]:
    if not summary or not summary.columns:
        return None
    label_col = summary.columns[0]
    target = label.strip().lower()
    for r in summary.rows.all():
        if str(r.cells.get(label_col, '')).strip().lower() == target:
            v = r.cells.get(col)
            return _money(v) if v not in (None, '') else None
    return None


def _months_sum(summary: BonuScheduleSheet, label: str) -> Optional[Decimal]:
    """Sum a summary line's period cells — everything between label and Totals.

    Reuses `_period_columns` (which stops at the Totals cell) so scratch cells to
    the right of Totals — the same Column 21..31 that produced 57 false criticals
    against the validator — cannot silently corrupt the KPI base sum on a future
    upload. Works today only because Kutlo's P&L rows have blank scratch cells;
    the next month's file could easily change that."""
    if not summary or not summary.columns:
        return None
    label_col = summary.columns[0]
    period_cols = _period_columns(summary)
    target = label.strip().lower()
    for r in summary.rows.all():
        if str(r.cells.get(label_col, '')).strip().lower() == target:
            return sum((_money(r.cells.get(c)) for c in period_cols), ZERO)
    return None


def _describe_period(period_cols: list) -> str:
    """Human-readable span for the period columns — never hardcoded, so next
    month's file describes itself instead of carrying last month's label."""
    if not period_cols:
        return 'No period columns'
    return f'{len(period_cols)} periods, {period_cols[0]} to {period_cols[-1]}'


def compute() -> dict:
    """Every KPI derived from the schedule, plus what the author's own cells say
    for the same figure. The frontend then shows the two side by side, in the
    same style the sheet totals use, so a disagreement is on screen rather than
    silently taken on trust."""
    summary = BonuScheduleSheet.objects.filter(key='summary').first()
    if not summary:
        return {'available': False, 'reason': 'no-summary-tab'}

    totals_col = next((c for c in summary.columns
                       if c.strip().lower() == 'totals'), '')
    if not totals_col:
        return {'available': False, 'reason': 'no-totals-column'}

    # Author's own totals row values — what the workbook itself says.
    stated_revenue = _summary_row_value(summary, 'Total Revenue', totals_col) or ZERO
    stated_vat     = _summary_row_value(summary, 'Vat @ 14%', totals_col) or ZERO
    stated_comm    = _summary_row_value(summary, 'Commission @ 15%', totals_col) or ZERO
    stated_claims  = _summary_row_value(summary, 'Client Claims', totals_col) or ZERO
    stated_admin   = _summary_row_value(summary, 'Admin Expenses', totals_col) or ZERO
    stated_pl      = _summary_row_value(summary, 'Profit/Loss', totals_col) or ZERO
    stated_base    = _summary_row_value(summary, 'Premiums(- VAT & Commission)',
                                        totals_col) or ZERO

    # Derived — sum the monthly cells, which cannot be over-typed.
    computed_revenue = _months_sum(summary, 'Total Revenue') or stated_revenue
    computed_vat     = _months_sum(summary, 'Vat @ 14%') or stated_vat
    computed_comm    = _months_sum(summary, 'Commission @ 15%') or stated_comm
    computed_claims  = _months_sum(summary, 'Client Claims') or stated_claims
    computed_admin   = _months_sum(summary, 'Admin Expenses') or stated_admin
    # VAT and commission are already negative on the P&L, so ADD them to get the
    # net-of-both figure. Claims and admin are already negative too.
    net_premium = computed_revenue + computed_vat + computed_comm
    pl          = net_premium + computed_claims + computed_admin

    # Ratios — negative claims / admin become positive when divided by net.
    def pct(numer, denom):
        if not denom:
            return None
        return _q((abs(numer) / abs(denom)) * 100)

    loss_ratio    = pct(computed_claims, net_premium)
    expense_ratio = pct(computed_admin, net_premium)
    combined      = None if (loss_ratio is None or expense_ratio is None) \
                    else _q(loss_ratio + expense_ratio)

    stated_loss    = pct(stated_claims, stated_base)
    stated_expense = pct(stated_admin, stated_base)
    stated_combined = None if (stated_loss is None or stated_expense is None) \
                      else _q(stated_loss + stated_expense)

    # Break-even: what premium, at today's cost-to-cover, drives combined to 100%.
    # Cost to cover = |claims| + |admin|. Net premium at break-even = cost. The
    # gross uplift is then cost / (net/gross), preserving today's VAT+commission
    # take-rate.
    cost = abs(computed_claims) + abs(computed_admin)
    take_rate = (net_premium / computed_revenue) if computed_revenue else None
    breakeven_gross = _q(cost / take_rate) if take_rate else None
    uplift_pct = None if not (breakeven_gross and computed_revenue) \
                 else _q((breakeven_gross / computed_revenue - 1) * 100)

    period_cols = _period_columns(summary)
    return {
        'available': True,
        'period':     _describe_period(period_cols),
        'revenue':    {'omni': _fmt_money(computed_revenue),
                       'author': _fmt_money(stated_revenue)},
        'vat':        {'omni': _fmt_money(computed_vat),
                       'author': _fmt_money(stated_vat)},
        'commission': {'omni': _fmt_money(computed_comm),
                       'author': _fmt_money(stated_comm)},
        'net_premium': {'omni': _fmt_money(net_premium),
                        'author': _fmt_money(stated_base),
                        'differ': stated_base != _q(net_premium)},
        'claims':     {'omni': _fmt_money(abs(computed_claims)),
                       'author': _fmt_money(abs(stated_claims))},
        'admin':      {'omni': _fmt_money(abs(computed_admin)),
                       'author': _fmt_money(abs(stated_admin))},
        'profit_loss': {'omni': _fmt_money(pl),
                        'author': _fmt_money(stated_pl)},
        'loss_ratio':    {'omni': None if loss_ratio    is None else str(loss_ratio),
                          'author': None if stated_loss is None else str(stated_loss),
                          'differ': loss_ratio != stated_loss},
        'expense_ratio': {'omni': None if expense_ratio    is None else str(expense_ratio),
                          'author': None if stated_expense is None else str(stated_expense),
                          'differ': expense_ratio != stated_expense},
        'combined_ratio': {'omni': None if combined    is None else str(combined),
                           'author': None if stated_combined is None else str(stated_combined),
                           'differ': combined != stated_combined},
        'break_even': {
            'gross_needed': None if breakeven_gross is None else _fmt_money(breakeven_gross),
            'uplift_pct':   None if uplift_pct is None else str(uplift_pct),
            'note': ('Gross premium that, at today\'s claims and admin, drives the '
                     'combined ratio to 100%. Assumes the VAT + commission take-rate '
                     f'stays at {_q((take_rate or ZERO) * 100)}% of gross.'
                     if take_rate else ''),
        },
    }
