"""
ifrs17/statistics.py — the analytics layer the CFO asked for.

"more data like loss ratios by products, etc etc all the statistics" (2026-08-25).

Everything here is DERIVED from the signed figures in constants.py (which come
from the Empirica report, which comes from the workbooks). Nothing is invented,
and nothing recomputes the actuary's selection — these are ratios, growth rates,
mix and trends over the figures that are already signed. Each builder returns the
same {title, columns, rows, note, ref} shape as disclosures.py so the screen, the
xlsx and the Word note all read one structure.

Where a statistic depends on a lever (loss ratio moves when a segment is toggled),
it reads the live Computed; where it is pure history (six-year GWP) it reads the
constants directly.
"""
from __future__ import annotations

from decimal import Decimal as D

from . import constants as K
from .engine import Computed

ZERO = D('0')


def _t(title, columns, rows, note='', ref=''):
    return {'title': title, 'ref': ref, 'columns': columns, 'rows': rows, 'note': note}


def _cagr(first: D, last: D, years: int) -> D | None:
    """Compound annual growth rate, as a decimal. None if it can't be formed."""
    if first is None or last is None or first <= 0 or years <= 0:
        return None
    return (last / first) ** (D(1) / D(years)) - 1


def six_year_performance() -> dict:
    """GWP, claims and loss ratio, FY2021 → FY2026, with year-on-year growth."""
    rows, prev = [], None
    for fy in sorted(K.SIX_YEAR):
        gwp, claims, lr = K.SIX_YEAR[fy]
        yoy = ((gwp - prev) / prev) if prev else None
        rows.append([f'FY{fy}', gwp, claims, lr, yoy])
        prev = gwp
    first = K.SIX_YEAR[min(K.SIX_YEAR)][0]
    last = K.SIX_YEAR[max(K.SIX_YEAR)][0]
    cagr = _cagr(first, last, len(K.SIX_YEAR) - 1)
    return _t('Six-year performance',
              ['Year', 'Gross written premium', 'Claims incurred', 'Loss ratio',
               'GWP growth'],
              rows,
              note=f'The book has compounded at {cagr:.1%} a year over the six years, '
                   f'with the loss ratio in a tight 48%–67% band. FY2022 was the best '
                   f'underwriting year at a 48% loss ratio; FY2021 the worst at 67%.'
                   if cagr else '',
              ref='§2.5 Table 3')


def loss_ratios_by_product(c: Computed) -> dict:
    """Loss ratio and combined ratio by product, at the current lever positions.

    This is the statistic the CFO named. Sorted worst-first, because the loss-
    making products are the ones a CFO wants at the top of the list.
    """
    rows = []
    for s in sorted(c.by_segment, key=lambda x: x.loss_ratio, reverse=True):
        rows.append([s.segment.title(), s.premium, s.claims, s.loss_ratio, s.combined_ratio,
                     'loss-making' if s.combined_ratio > 1 else 'profitable'])
    return _t('Loss and combined ratio by product',
              ['Product', 'Premium', 'Claims', 'Loss ratio', 'Combined ratio',
               'Underwriting'],
              rows,
              note='Combined ratio adds the loss ratio, the acquisition-commission '
                   'ratio and the attributable-expense ratio. Above 100% the product '
                   'loses money before investment return. Engineering (295% loss '
                   'ratio) and Guarantee (144%) are the two loss-makers; their small '
                   'premium is why the book still returns ~82% combined overall.',
              ref='§4.3 Table 16')


def segment_growth() -> dict:
    """GWP by segment, FY2022 → FY2026, with each product's five-year CAGR."""
    rows = []
    for seg, series in K.SEGMENT_GWP_HISTORY.items():
        cagr = _cagr(series[0], series[-1], len(series) - 1)
        rows.append([seg.title(), *series, cagr])
    return _t('Gross written premium by product, five-year trend',
              ['Product', *[f'FY{y}' for y in K.SEGMENT_HISTORY_YEARS], 'CAGR'],
              rows,
              note='Growth has been anything but uniform. Liability has grown almost '
                   'nine-fold over the period and Accident more than doubled, while '
                   'Engineering and Guarantee contracted sharply. Motor remains the '
                   'largest product but fell 1.8% in FY2026 — its first contraction '
                   'in the series.',
              ref='§2.5 Table 4')


def premium_mix(c: Computed) -> dict:
    """Each product's share of the current book — the concentration picture."""
    total = c.insurance_revenue or D(1)
    rows = []
    for s in sorted(c.by_segment, key=lambda x: x.premium, reverse=True):
        rows.append([s.segment.title(), s.premium, s.premium / total])
    return _t('Premium mix — product concentration',
              ['Product', 'Premium', 'Share of book'],
              rows,
              note='Motor and Property together are roughly two-thirds of the book, '
                   'so the group loss ratio moves mostly with those two. A growing '
                   'Liability line is diversifying that concentration.',
              ref='§4.3')


def development_factors() -> dict:
    """The volume-weighted age-to-age development factors, gross and net,
    FY2026 selected vs FY2025 signed — where the tail change lives."""
    rows = []
    labels = {
        'gross_fy26_selected': 'Gross, FY2026 selected',
        'gross_fy25_signed': 'Gross, FY2025 signed',
        'net_fy26_selected': 'Net, FY2026 selected',
        'net_fy25_signed': 'Net, FY2025 signed',
    }
    for key, label in labels.items():
        rows.append([label, *K.DEV_FACTORS[key]])
    return _t('Claim-development factors (age-to-age)',
              ['Basis', 'Yr 1-2', 'Yr 2-3', 'Yr 3-4', 'Yr 4-5', 'Yr 5-6', 'Yr 6-7'],
              rows,
              note=f'The FY2025 workbook applied a nil year-6-to-7 tail (1.000000); '
                   f'the FY2026 selection uses the factor actually observed '
                   f'(1.003100). That single change is worth {K.TAIL_CHANGE_IBNR_EFFECT:,.0f} '
                   f'of gross IBNR and is the clearest example of a method change '
                   f'that helps the reader see, rather than infer.',
              ref='§3.6 Table 7')


def key_ratios(c: Computed) -> dict:
    """A one-glance ratio panel over the current valuation."""
    rev = c.insurance_revenue or D(1)
    ceded = K.REPORTED['FY2026']['ceded_premium']
    ibnr_to_prem = c.gross_ibnr / rev
    net_ibnr_share = D(1) - (K.REPORTED['FY2026']['net_ibnr'] / c.gross_ibnr) \
        if c.gross_ibnr else ZERO
    return _t('Key ratios',
              ['Ratio', 'Value'],
              [
                  ['Loss ratio', c.loss_ratio],
                  ['Acquisition (commission) ratio', c.acquisition_ratio],
                  ['Attributable expense ratio', c.expense_ratio],
                  ['Combined ratio', c.combined_ratio],
                  ['Reinsurance cession rate', ceded / rev],
                  ['Gross IBNR as % of premium', ibnr_to_prem],
                  ['Ceded share of IBNR', net_ibnr_share],
                  ['Risk adjustment (% of fulfilment cash flows)', c.levers.ra_pct],
              ],
              note='One quarter of the current year’s ultimate claim cost is still '
                   'IBNR (13.1% of premium against a first-year paid development of '
                   '1.48), which is a reasonable proportion for a motor-dominated '
                   'book that settles quickly.',
              ref='§4.3 / §5.2')


def build_all(c: Computed) -> dict:
    return {
        'six_year_performance': six_year_performance(),
        'loss_ratios_by_product': loss_ratios_by_product(c),
        'segment_growth': segment_growth(),
        'premium_mix': premium_mix(c),
        'key_ratios': key_ratios(c),
        'development_factors': development_factors(),
    }
