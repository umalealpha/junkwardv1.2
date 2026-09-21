"""
frozen_drift.py — drift detection between live dashboard values and the
CFO-locked FrozenFigure rows.

Per handover § P1 (2026-05-17): on every dashboard load, the frontend
fetches `/api/v1/reports/frozen-drift/?period=...&company=...`. The
endpoint compares the four tile values (GWP, PAT, Total Assets,
Cash & Bank) to the corresponding FrozenFigure for the selected
period. Tiles that exceed `tolerance_pct` are returned in the
`drifts` array; the frontend renders a red banner on `/dashboard`.

Period mapping:
    FY25_Jun2025  → 2024-07-01 .. 2025-06-30
    FY26_Mar2026  → 2025-07-01 .. 2026-03-31

Only ADIC standalone is checked (the frozen figures are ADIC-only).
For any other company filter the endpoint returns drifts: [].
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal


# Map period code → (from_date, to_date)
PERIODS = {
    'FY25_Jun2025':  (date(2024, 7, 1), date(2025, 6, 30)),
    'FY26_Mar2026':  (date(2025, 7, 1), date(2026, 3, 31)),
    'FY26_Jun2026':  (date(2025, 7, 1), date(2026, 6, 30)),
}


def _adic_company():
    from core.models import Company
    try:
        return Company.objects.get(code__iexact='ADIC')
    except Company.DoesNotExist:
        return None


def _zero():
    return Decimal('0.00')


def build_frozen_drift(period: str, company_id=None) -> dict:
    """Compare live values to the 4 frozen figures for `period`.

    Returns:
        {
          'period':     'FY25_Jun2025' | 'FY26_Mar2026',
          'from_date':  '...',
          'to_date':    '...',
          'company':    'ADIC' | <code> | None,
          'drifts':     [ {label, frozen, actual, diff, diff_pct, ...}, ... ],
          'all_ok':     True/False,
        }
    """
    from ledger.models import FrozenFigure

    if period not in PERIODS:
        return {
            'period':   period,
            'drifts':   [],
            'all_ok':   True,
            'reason':   f'Unknown period {period!r}; known: {sorted(PERIODS)}',
        }

    from_date, to_date = PERIODS[period]
    adic = _adic_company()
    if adic is None:
        return {
            'period':   period,
            'drifts':   [],
            'all_ok':   True,
            'reason':   'ADIC company not found in core.Company',
        }

    # Drift comparison only applies when the dashboard is filtered to
    # ADIC. For other companies, there is no frozen figure to compare
    # against, so we return drifts: [].
    if company_id is not None and str(company_id) != str(adic.id):
        return {
            'period':    period,
            'from_date': str(from_date),
            'to_date':   str(to_date),
            'company':   None,
            'drifts':    [],
            'all_ok':    True,
            'reason':    'Drift check only applies to ADIC standalone.',
        }

    # Resolve actuals — one call per figure, exactly as the dashboard does.
    actuals = _compute_actuals(adic.id, from_date, to_date)

    # Compare to each FrozenFigure for this period.
    drifts = []
    for ff in FrozenFigure.objects.filter(period=period, is_active=True):
        actual = actuals.get(ff.line_label)
        if actual is None:
            continue
        if ff.acknowledged_by_override:
            # Admin acknowledged — silence the banner for THIS figure.
            continue
        drift = ff.drift_for(actual)
        if drift is not None:
            drifts.append(drift)

    return {
        'period':    period,
        'from_date': str(from_date),
        'to_date':   str(to_date),
        'company':   'ADIC',
        'drifts':    drifts,
        'all_ok':    len(drifts) == 0,
        'actuals':   {k: str(v) for k, v in actuals.items()},
    }


def _compute_actuals(adic_id, from_date, to_date) -> dict:
    """Return the four current dashboard values for ADIC at `to_date`.

    Keys mirror the FrozenFigure.line_label values seeded by
    `seed_frozen_figures`: 'GWP', 'PAT', 'Total Assets', 'Cash & Bank'.
    """
    from reporting.ma_pl import build_ma_pl
    from reporting.reports import build_balance_sheet, build_cash_position

    out = {}

    # MA P&L gives us GWP + PAT (the only frozen P&L lines).
    try:
        mapl = build_ma_pl(from_date, to_date, company_id=adic_id)
        totals = mapl.get('totals') or {}
        if 'gross_written_premium' in totals:
            out['GWP'] = Decimal(str(totals['gross_written_premium']))
        if 'pat' in totals:
            out['PAT'] = Decimal(str(totals['pat']))
    except Exception:
        pass

    # Balance Sheet → Total Assets.
    try:
        bs = build_balance_sheet(to_date, company_id=adic_id)
        assets = (bs.get('assets') or {})
        # Total can be at assets.total, or assets.totals.total_assets — both
        # patterns appear in reports.py history; try assets.total first.
        ta = assets.get('total')
        if ta is None:
            totals = (bs.get('totals') or {})
            ta = totals.get('total_assets')
        if ta is not None:
            out['Total Assets'] = Decimal(str(ta))
    except Exception:
        pass

    # Cash Position — period-aware via `as_of`.
    try:
        try:
            cp = build_cash_position(company_id=adic_id, as_of=to_date)
        except TypeError:
            # Older signature without as_of — fall back.
            cp = build_cash_position(company_id=adic_id)
        cash = cp.get('total_bwp')
        if cash is not None:
            out['Cash & Bank'] = Decimal(str(cash))
    except Exception:
        pass

    return out
