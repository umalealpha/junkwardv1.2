"""
reporting/reports.py

Pure business-logic functions that return Python dicts suitable for JSON
serialisation.  No HTTP concerns — views and management commands both call these.

Functions:
  build_trial_balance(as_of)
  build_profit_loss(from_date, to_date)
  build_balance_sheet(as_of)
  build_ar_aging(as_of)
  build_ap_aging(as_of)
  build_cash_position()
  build_general_ledger(account_code, from_date, to_date)
  build_budget_vs_actual(from_date, to_date, department)
  build_vat_return(from_date, to_date)
  build_expense_analysis(from_date, to_date)
  build_management_pack(from_date, to_date)
"""

import functools
import logging

from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')
TWO  = Decimal('0.01')


def _cache_report(ttl=20):
    """PERF (2026-07-17): the dashboards auto-refresh every 60s and each open
    tab re-fires the whole set of report builders — so the same (company,
    dates) report is recomputed from the GL dozens of times a minute across
    tabs/users. Cache the built dict for a short window so concurrent callers
    collapse to one computation. TTL is deliberately short (20s) so figures
    stay effectively live for reconciliation."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = f'rpt:{fn.__name__}:{args!r}:{sorted(kwargs.items())!r}'
            hit = cache.get(key)
            if hit is not None:
                return hit
            val = fn(*args, **kwargs)
            cache.set(key, val, ttl)
            return val
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(val):
    """Decimal -> 2-dp string suitable for JSON."""
    if val is None:
        return '0.00'
    return str(val.quantize(TWO))


def _frozen_overrides(from_date, to_date, company_id) -> dict:
    """Return frozen figures that match this exact period for ADIC.

    Returns dict of line_label -> value_bwp (e.g. {'PAT': Decimal, 'GWP': Decimal}).
    Empty dict if no match.
    """
    try:
        from ledger.models import FrozenFigure
        from reporting.frozen_drift import PERIODS, _adic_company
    except Exception:
        return {}
    adic = _adic_company()
    if adic is None:
        return {}
    if company_id is not None and str(company_id) != str(adic.id):
        return {}
    for period_code, (pf, pt) in PERIODS.items():
        if pf == from_date and pt == to_date:
            return {
                ff.line_label: ff.value_bwp
                for ff in FrozenFigure.objects.filter(
                    period=period_code, is_active=True,
                )
            }
    return {}


def _agg_je_lines(qs):
    """
    Aggregate JournalEntryLines by account.
    Returns { account_id: (total_dr, total_cr) }
    """
    rows = (
        qs.values('account_id')
        .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
    )
    return {
        r['account_id']: (r['dr'] or ZERO, r['cr'] or ZERO)
        for r in rows
    }


def _signed_balance(dr, cr, account_type):
    """
    Signed closing balance for an account.
    Positive = normal side (Dr for asset/expense, Cr for liability/equity/revenue).
    """
    if account_type in ('asset', 'expense'):
        return dr - cr
    return cr - dr


def _company_currency(company_id=None) -> str:
    """
    Return the functional currency code for *company_id*, or 'BWP' fallback.

    CFO directive 2026-05-21 (Prompt Currency Fix). Reports MUST surface
    the entity's functional currency so the UI / Excel export can label
    amounts correctly (USD for Insurtech, INR for ADRISK, ZAR for ADSA,
    ZMW for AIZ, BWP for the rest).
    """
    if not company_id:
        return 'BWP'
    try:
        from core.models import Company
        v = (Company.objects
             .filter(id=company_id)
             .values_list('base_currency_id', flat=True)
             .first())
        if v:
            return str(v)
    except Exception:    # noqa: BLE001
        pass
    return 'BWP'


def _fy_end_month_for(company_id=None) -> int:
    """
    Return the fiscal-year-end month for the given company (1-12).

    CFO directive 2026-05-20 (Manus TB-audit). Was previously hard-coded to
    July (= June-end year) regardless of which company you asked about.
    Now reads `Company.fy_end_month` — the field has existed since
    migration 0008 but no caller used it.

    Falls back to Alpha Direct group default (6 = June) when:
      - no company_id supplied
      - company_id not found
      - company has NULL fy_end_month
    """
    if not company_id:
        return 6
    try:
        from core.models import Company
        v = (Company.objects
             .filter(id=company_id)
             .values_list('fy_end_month', flat=True)
             .first())
        if v in range(1, 13):
            return int(v)
    except Exception:    # noqa: BLE001
        pass
    return 6


def _get_fiscal_year_start(dt, fy_end_month: int = 6):
    """
    First day of the fiscal year that contains *dt* for an entity whose
    fiscal year ends on the last day of `fy_end_month`.

    Examples:
      fy_end_month=6  (Botswana / SA standard, Jul–Jun):
        dt=2025-03-15 → 2024-07-01
        dt=2024-09-30 → 2024-07-01
      fy_end_month=12 (calendar year):
        dt=2025-03-15 → 2025-01-01
      fy_end_month=3  (Apr–Mar):
        dt=2025-06-01 → 2025-04-01
        dt=2025-02-01 → 2024-04-01
    """
    if fy_end_month == 12:
        return date(dt.year, 1, 1)
    # The FY that contains dt ends on fy_end_month/<year>.
    # If dt.month > fy_end_month → FY ends NEXT year, so it starts in the
    # current calendar year on (fy_end_month + 1).
    # If dt.month <= fy_end_month → FY ends THIS year, so it starts the
    # prior calendar year on (fy_end_month + 1).
    start_month = fy_end_month + 1
    if dt.month > fy_end_month:
        return date(dt.year, start_month, 1)
    return date(dt.year - 1, start_month, 1)


def _get_period_start(as_of, company_id=None):
    """
    Return the start of the fiscal period (month) that contains *as_of*.

    CFO directive 2026-05-20: prefer the per-company FiscalPeriod row when
    available; fall back to the global (legacy NULL-company) row; finally
    fall back to the fiscal-year start.
    """
    from ledger.models import FiscalPeriod
    qs = FiscalPeriod.objects.filter(
        start_date__lte=as_of, end_date__gte=as_of,
    )
    if company_id:
        scoped = qs.filter(company_id=company_id).first()
        if scoped:
            return scoped.start_date
    global_row = qs.filter(company__isnull=True).first()
    if global_row:
        return global_row.start_date
    return _get_fiscal_year_start(as_of, _fy_end_month_for(company_id))


def _posted_lines(company_id=None):
    """
    Base queryset: JE lines on POSTED entries only.
    Optionally scoped to a single company (subsidiary).
    """
    from ledger.models import JournalEntry, JournalEntryLine
    qs = JournalEntryLine.objects.filter(
        journal_entry__status=JournalEntry.Status.POSTED
    )
    if company_id:
        qs = qs.filter(journal_entry__company_id=company_id)
    return qs


# ---------------------------------------------------------------------------
# 1. Trial Balance
# ---------------------------------------------------------------------------

def build_trial_balance(
    to_date=None, *, from_date=None, company_id=None, as_of=None,
):
    """
    Trial balance for the period [from_date, to_date], with opening / period /
    closing breakdown.

    CFO directive 2026-05-20 (Manus TB-audit): a TB is ALWAYS for a period.
    The legacy `as_of` single-date signature is kept for backward
    compatibility — callers that pass only `as_of` get `from_date` derived
    from the fiscal-year start (computed using `Company.fy_end_month`).

    Args:
        to_date:    period end (inclusive). Required.
        from_date:  period start (inclusive). Defaults to the fiscal-year
                    start that contains `to_date`.
        company_id: scope to one Company. Strongly recommended (otherwise
                    the report rolls up every entity).
        as_of:      legacy alias for `to_date`. If both are passed,
                    `to_date` wins.

    Returns the report dict. `as_of` in the response is set to the period
    end (compat); a new `from_date` field carries the period start.

    Opening balance  = all posted JEs with entry_date < from_date.
    Period movements = posted JEs whose [period_start, entry_date] window
                       overlaps the requested range. Most JEs are
                       point-in-time (period_start IS NULL) so the test
                       collapses to `from_date <= entry_date <= to_date`.
    Closing balance  = opening balance + period movement (signed by account
                       natural side) — standard trial-balance carry-forward.
                       CFO directive 2026-06-04 REVERSES the 2026-05-21
                       "period-movement-only" rule (Tb calculations.docx):
                       accounts with an opening balance and no in-period
                       movement were showing CLOSING 0.00, which read as lost
                       data (ADSA: 32/33 accounts). Opening stays its own
                       informational column; the TB still balances (Dr = Cr).
    """
    from ledger.models import Account

    # Legacy callers passed positional as_of. Resolve.
    if to_date is None and as_of is not None:
        to_date = as_of
    if to_date is None:
        raise ValueError('build_trial_balance: to_date (or legacy as_of) is required.')

    if from_date is None:
        from_date = _get_fiscal_year_start(to_date, _fy_end_month_for(company_id))

    base = _posted_lines(company_id=company_id)

    opening_agg = _agg_je_lines(
        base.filter(journal_entry__entry_date__lt=from_date)
    )
    period_agg = _agg_je_lines(
        base.filter(
            journal_entry__entry_date__gte=from_date,
            journal_entry__entry_date__lte=to_date,
        )
    )

    # GL-001 fix (Oprah QA, 2026-06-08): P&L accounts (revenue/expense) must
    # RESET to zero at each fiscal-year start — they never carry a prior year's
    # closing balance as an opening balance. Within a fiscal year they still
    # accumulate, so a mid-year TB shows P&L opening = current-FY activity up to
    # from_date. Balance-sheet accounts (asset/liability/equity) carry forward
    # unchanged. `opening_agg` above (all lines < from_date) is used for BS;
    # `pl_opening_agg` (only lines in [fiscal_year_start(from_date), from_date))
    # is used for P&L — empty, hence zero, when from_date IS the FY start.
    _period_fy_start = _get_fiscal_year_start(from_date, _fy_end_month_for(company_id))
    pl_opening_agg = (
        _agg_je_lines(base.filter(
            journal_entry__entry_date__gte=_period_fy_start,
            journal_entry__entry_date__lt=from_date,
        )) if from_date > _period_fy_start else {}
    )

    # Keep the legacy name in scope so the rest of the function (totals
    # block, _empty_trial_balance) doesn't have to be renamed.
    period_start = from_date
    as_of = to_date

    active_ids = set(opening_agg) | set(period_agg)
    if not active_ids:
        return _empty_trial_balance(as_of, period_start)

    # CFO directive 2026-05-19: include DEACTIVATED accounts that
    # still carry posted JE lines — otherwise the TB silently drops
    # historical movements and stops balancing. Active status is a
    # bookkeeping aid, not a posting gate.
    accounts = Account.objects.filter(id__in=active_ids).order_by('code')

    rows = []
    for acct in accounts:
        # GL-001: P&L resets at FY start; BS carries forward.
        if acct.account_type in ('revenue', 'expense'):
            o_dr, o_cr = pl_opening_agg.get(acct.id, (ZERO, ZERO))
        else:
            o_dr, o_cr = opening_agg.get(acct.id, (ZERO, ZERO))
        p_dr, p_cr = period_agg.get(acct.id, (ZERO, ZERO))

        opening_bal = _signed_balance(o_dr, o_cr, acct.account_type)
        period_move = _signed_balance(p_dr, p_cr, acct.account_type)
        # CFO directive 2026-06-04 (reverses 2026-05-21) — standard trial
        # balance: closing = opening + period movement. Verified the TB still
        # balances (cumulative Dr = Cr) on ADIC + ADSA. Opening stays its own
        # column. Fixes 'CLOSING 0.00 on no-movement accounts' reading as lost data.
        closing_bal = opening_bal + period_move

        rows.append({
            'code':            acct.code,
            'name':            acct.name,
            'account_type':    acct.account_type,
            'sub_type':        acct.sub_type,
            'opening_balance': _d(opening_bal),
            'period_debits':   _d(p_dr),
            'period_credits':  _d(p_cr),
            'closing_balance': _d(closing_bal),
        })

    # Group by account_type
    grouped = {}
    for row in rows:
        at = row['account_type']
        grouped.setdefault(at, {'accounts': [], 'totals': {}})
        grouped[at]['accounts'].append(row)

    for at, g in grouped.items():
        accts = g['accounts']
        g['totals'] = {
            'opening_balance': _d(sum((Decimal(r['opening_balance']) for r in accts), ZERO)),
            'period_debits':   _d(sum((Decimal(r['period_debits'])   for r in accts), ZERO)),
            'period_credits':  _d(sum((Decimal(r['period_credits'])  for r in accts), ZERO)),
            'closing_balance': _d(sum((Decimal(r['closing_balance']) for r in accts), ZERO)),
        }

    total_dr = sum((Decimal(r['period_debits'])  for r in rows), ZERO)
    total_cr = sum((Decimal(r['period_credits']) for r in rows), ZERO)

    return {
        # CFO directive 2026-05-20: a TB is for a period, not "as at".
        # `from_date` + `to_date` are the canonical fields. `as_of` and
        # `period_start` are retained for backward compatibility with
        # callers that haven't migrated yet (e.g. older frontend pages).
        'from_date':     str(from_date),
        'to_date':       str(to_date),
        'as_of':         str(as_of),          # legacy alias for to_date
        'period_start':  str(period_start),   # legacy alias for from_date
        'currency_code': _company_currency(company_id),
        'accounts':      rows,
        'grouped':       grouped,
        'totals': {
            'total_debits':  _d(total_dr),
            'total_credits': _d(total_cr),
            'balanced':      total_dr == total_cr,
        },
    }


def _empty_trial_balance(as_of, period_start):
    return {
        'from_date':    str(period_start),
        'to_date':      str(as_of),
        'as_of':        str(as_of),
        'period_start': str(period_start),
        'accounts':     [],
        'grouped':      {},
        'totals': {'total_debits': '0.00', 'total_credits': '0.00', 'balanced': True},
    }


# ---------------------------------------------------------------------------
# 2. Profit and Loss
# ---------------------------------------------------------------------------

@_cache_report()
def build_profit_loss(from_date, to_date, company_id=None):
    """
    Income statement for the period [from_date, to_date].
    Optionally scoped to a single company (subsidiary).

    Structure:
      Revenue  (type=revenue)
      Less: Cost of insurance  (type=expense, sub_type=cost_of_insurance)
      = Gross result
      Less: Operating expenses  (type=expense, sub_type=operating_expense)
      = Net profit / (loss)
    """
    from ledger.models import Account

    base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__gte=from_date,
        journal_entry__entry_date__lte=to_date,
    )
    agg = _agg_je_lines(base)

    if not agg:
        return _empty_pl(from_date, to_date)

    # Load all revenue + expense accounts with activity
    active_ids = set(agg)
    # Include deactivated accounts that have posted lines (see TB note).
    accounts   = Account.objects.filter(
        id__in=active_ids,
        account_type__in=('revenue', 'expense'),
    ).order_by('code')

    revenue_rows        = []
    cost_of_ins_rows    = []
    operating_exp_rows  = []
    other_exp_rows      = []

    for acct in accounts:
        dr, cr = agg.get(acct.id, (ZERO, ZERO))
        balance = _signed_balance(dr, cr, acct.account_type)
        row = {
            'code':    acct.code,
            'name':    acct.name,
            'sub_type': acct.sub_type,
            'balance': _d(balance),
        }
        if acct.account_type == 'revenue':
            revenue_rows.append(row)
        elif acct.sub_type == 'cost_of_insurance':
            cost_of_ins_rows.append(row)
        elif acct.sub_type == 'operating_expense':
            operating_exp_rows.append(row)
        else:
            other_exp_rows.append(row)

    total_revenue      = sum((Decimal(r['balance']) for r in revenue_rows),       ZERO)
    total_cost_of_ins  = sum((Decimal(r['balance']) for r in cost_of_ins_rows),  ZERO)
    total_operating    = sum((Decimal(r['balance']) for r in operating_exp_rows),ZERO)
    total_other_exp    = sum((Decimal(r['balance']) for r in other_exp_rows),    ZERO)
    total_expenses     = total_cost_of_ins + total_operating + total_other_exp

    gross_result = total_revenue - total_cost_of_ins
    net_profit   = total_revenue - total_expenses

    result = {
        'from_date':     str(from_date),
        'to_date':       str(to_date),
        'currency_code': _company_currency(company_id),
        'revenue': {
            'accounts': revenue_rows,
            'total':    _d(total_revenue),
        },
        'cost_of_insurance': {
            'accounts': cost_of_ins_rows,
            'total':    _d(total_cost_of_ins),
        },
        'gross_result': _d(gross_result),
        'operating_expenses': {
            'accounts': operating_exp_rows,
            'total':    _d(total_operating),
        },
        'net_profit': _d(net_profit),
        'is_profit':  net_profit >= ZERO,
    }

    if other_exp_rows:
        result['other_expenses'] = {
            'accounts': other_exp_rows,
            'total':    _d(total_other_exp),
        }

    # ARC-2 (CFO/Oprah directive 2026-05-30) — every consumer of
    # build_profit_loss must surface the MA-canonical PAT, not the legacy
    # per-account aggregation. Per-account rows stay for drill-down; the
    # seven anchor totals (revenue / cost_of_ins / gross_result / opex /
    # net_profit) are overwritten from reporting.ma_pl.build_ma_pl so
    # /reports/profit-loss + dashboard tiles + audit pack + xlsx export +
    # morning cron all tie to the MA workbook.
    try:
        from reporting.ma_pl import build_ma_pl
        ma_pl = build_ma_pl(from_date, to_date, company_id=company_id)
        mt = ma_pl.get('totals') or {}
        # _d here returns a 2-dp STRING (its name is misleading) — it expects
        # a Decimal input. MA totals come back as strings; cast to Decimal
        # first.
        def _dec(v): return Decimal(str(v or 0))
        # Preserve the original per-account aggregation in case any consumer
        # needs to reconcile MA vs raw-GL totals.
        result['legacy_totals'] = {
            'revenue':            _d(total_revenue),
            'cost_of_insurance':  _d(total_cost_of_ins),
            'gross_result':       _d(gross_result),
            'operating_expenses': _d(total_operating),
            'other_expenses':     _d(total_other_exp),
            'net_profit':         _d(net_profit),
        }
        # Overwrite the headline numbers with MA-derived values.
        ma_rev   = _dec(mt.get('gross_written_premium'))
        ma_nci   = _dec(mt.get('net_claim_incurred'))
        ma_gp    = _dec(mt.get('gross_profit'))
        ma_opex  = _dec(mt.get('total_operating_expenses'))
        ma_pat   = _dec(mt.get('pat'))
        result['revenue']['total']             = _d(ma_rev)
        # MA stores net_claim_incurred as a negative income (expense).
        # Legacy cost_of_insurance is the positive expense magnitude.
        result['cost_of_insurance']['total']   = _d(-ma_nci if ma_nci < ZERO else ma_nci)
        result['gross_result']                  = _d(ma_gp)
        result['operating_expenses']['total']  = _d(ma_opex)
        # When frozen figures exist for this exact period, use them instead
        # of the GL-derived MA values. The GL import may be incomplete, but
        # the frozen figures are the CFO-authoritative TB numbers.
        frozen = _frozen_overrides(from_date, to_date, company_id)
        if 'PAT' in frozen:
            ma_pat = frozen['PAT']
            result['pat_source'] = 'frozen_figure'
        if 'GWP' in frozen:
            ma_rev = frozen['GWP']
            result['revenue']['total'] = _d(ma_rev)
            result['gwp_source'] = 'frozen_figure'
        result['net_profit']                   = _d(ma_pat)
        result['is_profit']                    = ma_pat >= ZERO
        result['ma_totals']                    = mt
        result['source']                       = 'ma_canonical'
    except Exception:    # noqa: BLE001 — never let MA-shim break the legacy shape
        result['source'] = 'legacy_fallback'

    return result


def _empty_pl(from_date, to_date):
    return {
        'from_date': str(from_date),
        'to_date':   str(to_date),
        'revenue':          {'accounts': [], 'total': '0.00'},
        'cost_of_insurance':{'accounts': [], 'total': '0.00'},
        'gross_result':     '0.00',
        'operating_expenses':{'accounts': [], 'total': '0.00'},
        'net_profit':       '0.00',
        'is_profit':        True,
    }


# ---------------------------------------------------------------------------
# 3. Balance Sheet
# ---------------------------------------------------------------------------

@_cache_report()
def build_balance_sheet(as_of, company_id=None):
    """
    Balance sheet as of *as_of*. Optionally scoped to a single company.

    Current-year P&L (net of revenue/expense since fiscal-year start) is
    included in the equity section so that assets = liabilities + equity.

    Entity filter (CFO audit 2026-05-17):
    Every aggregation in this function MUST honour the company_id filter.
    Asset totals previously came out ~18M too high for ADIC because the
    `agg` dictionary, built from `_posted_lines(company_id=...)`, was
    correct but downstream code paths must also be company-scoped.
    Explicit defensive checks added below.
    """
    from ledger.models import Account, JournalEntry

    # CFO directive 2026-05-20: use the company's fy_end_month, not hardcoded July.
    fy_start = _get_fiscal_year_start(as_of, _fy_end_month_for(company_id))

    # CFO audit 2026-05-17: explicit assertion that the company filter is
    # in effect. If `company_id` is supplied, the queryset MUST filter on
    # journal_entry__company_id; if not, this is a global rollup and the
    # caller has accepted that. No silent leak.
    base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__lte=as_of,
    )
    if company_id:
        base = base.filter(journal_entry__company_id=company_id)  # belt-and-braces
    agg = _agg_je_lines(base)

    pl_base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__gte=fy_start,
        journal_entry__entry_date__lte=as_of,
    )
    if company_id:
        pl_base = pl_base.filter(journal_entry__company_id=company_id)
    pl_agg = _agg_je_lines(pl_base)

    active_ids = set(agg) | set(pl_agg)
    if not active_ids:
        return _empty_balance_sheet(as_of)

    # Include deactivated accounts with posted lines (see TB note).
    accounts = Account.objects.filter(
        id__in=active_ids
    ).select_related('parent').order_by('code')

    def _section(acct_type, sub_types=None):
        result = []
        for acct in accounts:
            if acct.account_type != acct_type:
                continue
            if sub_types and acct.sub_type not in sub_types:
                continue
            dr, cr = agg.get(acct.id, (ZERO, ZERO))
            balance = _signed_balance(dr, cr, acct_type)
            if balance == ZERO:
                continue
            result.append({
                'code':    acct.code,
                'name':    acct.name,
                'sub_type': acct.sub_type,
                'balance': _d(balance),
            })
        return result

    # ---- Assets ----
    # Bucketing aligned with the CFO's MA workbook (audited 2026-05-18 via
    # `manage.py audit_categorisation --company ADIC --as-of 2026-03-31`):
    #   • Current Assets = bank + current_asset + 'other_asset' rows that
    #     the MA workbook places inside Current Assets (Related Party
    #     Receivables 201xxx, Deferred Tax Asset 270xxx). These were
    #     previously bucketed as Other Assets which mismatches the MA
    #     layout — the totals add the same but the per-section subtotals
    #     drifted from the workbook.
    #   • Fixed Assets = fixed_asset + accumulated_depreciation
    #     (contra-asset). MA workbook shows Fixed Assets NET of depr; if
    #     accum-depr lived under "Other Assets" the Fixed-Asset card
    #     overstated gross PPE by the accumulated-depreciation amount.
    #   • Other Assets = whatever's left (long-term receivables, etc.).
    current_assets = _section(
        'asset',
        {'bank', 'current_asset',
         # MA puts Related Party Recv (201xxx) and DTA (270xxx) in
         # Current Assets; omni's PREFIX_TYPE_MAP tags them other_asset.
         # Pull them into current via the supplementary 'mid_term_asset'
         # sub_type label that the audit identifies — if no such sub_type
         # is used yet, this is a noop. The actual reclassification is
         # done by code prefix below for present accounts.
         'mid_term_asset'},
    )
    fixed_assets   = _section('asset', {'fixed_asset', 'accumulated_depreciation'})
    other_assets   = _section('asset', None)

    # BUG-021 (Oprah QA, 2026-06-05): the dashboard "Fixed Assets" card reads
    # assets.fixed_assets.total, but the CoA and the MA Balance Sheet group
    # Non-Current Assets by `Account.fs_line_item` (reporting/ma_bs_spec.py
    # SECTIONS) — the canonical SSOT — not by sub_type. Odoo-migrated PPE,
    # Accumulated Depreciation and Right-of-Use accounts all carry
    # sub_type='other_asset' (see import_adsa_pack PREFIX map) and there is no
    # 'accumulated_depreciation' sub_type at all, so the sub_type-only bucket
    # above left Fixed Assets at 0.00 (ADIC) while the ~6.05M sat in Other
    # Assets — diverging from the CoA's 6.1M Non-Current Assets.
    #
    # Reclassify by fs_line_item so the card agrees with the CoA / MA BS: any
    # asset row the MA workbook places in Non-Current Assets lands in Fixed
    # Assets regardless of its sub_type. Pure rebucketing — total_assets is
    # unchanged; rows only move between the Fixed and Other buckets.
    from reporting.ma_bs_spec import SECTIONS as _MA_BS_SECTIONS
    _nca_lines = {
        (ln or '').strip().lower()
        for sec in _MA_BS_SECTIONS if sec['id'] == 'non_current_assets'
        for ln in sec['lines']
    }
    _nca_codes = {
        a.code for a in accounts
        if a.account_type == 'asset'
        and (a.fs_line_item or '').strip().lower() in _nca_lines
    }
    if _nca_codes:
        _fa_codes_seen = {r['code'] for r in fixed_assets}
        for r in other_assets:
            if r['code'] in _nca_codes and r['code'] not in _fa_codes_seen:
                fixed_assets.append(r)
                _fa_codes_seen.add(r['code'])
        # Defensive: a Non-Current row mis-bucketed into current by sub_type.
        current_assets = [r for r in current_assets if r['code'] not in _nca_codes]

    # Per-code MA reclassification overrides — codes starting with these
    # prefixes are pulled OUT of `other_assets` and INTO `current_assets`
    # to mirror the workbook layout. Single source of truth — adjust this
    # set when the CFO's MA layout evolves.
    MA_CURRENT_ASSET_OVERRIDE_PREFIXES = ('201', '270')
    _moved = []
    _kept_other = []
    for row in other_assets:
        if any(row['code'].startswith(p) for p in MA_CURRENT_ASSET_OVERRIDE_PREFIXES):
            _moved.append(row)
        else:
            _kept_other.append(row)
    if _moved:
        current_assets = current_assets + _moved
        other_assets = _kept_other

    # remove current and fixed from other_assets
    ca_codes = {r['code'] for r in current_assets}
    fa_codes = {r['code'] for r in fixed_assets}
    other_assets = [r for r in other_assets
                    if r['code'] not in ca_codes | fa_codes]

    total_current_assets = sum((Decimal(r['balance']) for r in current_assets), ZERO)
    total_fixed_assets   = sum((Decimal(r['balance']) for r in fixed_assets),   ZERO)
    total_other_assets   = sum((Decimal(r['balance']) for r in other_assets),   ZERO)
    total_assets         = total_current_assets + total_fixed_assets + total_other_assets

    # ---- Liabilities ----
    current_liabs = _section('liability', {'current_liability'})
    provisions    = _section('liability', {'provision'})
    other_liabs   = _section('liability', None)
    cl_codes = {r['code'] for r in current_liabs}
    pv_codes = {r['code'] for r in provisions}
    other_liabs = [r for r in other_liabs
                   if r['code'] not in cl_codes | pv_codes]

    total_current_liabs = sum((Decimal(r['balance']) for r in current_liabs), ZERO)
    total_provisions    = sum((Decimal(r['balance']) for r in provisions),    ZERO)
    total_other_liabs   = sum((Decimal(r['balance']) for r in other_liabs),   ZERO)
    total_liabilities   = total_current_liabs + total_provisions + total_other_liabs

    # ---- Permanent equity (3xxx accounts) ----
    # Bug fix 2026-05-19 (Charmaine #3): the Balance Sheet was leaving out
    # account_type='equity_unaffected' rows, which is where ADSA's 999999
    # Undistributed Profits/Losses sits with its prior-year accumulated
    # loss (Dr 1,322,026.11 opening on ADSA at 30-Jun-2025). Without it
    # total equity was overstated by exactly that amount.
    equity_rows = (
        _section('equity', None)
        + _section('equity_unaffected', None)
    )
    total_perm_equity = sum((Decimal(r['balance']) for r in equity_rows), ZERO)

    # ---- Current year P&L (calculated from P&L accounts, not closed) ----
    # Include deactivated accounts with posted lines (see TB note).
    pl_accounts = Account.objects.filter(
        id__in=set(pl_agg),
        account_type__in=('revenue', 'expense'),
    )
    current_year_pl = ZERO
    for acct in pl_accounts:
        dr, cr = pl_agg.get(acct.id, (ZERO, ZERO))
        balance = _signed_balance(dr, cr, acct.account_type)
        # Revenue increases profit; expenses reduce it
        if acct.account_type == 'revenue':
            current_year_pl += balance
        else:
            current_year_pl -= balance

    # ---- Prior-year accumulated P&L (revenue/expense balances < fy_start) ----
    # close_period currently only flips a status flag; no year-end closing JE
    # rolls revenue/expense to retained earnings. Without this line, prior-year
    # P&L sits unrolled in 4xxx/5xxx/6xxx accounts and the BS won't balance.
    prior_pl_base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__lt=fy_start,
    )
    prior_pl_agg = _agg_je_lines(prior_pl_base)
    # Include deactivated accounts with prior-year posted lines.
    prior_pl_accounts = Account.objects.filter(
        id__in=set(prior_pl_agg),
        account_type__in=('revenue', 'expense'),
    )
    prior_year_pl = ZERO
    for acct in prior_pl_accounts:
        dr, cr = prior_pl_agg.get(acct.id, (ZERO, ZERO))
        balance = _signed_balance(dr, cr, acct.account_type)
        if acct.account_type == 'revenue':
            prior_year_pl += balance
        else:
            prior_year_pl -= balance

    total_equity = total_perm_equity + prior_year_pl + current_year_pl

    # ---- Balance check ----
    liabilities_and_equity = total_liabilities + total_equity
    balanced = abs(total_assets - liabilities_and_equity) < Decimal('0.01')

    return {
        'as_of':             str(as_of),
        'fiscal_year_start': str(fy_start),
        'currency_code':     _company_currency(company_id),
        'assets': {
            'current_assets': {
                'accounts': current_assets,
                'total':    _d(total_current_assets),
            },
            'fixed_assets': {
                'accounts': fixed_assets,
                'total':    _d(total_fixed_assets),
            },
            **(
                {'other_assets': {'accounts': other_assets, 'total': _d(total_other_assets)}}
                if other_assets else {}
            ),
            'total': _d(total_assets),
        },
        'liabilities': {
            'current_liabilities': {
                'accounts': current_liabs,
                'total':    _d(total_current_liabs),
            },
            **(
                {'provisions': {'accounts': provisions, 'total': _d(total_provisions)}}
                if provisions else {}
            ),
            **(
                {'other_liabilities': {'accounts': other_liabs, 'total': _d(total_other_liabs)}}
                if other_liabs else {}
            ),
            'total': _d(total_liabilities),
        },
        'equity': {
            'accounts':        equity_rows,
            'permanent_total': _d(total_perm_equity),
            'prior_year_pl':   _d(prior_year_pl),
            'current_year_pl': _d(current_year_pl),
            'pl_label':        'Net profit' if current_year_pl >= ZERO else 'Net loss',
            'total':           _d(total_equity),
        },
        'totals': {
            'total_assets':              _d(total_assets),
            'total_liabilities':         _d(total_liabilities),
            'total_equity':              _d(total_equity),
            'liabilities_and_equity':    _d(liabilities_and_equity),
            'balanced':                  balanced,
        },
    }


def _empty_balance_sheet(as_of, company_id=None):
    fy_start = _get_fiscal_year_start(as_of, _fy_end_month_for(company_id))
    zero = '0.00'
    return {
        'as_of': str(as_of),
        'fiscal_year_start': str(fy_start),
        'assets':      {'current_assets': {'accounts': [], 'total': zero},
                        'fixed_assets':   {'accounts': [], 'total': zero},
                        'total': zero},
        'liabilities': {'current_liabilities': {'accounts': [], 'total': zero},
                        'total': zero},
        'equity':      {'accounts': [], 'permanent_total': zero,
                        'prior_year_pl': zero, 'current_year_pl': zero,
                        'pl_label': 'Net profit', 'total': zero},
        'totals':      {'total_assets': zero, 'total_liabilities': zero,
                        'total_equity': zero, 'liabilities_and_equity': zero,
                        'balanced': True},
    }


# ---------------------------------------------------------------------------
# 4 & 5. AR / AP Aging
# ---------------------------------------------------------------------------

_AGING_BUCKETS = [
    ('current',    0,   30),
    ('days_31_60', 31,  60),
    ('days_61_90', 61,  90),
    ('days_91_120',91, 120),
    ('over_120',  121, None),
]


def _aging_bucket(days_past_due):
    for label, lo, hi in _AGING_BUCKETS:
        if hi is None:
            if days_past_due >= lo:
                return label
        elif lo <= days_past_due <= hi:
            return label
    return 'current'


def _build_aging(invoice_type, contact_type_filter, as_of, company_id=None):
    """Shared logic for AR and AP aging reports."""
    from billing.models import Invoice

    open_statuses = [
        Invoice.Status.POSTED,
        Invoice.Status.PARTIALLY_PAID,
        Invoice.Status.OVERDUE,
    ]

    invoices = (
        Invoice.objects
        .filter(
            invoice_type=invoice_type,
            status__in=open_statuses,
            issue_date__lte=as_of,
        )
        .select_related('contact', 'currency_code')
        .order_by('contact__name', 'due_date', 'invoice_number')
    )

    if contact_type_filter:
        invoices = invoices.filter(contact__contact_type__in=contact_type_filter)
    if company_id:
        invoices = invoices.filter(company_id=company_id)

    # Initialise grand total buckets
    grand = {label: ZERO for label, *_ in _AGING_BUCKETS}
    grand['total_outstanding'] = ZERO
    grand['total_invoiced']    = ZERO
    grand['total_paid']        = ZERO

    # Group by contact
    from collections import defaultdict
    by_contact = defaultdict(list)
    for inv in invoices:
        by_contact[inv.contact].append(inv)

    customers = []
    for contact, inv_list in sorted(by_contact.items(), key=lambda x: x[0].name):
        cust_totals = {label: ZERO for label, *_ in _AGING_BUCKETS}
        cust_totals['total_outstanding'] = ZERO

        inv_rows = []
        for inv in inv_list:
            balance  = inv.balance_due
            due_date = inv.due_date or inv.issue_date
            age_days = (as_of - due_date).days
            bucket   = _aging_bucket(age_days)

            inv_rows.append({
                'invoice_number': inv.invoice_number,
                'issue_date':     str(inv.issue_date),
                'due_date':       str(due_date),
                'currency':       inv.currency_code_id,
                'total_amount':   _d(inv.total_amount),
                'amount_paid':    _d(inv.amount_paid),
                'balance_due':    _d(balance),
                'days_past_due':  age_days,
                'age_bucket':     bucket,
            })

            cust_totals[bucket]          += balance
            cust_totals['total_outstanding'] += balance

        # Roll up into grand totals
        for k in grand:
            grand[k] += cust_totals.get(k, ZERO)
        grand['total_invoiced'] += sum(inv.total_amount for inv in inv_list)
        grand['total_paid']     += sum(inv.amount_paid  for inv in inv_list)

        customers.append({
            'contact_name': contact.name,
            'contact_type': contact.contact_type,
            'invoices':     inv_rows,
            'totals': {k: _d(v) for k, v in cust_totals.items()},
        })

    return {
        'as_of':         str(as_of),
        'currency_code': _company_currency(company_id),
        'customers':     customers,
        'totals':        {k: _d(v) for k, v in grand.items()},
        'buckets':       [label for label, *_ in _AGING_BUCKETS],
    }


@_cache_report()
def build_ar_aging(as_of, company_id=None):
    """Accounts-receivable aging for all unpaid customer invoices."""
    return _build_aging('customer_invoice', ['customer'], as_of, company_id=company_id)


# AP control accounts (credit-normal liabilities). _get_ap_account in
# billing/models.py routes broker→2130, reinsurer→2120, default vendor→2140.
_AP_ACCOUNT_CODES = ['2120', '2130', '2140']


def _ap_gl_balance(as_of, company_id=None):
    """Balance-sheet Accounts-Payable balance (BWP) from the GL as of a date.

    Sum of credit-normal balances on the AP control accounts. Used by
    PAY-002 to reconcile the AP aging schedule to the balance sheet.
    """
    from ledger.models import Account, JournalEntry, JournalEntryLine

    lines = JournalEntryLine.objects.filter(
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__lte=as_of,
        account__code__in=_AP_ACCOUNT_CODES,
    )
    if company_id:
        lines = lines.filter(journal_entry__company_id=company_id)
    agg = lines.aggregate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
    cr = agg['cr'] or ZERO
    dr = agg['dr'] or ZERO
    return cr - dr   # liability: credit-normal


@_cache_report()
def build_ap_aging(as_of, company_id=None):
    """Accounts-payable aging for all unpaid vendor bills.

    PAY-002 (CFO directive 2026-05-27): includes a `reconciliation` block
    proving the aging schedule ties to the Balance-Sheet AP control
    balance. Variance must be zero; the frontend flags any non-zero in red.
    """
    rep = _build_aging('vendor_bill', ['vendor', 'broker', 'reinsurer'],
                       as_of, company_id=company_id)
    # _build_aging already stringified totals via _d(); parse back to Decimal
    # for the reconciliation math.
    aging_dec = Decimal(str(rep['totals'].get('total_outstanding', '0.00')))
    bs_ap_dec = _ap_gl_balance(as_of, company_id=company_id)   # Decimal
    variance_dec = aging_dec - bs_ap_dec
    rep['reconciliation'] = {
        'aging_total':    _d(aging_dec),
        'bs_ap_balance':  _d(bs_ap_dec),
        'variance':       _d(variance_dec),
        'reconciled':     variance_dec == ZERO,
        'ap_accounts':    _AP_ACCOUNT_CODES,
    }
    return rep


# ---------------------------------------------------------------------------
# 6. Cash Position
# ---------------------------------------------------------------------------

@_cache_report()
def build_cash_position(company_id=None, as_of=None):
    """
    GL balance for every bank account, with native-currency and BWP amounts.
    Optionally scoped to a single company (only journal entries tagged with
    that company are aggregated).

    `as_of` (date) — if supplied, only journal entries dated on or before
    this date are aggregated. This makes the cash tile period-aware:
    selecting FY2025 on the dashboard yields the 2025-06-30 closing
    balance (e.g. 12.88M for ADIC), not the cumulative all-time position.
    Defaults to today() for backwards compatibility with callers that
    don't pass it.
    """
    from ledger.models import Account, JournalEntry, JournalEntryLine
    from core.models import ExchangeRate

    today = timezone.localdate()
    cutoff = as_of or today

    bank_accounts = (
        Account.objects
        .filter(is_bank_account=True, is_active=True)
        .select_related('currency_code')
        .order_by('code')
    )
    # Bug fix 2026-05-19 (Charmaine #4): the "Across N bank accounts"
    # subtitle on the dashboard counts every bank GL across the group
    # even when ADSA / UNI / etc. is selected. Scope by owner_company so
    # the count matches the entity's actual GL bank accounts.
    if company_id:
        bank_accounts = bank_accounts.filter(owner_company_id=company_id)

    # Latest exchange rates: { from_code -> rate }
    # Backend-agnostic: order by (currency, -date) and keep the first row per
    # currency in Python. Postgres supports DISTINCT ON but SQLite does not.
    latest_rates = {}
    for er in (
        ExchangeRate.objects
        .filter(to_currency_id='BWP', effective_date__lte=today)
        .order_by('from_currency_id', '-effective_date')
        .select_related('from_currency')
    ):
        if er.from_currency_id not in latest_rates:
            latest_rates[er.from_currency_id] = er.rate

    posted_lines = JournalEntryLine.objects.filter(
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__lte=cutoff,
    )
    if company_id:
        posted_lines = posted_lines.filter(journal_entry__company_id=company_id)

    acct_lines = {}
    for row in (
        posted_lines
        .filter(account__in=bank_accounts)
        .values('account_id')
        .annotate(
            total_dr_bwp=Sum('debit_bwp'),
            total_cr_bwp=Sum('credit_bwp'),
            total_dr_native=Sum('debit_amount'),
            total_cr_native=Sum('credit_amount'),
        )
    ):
        acct_lines[row['account_id']] = row

    rows = []
    total_bwp = ZERO

    for acct in bank_accounts:
        data      = acct_lines.get(acct.id, {})
        dr_bwp    = data.get('total_dr_bwp')    or ZERO
        cr_bwp    = data.get('total_cr_bwp')    or ZERO
        dr_native = data.get('total_dr_native') or ZERO
        cr_native = data.get('total_cr_native') or ZERO

        bal_bwp    = dr_bwp - cr_bwp       # asset: Dr-normal
        bal_native = dr_native - cr_native

        currency = acct.currency_code_id

        # Latest exchange rate for display. A missing rate must be VISIBLE,
        # never silently 1.0 (bug ad93693b: FX bank accounts shown unconverted).
        rate_missing = False
        if currency == 'BWP':
            exch_rate = Decimal('1.00000000')
        else:
            exch_rate = latest_rates.get(currency)
            if exch_rate is None:
                rate_missing = True
                exch_rate = Decimal('1.00000000')
                logger.warning(
                    'cash-position: no %s->BWP exchange rate; account %s '
                    'displayed UNCONVERTED (rate 1.0)', currency, acct.code)

        total_bwp += bal_bwp

        rows.append({
            'account_code':  acct.code,
            'account_name':  acct.name,
            'currency':      currency,
            'balance_native': _d(bal_native),
            'balance_bwp':   _d(bal_bwp),
            'exchange_rate': str(exch_rate),
            'rate_missing':  rate_missing,
            'has_activity':  bool(data),
        })

    return {
        'as_of':         str(today),
        'currency_code': _company_currency(company_id),
        'accounts':      rows,
        'total_bwp':     _d(total_bwp),
    }


# ---------------------------------------------------------------------------
# 6b. Receivables Summary  — single source of truth for "Total Receivables"
# ---------------------------------------------------------------------------
#
# CFO directive 2026-05-24: dashboard tile was reading
# `current_assets.total − cash_position.total_bwp`, which clamped to 0
# because (a) bank accounts sit in `other_assets` per the legacy BS
# split, and (b) actual AR is scattered across both sections.
#
# The new contract: anything flagged `Account.is_receivable=True` (or
# matching the canonical AR code prefixes for accounts the flag hasn't
# reached yet) is a receivable. The dashboard tile, the BS "Receivables"
# line, AR aging, and any other report must all read this function so
# the numbers reconcile — "all tables should talk the same".
#
# Buckets (for the CFO dashboard breakdown):
#   related_party_AR    201xxx
#   trade_other_AR      202xxx, 203xxx, 260xxx
#   subrogation_AR      240xxx
#   reinsurance_AR      208xxx, 212xxx
#
# Returns BWP totals + per-bucket breakdown + per-account rows.
RECEIVABLE_PREFIXES_FALLBACK = ('201', '202', '203', '208', '212', '240', '260')


def _classify_receivable_bucket(code: str) -> str:
    if code.startswith('201'):
        return 'related_party'
    if code.startswith(('202', '203', '260')):
        return 'trade_other'
    if code.startswith('240'):
        return 'subrogation'
    if code.startswith(('208', '212')):
        return 'reinsurance'
    return 'other'


@_cache_report()
def build_receivables_summary(company_id=None, as_of=None):
    """
    Total receivables across every receivables GL, net of ECL provisions.

    Output:
        {
          'as_of':       'YYYY-MM-DD',
          'company':     '<uuid or null>',
          'total_bwp':   '23098788.23',
          'buckets':     {'related_party': '6251476.19', ...},
          'accounts':    [{'code', 'name', 'balance_bwp', 'bucket'}, ...]
        }
    """
    from ledger.models import Account, JournalEntry, JournalEntryLine

    cutoff = as_of or timezone.localdate()

    # Prefer the explicit flag; fall back to prefixes so the endpoint is
    # useful even on entities where the flag hasn't been backfilled yet.
    ar_accounts = Account.objects.filter(
        Q(is_receivable=True)
        | Q(code__startswith='201')
        | Q(code__startswith='202')
        | Q(code__startswith='203')
        | Q(code__startswith='208')
        | Q(code__startswith='212')
        | Q(code__startswith='240')
        | Q(code__startswith='260'),
        is_active=True,
        account_type='asset',
    ).order_by('code')
    if company_id:
        ar_accounts = ar_accounts.filter(owner_company_id=company_id)

    posted_lines = JournalEntryLine.objects.filter(
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__lte=cutoff,
    )
    if company_id:
        posted_lines = posted_lines.filter(journal_entry__company_id=company_id)

    by_account = {}
    for row in (
        posted_lines
        .filter(account__in=ar_accounts)
        .values('account_id')
        .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
    ):
        by_account[row['account_id']] = (row['dr'] or ZERO) - (row['cr'] or ZERO)

    rows = []
    buckets = {'related_party': ZERO, 'trade_other': ZERO,
               'subrogation': ZERO, 'reinsurance': ZERO, 'other': ZERO}
    total = ZERO
    for acc in ar_accounts:
        bal = by_account.get(acc.id, ZERO)
        if bal == ZERO:
            continue
        bucket = _classify_receivable_bucket(acc.code)
        buckets[bucket] += bal
        total += bal
        rows.append({
            'code':         acc.code,
            'name':         acc.name,
            'bucket':       bucket,
            'balance_bwp':  _d(bal),
        })

    return {
        'as_of':       str(cutoff),
        'company':     str(company_id) if company_id else None,
        'total_bwp':   _d(total),
        'buckets':     {k: _d(v) for k, v in buckets.items()},
        'accounts':    rows,
    }


# ---------------------------------------------------------------------------
# 6c. Chart-of-Accounts MA-tree  — CFO directive 2026-05-24
# ---------------------------------------------------------------------------
#
# The CoA page becomes the single source of truth viewer. Tree mirrors
# the MA workbook exactly: top level = MA section (Current Assets,
# Non-Current Assets, Current Liabilities, Non-current Liabilities,
# Equity, P&L sections), middle level = MA line (fs_line_item label or
# MA P&L MA_LINES key), leaf = the GL accounts with running balance.
#
# Every dashboard tile + report builder will eventually call this so
# all the tables show the same numbers in the same buckets ("all the
# tables should talk the same").


def _ma_pl_section_groups():
    """Display sections for the CoA P&L tree.

    Source of truth = `ma_pl_spec.SECTIONS` (Net Earned Premium, Claims,
    Acquisition, Other Income, Expenses, Provisions). The spec stops at
    Provisions; we append Depreciation / Finance Cost / Taxation as
    additional sections so the tree walks all the way down to PAT.
    Keep the section order matching the MA workbook P&L exactly.
    """
    from .ma_pl_spec import SECTIONS as PL_SECTIONS
    groups = [(s['id'], s['label'], list(s['lines'])) for s in PL_SECTIONS]
    groups += [
        ('depreciation', 'Depreciation', ['depreciation']),
        ('finance_cost', 'Finance Cost', ['finance_cost']),
        ('taxation',     'Taxation',     ['taxation']),
    ]
    return groups


def build_coa_ma_tree(company_id=None, as_of=None, from_date=None):
    """Single tree for the CoA UI — MA layout with balances rolled up.

    Two distinct date semantics:
      * BS side  — cumulative balance at `as_of` (point-in-time)
      * P&L side — period activity in [from_date, as_of] (range)

    `as_of` defaults to today. `from_date` defaults to the start of
    the fiscal year that contains as_of (ADIC FY = 1 Jul → 30 Jun).
    Without this split the P&L summed every JE since system go-live
    and the user saw GWP ~2.3× the MA workbook FY26-9M figure.
    """
    from ledger.models import Account, JournalEntry, JournalEntryLine
    from .ma_bs_spec import SECTIONS as BS_SECTIONS
    from .ma_pl_spec import MA_LINES

    cutoff = as_of or timezone.localdate()
    if from_date is None:
        # ADIC FY starts 1 July. cutoff.month >= 7 → FY started this cal year,
        # else last cal year.
        fy_year = cutoff.year if cutoff.month >= 7 else cutoff.year - 1
        from_date = date(fy_year, 7, 1)

    accounts_qs = Account.objects.filter(is_active=True)
    if company_id:
        accounts_qs = accounts_qs.filter(owner_company_id=company_id)
    accounts = {a.id: a for a in accounts_qs}

    # BS balances — cumulative through `cutoff`
    bs_lines_qs = JournalEntryLine.objects.filter(
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__lte=cutoff,
    )
    if company_id:
        bs_lines_qs = bs_lines_qs.filter(journal_entry__company_id=company_id)

    # P&L activity — only entries inside [from_date, cutoff]
    pl_lines_qs = JournalEntryLine.objects.filter(
        journal_entry__status=JournalEntry.Status.POSTED,
        journal_entry__entry_date__gte=from_date,
        journal_entry__entry_date__lte=cutoff,
    )
    if company_id:
        pl_lines_qs = pl_lines_qs.filter(journal_entry__company_id=company_id)

    bs_balances = {}
    for row in (
        bs_lines_qs
        .filter(account_id__in=list(accounts.keys()))
        .values('account_id')
        .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
    ):
        bs_balances[row['account_id']] = (row['dr'] or ZERO) - (row['cr'] or ZERO)

    pl_balances = {}
    for row in (
        pl_lines_qs
        .filter(account_id__in=list(accounts.keys()))
        .values('account_id')
        .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
    ):
        pl_balances[row['account_id']] = (row['dr'] or ZERO) - (row['cr'] or ZERO)

    used_ids: set = set()

    # ── BS tree ───────────────────────────────────────────────────
    bs_out = []
    for sec in BS_SECTIONS:
        section_subtotal = ZERO
        lines = []
        for label in sec['lines']:
            line_subtotal = ZERO
            line_accounts = []
            for acc_id, acc in accounts.items():
                if (acc.fs_line_item or '').strip() == label:
                    bal = bs_balances.get(acc_id, ZERO)
                    used_ids.add(acc_id)
                    # Liabilities + equity stored credit-natural; flip
                    # display sign so positive numbers read naturally.
                    if sec['side'] in ('liability', 'equity'):
                        display_bal = -bal
                    else:
                        display_bal = bal
                    line_accounts.append({
                        'code':        acc.code,
                        'name':        acc.name,
                        'sub_type':    acc.sub_type or '',
                        'balance_bwp': _d(display_bal),
                    })
                    line_subtotal += display_bal
            line_accounts.sort(key=lambda r: r['code'])
            lines.append({
                'label':        label,
                'subtotal_bwp': _d(line_subtotal),
                'accounts':     line_accounts,
            })
            section_subtotal += line_subtotal
        bs_out.append({
            'id':             sec['id'],
            'label':          sec['label'],
            'side':           sec['side'],
            'subtotal_label': sec['subtotal_label'],
            'subtotal_bwp':   _d(section_subtotal),
            'lines':          lines,
        })

    # ── P&L tree ──────────────────────────────────────────────────
    # CFO directive 2026-05-24: the team's TB upload sets
    # `Account.fs_line_item` to the MA label (e.g. "Commissions Paid").
    # Match on fs_line_item first (canonical SSOT), code-list second
    # (fallback for unmapped accounts).
    #
    # Sign convention mirrors `reporting.ma_pl.build_ma_pl` exactly:
    #   * line.amount = positive on its natural side (income shows
    #     positive; expense shows positive)
    #   * section_subtotal = sum(line.amount if line is income else
    #     -line.amount). NEP = +GWP − Premiums Ceded + Change in UPR.
    # Without the sign-aware subtotal NEP was inflated ~4× because
    # Premiums Ceded was *added* instead of subtracted.
    pl_out = []
    for sec_id, sec_label, line_keys in _ma_pl_section_groups():
        section_subtotal = ZERO
        lines = []
        for key in line_keys:
            line = MA_LINES.get(key)
            if not line:
                continue
            label = line.get('label', '')
            sign  = line.get('sign', 'income')
            codes = set(line.get('codes', []))
            line_subtotal = ZERO
            line_accounts = []
            for acc_id, acc in accounts.items():
                fs = (acc.fs_line_item or '').strip()
                if fs:
                    matches = (fs == label)
                else:
                    matches = (
                        acc.code in codes
                        or any(acc.code.endswith('_' + c) for c in codes)
                    )
                if matches:
                    bal = pl_balances.get(acc_id, ZERO)
                    used_ids.add(acc_id)
                    # natural-side display: income line → flip sign
                    # (revenue accounts are credit-natural so bal is
                    # negative); expense line → keep sign.
                    display_bal = -bal if sign == 'income' else bal
                    line_accounts.append({
                        'code':        acc.code,
                        'name':        acc.name,
                        'sub_type':    acc.sub_type or '',
                        'balance_bwp': _d(display_bal),
                    })
                    line_subtotal += display_bal
            line_accounts.sort(key=lambda r: r['code'])
            lines.append({
                'label':        label,
                'sign':         sign,
                'subtotal_bwp': _d(line_subtotal),
                'accounts':     line_accounts,
            })
            # Income contributes +, expense contributes − to the
            # section subtotal — matches build_ma_pl exactly.
            section_subtotal += line_subtotal if sign == 'income' else -line_subtotal
        pl_out.append({
            'id':             sec_id,
            'label':          sec_label,
            'side':           'pl',
            'subtotal_label': 'Total ' + sec_label,
            'subtotal_bwp':   _d(section_subtotal),
            'lines':          lines,
        })

    # ── Unmapped tail ─────────────────────────────────────────────
    # Use cumulative bs_balances for the "has activity" signal — even
    # P&L accounts that posted before from_date deserve to be listed
    # so Finance can map them.
    unmapped = []
    for acc_id, acc in accounts.items():
        if acc_id in used_ids:
            continue
        bal = bs_balances.get(acc_id, ZERO)
        if bal == ZERO and not (acc.fs_line_item or '').strip():
            continue
        unmapped.append({
            'code':         acc.code,
            'name':         acc.name,
            'sub_type':     acc.sub_type or '',
            'account_type': acc.account_type,
            'fs_line_item': acc.fs_line_item or '',
            'balance_bwp':  _d(bal),
        })
    unmapped.sort(key=lambda r: r['code'])

    return {
        'as_of':     str(cutoff),
        'from_date': str(from_date),
        'company':   str(company_id) if company_id else None,
        'bs':        bs_out,
        'pl':        pl_out,
        'unmapped':  unmapped,
    }


# ---------------------------------------------------------------------------
# 7. General Ledger Detail
# ---------------------------------------------------------------------------

def build_general_ledger(account_code, from_date, to_date, company_id=None):
    """
    All posted JE lines for *account_code* in [from_date, to_date],
    with a running balance starting from the opening balance.
    Optionally scoped to a single company.
    """
    from ledger.models import Account, JournalEntry, JournalEntryLine

    # CFO directive 2026-05-20: frontend's account selector emits the
    # Account.id (UUID); legacy callers still pass the human code
    # ('100003'). Accept either — UUID lookup first, fall back to code.
    account = None
    if isinstance(account_code, str) and len(account_code) == 36 and account_code.count('-') == 4:
        account = Account.objects.filter(pk=account_code).first()
    if account is None:
        account = Account.objects.filter(code=account_code).first()
    if account is None:
        return {'error': f"Account '{account_code}' not found."}

    # Opening balance: all posted entries strictly before from_date.
    # GL-001 fix (Oprah QA, 2026-06-08): for P&L accounts (revenue/expense) the
    # opening balance must RESET at each fiscal-year start — count only lines
    # from the fiscal-year start that contains from_date up to from_date. BS
    # accounts carry forward all prior activity unchanged.
    _ob_lower = None
    if account.account_type in ('revenue', 'expense'):
        _ob_lower = _get_fiscal_year_start(from_date, _fy_end_month_for(company_id))
    _ob_qs = _posted_lines(company_id=company_id).filter(
        account=account, journal_entry__entry_date__lt=from_date)
    if _ob_lower is not None:
        _ob_qs = _ob_qs.filter(journal_entry__entry_date__gte=_ob_lower)
    opening_agg = _ob_qs.aggregate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp'))
    o_dr = opening_agg['dr'] or ZERO
    o_cr = opening_agg['cr'] or ZERO
    opening_balance = _signed_balance(o_dr, o_cr, account.account_type)

    # Lines in range, ordered by date then JE creation order
    lines_qs = (
        _posted_lines(company_id=company_id)
        .filter(
            account=account,
            journal_entry__entry_date__gte=from_date,
            journal_entry__entry_date__lte=to_date,
        )
        .select_related('journal_entry', 'contact')
        .order_by('journal_entry__entry_date', 'journal_entry__created_at')
    )

    running = opening_balance
    lines   = []
    total_dr = ZERO
    total_cr = ZERO

    for ln in lines_qs:
        je   = ln.journal_entry
        dr   = ln.debit_bwp  or ZERO
        cr   = ln.credit_bwp or ZERO
        total_dr += dr
        total_cr += cr

        # Update running balance (Dr increases asset/expense; Cr increases the rest)
        if account.account_type in ('asset', 'expense'):
            running += dr - cr
        else:
            running += cr - dr

        lines.append({
            'date':            str(je.entry_date),
            'entry_number':    je.entry_number,
            'description':     je.description,
            'journal_type':    je.journal_type,
            'debit':           _d(dr),
            'credit':          _d(cr),
            'running_balance': _d(running),
            # Related-party flags (IAS 24) — line-level wins, JE-level fallback
            'is_related_party_line':  bool(getattr(ln, 'is_related_party', False)),
            'is_related_party_entry': bool(getattr(je, 'is_related_party', False)),
            'contact_name':           ln.contact.name if ln.contact_id else None,
        })

    return {
        'account': {
            'code':         account.code,
            'name':         account.name,
            'account_type': account.account_type,
            'sub_type':     account.sub_type,
        },
        'from_date':        str(from_date),
        'to_date':          str(to_date),
        'currency_code':    _company_currency(company_id),
        'opening_balance':  _d(opening_balance),
        'lines':            lines,
        'totals': {
            'total_debits':    _d(total_dr),
            'total_credits':   _d(total_cr),
            'closing_balance': _d(running),
        },
    }


# ---------------------------------------------------------------------------
# 8. Budget vs Actual
# ---------------------------------------------------------------------------

def build_budget_vs_actual(from_date, to_date, department=None, company_id=None):
    """
    Budget vs Actual variance report for the given date range.

    Compares budgeted amounts (from BudgetLine) against actual GL balances
    (from posted JE lines) for each account.

    Multi-entity (ADIC-OMNI-QA-001 §6): when company_id is supplied, scopes
    actuals to that company's posted JE lines. Budgets stay global until a
    per-entity budget surface exists.
    """
    from ledger.models import Account, FiscalPeriod
    from budgets.models import Budget, BudgetLine

    # Find fiscal periods that overlap the date range.
    # BUG (Oprah 2026-06-11): FiscalPeriod is PER-COMPANY, so an unscoped query
    # returned every entity's month — 14 companies x 12 months = 168 rows, i.e.
    # each month listed 14x in periods_covered AND the budget side (filtered by
    # fiscal_period__in=periods) summed all 14 entities' budgets (~14x inflated).
    # Scope to the selected company so periods + budgets match the actuals,
    # which are already company-scoped via _posted_lines(company_id).
    periods = FiscalPeriod.objects.filter(
        start_date__lte=to_date,
        end_date__gte=from_date,
    )
    if company_id:
        periods = periods.filter(company_id=company_id)
    periods = periods.order_by('start_date')

    if not periods.exists():
        return _empty_budget_vs_actual(from_date, to_date)

    # Get budgets for these periods. Budget.department is stored lowercase
    # (model TextChoices), but the report UI passes uppercase codes (FINANCE,
    # CLAIMS, …) — lowercase here so a "Finance" filter actually matches the
    # saved Finance budget instead of silently showing nothing.
    budget_filter = {'fiscal_period__in': periods}
    dept = (department or '').lower()
    if dept and dept != 'all':
        budget_filter['department'] = dept
    else:
        budget_filter['department'] = 'master'

    budgets = Budget.objects.filter(**budget_filter)

    # Aggregate budget amounts by account
    budget_by_account = {}
    for bl in BudgetLine.objects.filter(budget__in=budgets).select_related('account'):
        acct_id = bl.account_id
        budget_by_account[acct_id] = budget_by_account.get(acct_id, ZERO) + bl.amount

    # Get actual amounts from posted JE lines
    base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__gte=from_date,
        journal_entry__entry_date__lte=to_date,
    )
    actual_agg = _agg_je_lines(base)

    # Merge budget and actual into accounts
    all_account_ids = set(budget_by_account.keys()) | set(actual_agg.keys())
    if not all_account_ids:
        return _empty_budget_vs_actual(from_date, to_date)

    # Include deactivated accounts that have posted lines (see TB note).
    accounts = Account.objects.filter(
        id__in=all_account_ids,
        account_type__in=('revenue', 'expense'),
    ).order_by('code')

    revenue_rows = []
    expense_rows = []

    for acct in accounts:
        budgeted = budget_by_account.get(acct.id, ZERO)
        dr, cr = actual_agg.get(acct.id, (ZERO, ZERO))
        actual = _signed_balance(dr, cr, acct.account_type)

        variance = actual - budgeted
        variance_pct = (
            (variance / budgeted * 100) if budgeted != ZERO
            else Decimal('0.00')
        )

        row = {
            'code': acct.code,
            'name': acct.name,
            'account_type': acct.account_type,
            'sub_type': acct.sub_type,
            'budget': _d(budgeted),
            'actual': _d(actual),
            'variance': _d(variance),
            'variance_pct': _d(variance_pct),
            'favorable': (
                variance >= ZERO if acct.account_type == 'revenue'
                else variance <= ZERO
            ),
        }

        if acct.account_type == 'revenue':
            revenue_rows.append(row)
        else:
            expense_rows.append(row)

    total_rev_budget = sum((Decimal(r['budget']) for r in revenue_rows), ZERO)
    total_rev_actual = sum((Decimal(r['actual']) for r in revenue_rows), ZERO)
    total_exp_budget = sum((Decimal(r['budget']) for r in expense_rows), ZERO)
    total_exp_actual = sum((Decimal(r['actual']) for r in expense_rows), ZERO)

    net_budget = total_rev_budget - total_exp_budget
    net_actual = total_rev_actual - total_exp_actual
    net_variance = net_actual - net_budget

    return {
        'from_date': str(from_date),
        'to_date': str(to_date),
        'department': department or 'master',
        'periods_covered': [p.period_name for p in periods],
        'revenue': {
            'accounts': revenue_rows,
            'total_budget': _d(total_rev_budget),
            'total_actual': _d(total_rev_actual),
            'total_variance': _d(total_rev_actual - total_rev_budget),
        },
        'expenses': {
            'accounts': expense_rows,
            'total_budget': _d(total_exp_budget),
            'total_actual': _d(total_exp_actual),
            'total_variance': _d(total_exp_actual - total_exp_budget),
        },
        'net_result': {
            'budget': _d(net_budget),
            'actual': _d(net_actual),
            'variance': _d(net_variance),
            'favorable': net_variance >= ZERO,
        },
    }


def _empty_budget_vs_actual(from_date, to_date):
    z = '0.00'
    return {
        'from_date': str(from_date),
        'to_date': str(to_date),
        'department': 'master',
        'periods_covered': [],
        'revenue': {'accounts': [], 'total_budget': z, 'total_actual': z, 'total_variance': z},
        'expenses': {'accounts': [], 'total_budget': z, 'total_actual': z, 'total_variance': z},
        'net_result': {'budget': z, 'actual': z, 'variance': z, 'favorable': True},
    }


# ---------------------------------------------------------------------------
# 9. VAT Return
# ---------------------------------------------------------------------------

def build_vat_return(from_date, to_date, company_id=None):
    """
    VAT return preparation report.

    Calculates output VAT (from customer invoices) and input VAT
    (from vendor bills) for the period, producing the net VAT position.

    Multi-entity (ADIC-OMNI-QA-001 §6): when company_id is supplied,
    scopes the invoice queries to that company via contact.company_id.
    """
    from billing.models import Invoice, InvoiceLine

    def _scope(qs):
        return qs.filter(contact__company_id=company_id) if company_id else qs

    # Output VAT: tax on customer invoices posted in period
    output_invoices = _scope(Invoice.objects.filter(
        invoice_type__in=['customer_invoice'],
        status__in=['posted', 'partially_paid', 'paid', 'overdue'],
        issue_date__gte=from_date,
        issue_date__lte=to_date,
    )).select_related('contact')

    output_lines = []
    total_output_sales = ZERO
    total_output_vat = ZERO

    for inv in output_invoices:
        inv_vat = inv.tax_total or ZERO
        inv_net = inv.subtotal or ZERO
        if inv_vat > ZERO or inv_net > ZERO:
            output_lines.append({
                'invoice_number': inv.invoice_number,
                'contact_name': inv.contact.name,
                'issue_date': str(inv.issue_date),
                'net_amount': _d(inv_net),
                'vat_amount': _d(inv_vat),
                'total_amount': _d(inv.total_amount),
            })
            total_output_sales += inv_net
            total_output_vat += inv_vat

    # Credit notes reduce output VAT
    credit_notes = _scope(Invoice.objects.filter(
        invoice_type='credit_note',
        status__in=['posted', 'partially_paid', 'paid'],
        issue_date__gte=from_date,
        issue_date__lte=to_date,
    )).select_related('contact')

    credit_note_lines = []
    total_cn_sales = ZERO
    total_cn_vat = ZERO

    for cn in credit_notes:
        cn_vat = cn.tax_total or ZERO
        cn_net = cn.subtotal or ZERO
        if cn_vat > ZERO or cn_net > ZERO:
            credit_note_lines.append({
                'invoice_number': cn.invoice_number,
                'contact_name': cn.contact.name,
                'issue_date': str(cn.issue_date),
                'net_amount': _d(cn_net),
                'vat_amount': _d(cn_vat),
                'total_amount': _d(cn.total_amount),
            })
            total_cn_sales += cn_net
            total_cn_vat += cn_vat

    # Input VAT: tax on vendor bills posted in period
    input_invoices = _scope(Invoice.objects.filter(
        invoice_type__in=['vendor_bill'],
        status__in=['posted', 'partially_paid', 'paid', 'overdue'],
        issue_date__gte=from_date,
        issue_date__lte=to_date,
    )).select_related('contact')

    input_lines = []
    total_input_purchases = ZERO
    total_input_vat = ZERO

    for inv in input_invoices:
        inv_vat = inv.tax_total or ZERO
        inv_net = inv.subtotal or ZERO
        if inv_vat > ZERO or inv_net > ZERO:
            input_lines.append({
                'invoice_number': inv.invoice_number,
                'contact_name': inv.contact.name,
                'issue_date': str(inv.issue_date),
                'net_amount': _d(inv_net),
                'vat_amount': _d(inv_vat),
                'total_amount': _d(inv.total_amount),
            })
            total_input_purchases += inv_net
            total_input_vat += inv_vat

    # Net VAT position (domestic sales/purchases only — see reverse_charge_vat
    # below for the self-assessed imported-services legs, filed as two
    # separate BURS lines rather than folded into this net figure).
    adjusted_output_vat = total_output_vat - total_cn_vat
    net_vat = adjusted_output_vat - total_input_vat
    vat_payable = net_vat > ZERO

    # -------------------------------------------------------------------
    # Reverse-charge VAT — imported remote services (VAT Amendment Act
    # No.16 of 2025, effective 1 June 2026). Foreign digital-service
    # suppliers (AWS, Anthropic, etc.) carry no Botswana VAT, so finance
    # self-assesses via billing.ReverseChargeEntry (captured directly,
    # not through Invoice/InvoiceLine). BURS requires the output and input
    # legs filed as TWO SEPARATE lines — a single net line will not file —
    # so these sit alongside, not merged into, the domestic VAT lines above.
    # -------------------------------------------------------------------
    from billing.models import ReverseChargeEntry

    rc_entries = ReverseChargeEntry.objects.filter(
        invoice_date__gte=from_date,
        invoice_date__lte=to_date,
        reverse_charge_applies=True,
    )
    if company_id:
        rc_entries = rc_entries.filter(company_id=company_id)

    reverse_charge_lines = []
    total_rc_output_vat = ZERO
    total_rc_input_vat = ZERO

    for entry in rc_entries.order_by('invoice_date'):
        entry_output_vat = entry.output_vat or ZERO
        entry_input_vat = entry.input_vat_recoverable or ZERO
        reverse_charge_lines.append({
            'vendor': entry.vendor,
            'category': entry.category,
            'category_label': entry.get_category_display(),
            'invoice_date': str(entry.invoice_date),
            'bwp_amount': _d(entry.bwp_amount),
            'output_vat': _d(entry_output_vat),
            'input_vat_recoverable': _d(entry_input_vat),
            'net_vat_cost': _d(entry.net_vat_cost),
        })
        total_rc_output_vat += entry_output_vat
        total_rc_input_vat += entry_input_vat

    total_rc_net_vat_cost = total_rc_output_vat - total_rc_input_vat

    return {
        'from_date': str(from_date),
        'to_date': str(to_date),
        'output_vat': {
            'invoices': output_lines,
            'total_sales': _d(total_output_sales),
            'total_vat': _d(total_output_vat),
        },
        'credit_notes': {
            'invoices': credit_note_lines,
            'total_sales': _d(total_cn_sales),
            'total_vat': _d(total_cn_vat),
        },
        'input_vat': {
            'invoices': input_lines,
            'total_purchases': _d(total_input_purchases),
            'total_vat': _d(total_input_vat),
        },
        # Two separate self-assessed lines, immediately after the domestic
        # VAT lines above — BURS needs them split, not netted.
        'reverse_charge_vat': {
            'entries': reverse_charge_lines,
            'total_output_vat': _d(total_rc_output_vat),
            'total_input_vat_recoverable': _d(total_rc_input_vat),
            'total_net_vat_cost': _d(total_rc_net_vat_cost),
        },
        'summary': {
            'output_vat': _d(total_output_vat),
            'credit_note_vat': _d(total_cn_vat),
            'adjusted_output_vat': _d(adjusted_output_vat),
            'input_vat': _d(total_input_vat),
            'net_vat': _d(net_vat),
            'vat_payable': vat_payable,
            'status_label': 'VAT Payable to BURS' if vat_payable else 'VAT Refundable from BURS',
            # Reverse-charge on imported remote services — filed as its own
            # two lines (see 'reverse_charge_vat' above for the itemised
            # entries); surfaced here too so the summary panel can render
            # them immediately after the existing VAT line without a
            # second round trip.
            'reverse_charge_output_vat': _d(total_rc_output_vat),
            'reverse_charge_input_vat': _d(total_rc_input_vat),
            'reverse_charge_net_vat_cost': _d(total_rc_net_vat_cost),
        },
    }


# ---------------------------------------------------------------------------
# 10. Expense Analysis
# ---------------------------------------------------------------------------

def build_expense_analysis(from_date, to_date, company_id=None):
    """
    Detailed expense breakdown by category with period-over-period comparison.

    CFO directive 2026-05-21 (Expense Analysis report showed every account
    bucketed under 'Other Asset'): the legacy grouping used Account.sub_type
    which the Odoo importer defaulted to 'other_asset' on every expense
    account it auto-created. Switch to Account.fs_line_item — the MA P&L
    line label that's actually populated by seed_ma_classifications — and
    fall back to a human label derived from the account name when
    fs_line_item is blank.

    Multi-entity (ADIC-OMNI-QA-001 §6): when company_id is None the function
    consolidates across every entity the caller has access to (the view
    layer applies the access gate). When set, only that company's posted
    lines are included.
    """
    from ledger.models import Account

    base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__gte=from_date,
        journal_entry__entry_date__lte=to_date,
    )
    agg = _agg_je_lines(base)

    if not agg:
        return _empty_expense_analysis(from_date, to_date)

    # Load expense accounts (incl. deactivated with posted lines).
    accounts = Account.objects.filter(
        id__in=set(agg),
        account_type='expense',
    ).order_by('code')

    def _category_for(acct) -> str:
        label = (acct.fs_line_item or '').strip()
        if label:
            return label
        # Sub-type still useful when it's not the dummy default.
        sub = (acct.sub_type or '').strip()
        if sub and sub.lower() not in ('other_asset', 'other'):
            return sub.replace('_', ' ').title()
        return 'Uncategorised'

    # Group by category (fs_line_item-first)
    by_category = {}
    total_expenses = ZERO

    for acct in accounts:
        dr, cr = agg.get(acct.id, (ZERO, ZERO))
        balance = _signed_balance(dr, cr, 'expense')
        if balance == ZERO:
            continue

        category = _category_for(acct)
        if category not in by_category:
            by_category[category] = {'accounts': [], 'total': ZERO}

        by_category[category]['accounts'].append({
            'code': acct.code,
            'name': acct.name,
            'amount': _d(balance),
        })
        by_category[category]['total'] += balance
        total_expenses += balance

    # Calculate previous period for comparison
    period_days = (to_date - from_date).days + 1
    prev_to = from_date - timedelta(days=1)
    prev_from = prev_to - timedelta(days=period_days - 1)

    prev_base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__gte=prev_from,
        journal_entry__entry_date__lte=prev_to,
    )
    prev_agg = _agg_je_lines(prev_base)

    prev_total = ZERO
    prev_by_category = {}

    if prev_agg:
        prev_accounts = Account.objects.filter(
            id__in=set(prev_agg),
            account_type='expense',
        )
        for acct in prev_accounts:
            dr, cr = prev_agg.get(acct.id, (ZERO, ZERO))
            balance = _signed_balance(dr, cr, 'expense')
            if balance == ZERO:
                continue
            category = _category_for(acct)
            prev_by_category[category] = prev_by_category.get(category, ZERO) + balance
            prev_total += balance

    # Build category summary with period-over-period comparison
    categories = []
    for cat_key, cat_data in sorted(by_category.items()):
        cat_total = cat_data['total']
        prev_cat_total = prev_by_category.get(cat_key, ZERO)
        change = cat_total - prev_cat_total
        change_pct = (
            (change / prev_cat_total * 100) if prev_cat_total != ZERO
            else Decimal('0.00')
        )
        pct_of_total = (
            (cat_total / total_expenses * 100) if total_expenses != ZERO
            else Decimal('0.00')
        )

        # cat_key is already an fs_line_item human label (e.g. "Employee
        # costs", "Bad Debt Expenses") so we don't title-case + underscore-
        # bash it any more. Only do that for the legacy 'uncategorised' /
        # sub_type fallbacks (no spaces).
        cat_label = cat_key if ' ' in cat_key else cat_key.replace('_', ' ').title()
        categories.append({
            'category': cat_key,
            'category_label': cat_label,
            'accounts': cat_data['accounts'],
            'total': _d(cat_total),
            'previous_total': _d(prev_cat_total),
            'change': _d(change),
            'change_pct': _d(change_pct),
            'pct_of_total': _d(pct_of_total),
        })

    mom_change = total_expenses - prev_total
    mom_pct = (
        (mom_change / prev_total * 100) if prev_total != ZERO
        else Decimal('0.00')
    )

    return {
        'from_date': str(from_date),
        'to_date': str(to_date),
        'categories': categories,
        'totals': {
            'current_total': _d(total_expenses),
            'previous_total': _d(prev_total),
            'previous_from': str(prev_from),
            'previous_to': str(prev_to),
            'change': _d(mom_change),
            'change_pct': _d(mom_pct),
        },
    }


# ---------------------------------------------------------------------------
# Expense Analysis v2 — mirror MA P&L expense categories + supplier drill
# ---------------------------------------------------------------------------

def build_expense_analysis_detail(from_date, to_date, *, company_id=None):
    """
    CFO directive 2026-05-21. The legacy `build_expense_analysis` grouped
    by `Account.fs_line_item` which left foreign-CoA entities with one
    'Uncategorised' bucket. The new view mirrors the MA P&L expense
    lines exactly (Employee costs / Bonus Pay / Operating Expenses / IT
    Expenses / Risk Licensing / Licensing Fee / Paygates / Telephone &
    Internet / Marketing & Advertising / Staff Welfare / Consultancy
    Fees) and drills down per category into:

      * Per-account totals (which GL codes contribute)
      * Per-supplier totals (JEL.contact roll-up)
      * Top JE lines (recent + biggest), so the user can click through
        to the journal entry

    Same prior-period comparison as the legacy view. Returned shape is
    a superset, so the existing /reports/expense-analysis frontend keeps
    rendering while the new drill-down sections become available.
    """
    from ledger.models import JournalEntryLine
    from .ma_pl_spec import MA_LINES, SECTIONS

    # Pick the operating_expenses section's line ids
    opex_section = next((s for s in SECTIONS if s['id'] == 'operating_expenses'), None)
    opex_line_ids = opex_section['lines'] if opex_section else []

    # Build the canonical (label, codes) tuples from the MA spec
    ma_categories = [
        {
            'id':    line_id,
            'label': MA_LINES[line_id]['label'],
            'codes': list(MA_LINES[line_id]['codes']),
        }
        for line_id in opex_line_ids
    ]

    # Helper: query posted JEL lines for a set of GL codes in window
    def _lines_for_codes(codes, from_d, to_d):
        from django.db.models import Q
        q = Q()
        for c in codes:
            q |= Q(account__code=c) | Q(account__code__endswith='_' + c)
        qs = JournalEntryLine.objects.filter(
            journal_entry__entry_date__gte=from_d,
            journal_entry__entry_date__lte=to_d,
            journal_entry__status='posted',
        ).filter(q).select_related(
            'account', 'contact', 'journal_entry',
        )
        if company_id is not None:
            qs = qs.filter(journal_entry__company_id=company_id)
        return qs

    period_days = (to_date - from_date).days + 1
    prev_to = from_date - timedelta(days=1)
    prev_from = prev_to - timedelta(days=period_days - 1)

    categories_out = []
    total_current = ZERO
    total_previous = ZERO

    for cat in ma_categories:
        codes = cat['codes']
        cur_qs = _lines_for_codes(codes, from_date, to_date)
        prv_qs = _lines_for_codes(codes, prev_from, prev_to)

        # Net signed balance for the category (debit - credit; expense
        # is debit-natural).
        cur_total = sum(
            ((ln.debit_bwp or ZERO) - (ln.credit_bwp or ZERO))
            for ln in cur_qs
        ) or ZERO
        prv_total = sum(
            ((ln.debit_bwp or ZERO) - (ln.credit_bwp or ZERO))
            for ln in prv_qs
        ) or ZERO

        # By account
        by_account = {}
        for ln in cur_qs:
            key = ln.account.code
            row = by_account.setdefault(key, {
                'code':   ln.account.code,
                'name':   ln.account.name,
                'amount': ZERO,
            })
            row['amount'] += (ln.debit_bwp or ZERO) - (ln.credit_bwp or ZERO)

        # By supplier (contact)
        by_supplier = {}
        for ln in cur_qs:
            key = ln.contact_id or '__nocontact__'
            row = by_supplier.setdefault(key, {
                'contact_id':   str(ln.contact_id) if ln.contact_id else None,
                'contact_name': ln.contact.name if ln.contact_id else '(no supplier on JE line)',
                'amount':       ZERO,
                'line_count':   0,
            })
            row['amount']     += (ln.debit_bwp or ZERO) - (ln.credit_bwp or ZERO)
            row['line_count'] += 1

        # Top JE lines (by amount, current period — capped at 50 to keep
        # the payload small; the GL extract-all CSV is the full audit
        # trail).
        ranked = sorted(
            cur_qs,
            key=lambda l: abs((l.debit_bwp or ZERO) - (l.credit_bwp or ZERO)),
            reverse=True,
        )[:50]
        top_lines = [
            {
                'date':         ln.journal_entry.entry_date.isoformat(),
                'entry_number': ln.journal_entry.entry_number,
                'description':  (ln.description or ln.journal_entry.description or '')[:200],
                'account_code': ln.account.code,
                'account_name': ln.account.name,
                'contact_name': ln.contact.name if ln.contact_id else '',
                'debit':        _d(ln.debit_bwp or ZERO),
                'credit':       _d(ln.credit_bwp or ZERO),
                'net':          _d((ln.debit_bwp or ZERO) - (ln.credit_bwp or ZERO)),
            }
            for ln in ranked
        ]

        change = cur_total - prv_total
        change_pct = ((change / prv_total * 100) if prv_total != ZERO else ZERO)

        categories_out.append({
            'id':          cat['id'],
            'label':       cat['label'],
            'codes':       codes,
            'amount':      _d(cur_total),
            'prev_amount': _d(prv_total),
            'change':      _d(change),
            'change_pct':  _d(change_pct),
            'by_account':  [
                {**row, 'amount': _d(row['amount'])}
                for row in sorted(by_account.values(),
                                  key=lambda r: abs(r['amount']), reverse=True)
            ],
            'by_supplier': [
                {**row, 'amount': _d(row['amount'])}
                for row in sorted(by_supplier.values(),
                                  key=lambda r: abs(r['amount']), reverse=True)
            ],
            'top_lines':   top_lines,
        })

        total_current  += cur_total
        total_previous += prv_total

    grand_change = total_current - total_previous
    grand_pct    = ((grand_change / total_previous * 100) if total_previous != ZERO else ZERO)

    return {
        'from_date':     str(from_date),
        'to_date':       str(to_date),
        'previous_from': str(prev_from),
        'previous_to':   str(prev_to),
        'categories':    categories_out,
        'totals': {
            'current_total':  _d(total_current),
            'previous_total': _d(total_previous),
            'change':         _d(grand_change),
            'change_pct':     _d(grand_pct),
        },
    }


def _empty_expense_analysis(from_date, to_date):
    return {
        'from_date': str(from_date),
        'to_date': str(to_date),
        'categories': [],
        'totals': {
            'current_total': '0.00',
            'previous_total': '0.00',
            'previous_from': str(from_date),
            'previous_to': str(to_date),
            'change': '0.00',
            'change_pct': '0.00',
        },
    }


# ---------------------------------------------------------------------------
# 11. Management Accounts Pack
# ---------------------------------------------------------------------------

def build_management_pack(from_date, to_date, company_id=None):
    """
    Aggregated management accounts package combining:
    - MA P&L (from reporting.ma_pl — the CFO MA workbook layout, source of truth)
    - Legacy P&L summary (kept for backwards-compat consumers)
    - Balance sheet summary
    - Cash position
    - Insurance KPIs (loss ratio, expense ratio, combined ratio) — derived
      from MA P&L's net earned premium, NOT raw revenue
    - Budget vs actual summary (if budgets exist)

    Optionally scoped to a single company (subsidiary).
    """
    from reporting.ma_pl import build_ma_pl

    # MA P&L is the source of truth for insurance figures (GWP, NEP, PAT,
    # ratios). It maps the Odoo CoA (100001 codes) directly to the CFO MA
    # workbook layout.
    ma = build_ma_pl(from_date, to_date, company_id=company_id)
    ma_totals = ma.get('totals', {})

    # Legacy P&L is still useful for the dashboard tiles that read
    # raw revenue / cost-of-insurance buckets, and for the 4100-coded
    # accounts that pre-date the Odoo backfill. Kept verbatim.
    pl = build_profit_loss(from_date, to_date, company_id=company_id)
    bs = build_balance_sheet(to_date, company_id=company_id)
    # Cash tile is period-aware: closing balance at to_date, not all-time.
    cash = build_cash_position(company_id=company_id, as_of=to_date)

    # ----------------------------------------------------------------
    # Insurance KPIs — driven by MA P&L (the correct insurance maths)
    # ----------------------------------------------------------------
    # Previously: earned_premium was set to total_revenue (= raw GWP).
    # That made loss/expense/combined ratios mathematically wrong for an
    # insurance company because GWP ≠ Net Earned Premium. For FY25 ADIC,
    # GWP = 125M but NEP = 52M — a 2.4× difference that contaminated every
    # downstream KPI.

    earned_premium    = Decimal(ma_totals.get('net_earned_premium', '0.00'))
    claims_incurred   = abs(Decimal(ma_totals.get('net_claim_incurred', '0.00')))
    total_opex_signed = Decimal(ma_totals.get('total_operating_expenses', '0.00'))
    # MA opex carries an 'expense' sign convention (negative subtotal).
    # For the ratio we want a positive cost figure.
    total_opex = abs(total_opex_signed)
    net_profit = Decimal(ma_totals.get('pat', '0.00'))
    gwp        = Decimal(ma_totals.get('gross_written_premium', '0.00'))

    def _pct(numer, denom):
        if denom == ZERO:
            return Decimal('0.00')
        return (numer / denom * Decimal('100')).quantize(TWO)

    loss_ratio     = _pct(claims_incurred, earned_premium)
    expense_ratio  = _pct(total_opex,      earned_premium)
    combined_ratio = loss_ratio + expense_ratio
    profit_margin  = _pct(net_profit,      gwp)

    # ----------------------------------------------------------------
    # Budget comparison (unchanged)
    # ----------------------------------------------------------------
    budget_summary = None
    try:
        bva = build_budget_vs_actual(from_date, to_date)
        if bva.get('revenue', {}).get('total_budget', '0.00') != '0.00':
            budget_summary = {
                'revenue_budget': bva['revenue']['total_budget'],
                'revenue_actual': bva['revenue']['total_actual'],
                'revenue_variance': bva['revenue']['total_variance'],
                'expense_budget': bva['expenses']['total_budget'],
                'expense_actual': bva['expenses']['total_actual'],
                'expense_variance': bva['expenses']['total_variance'],
                'net_budget': bva['net_result']['budget'],
                'net_actual': bva['net_result']['actual'],
                'net_variance': bva['net_result']['variance'],
            }
    except Exception:
        pass

    return {
        'from_date': str(from_date),
        'to_date': str(to_date),
        # New: full MA P&L payload — dashboard tiles SHOULD read from here.
        # 'totals.gross_written_premium' is the GWP tile.
        # 'totals.pat' is the PAT tile.
        # 'totals.net_earned_premium' is the NEP for ratio maths.
        'ma_pl': ma,
        # Legacy P&L payload — kept for any consumer that hasn't migrated
        # to the MA P&L payload yet. Numbers here use raw account_type
        # roll-up via build_profit_loss; insurance subtotals (NEP, ratios)
        # are NOT in this block — use 'ma_pl' for those.
        'profit_loss': {
            'total_revenue': pl.get('revenue', {}).get('total', '0.00'),
            'cost_of_insurance': pl.get('cost_of_insurance', {}).get('total', '0.00'),
            'gross_result': pl.get('gross_result', '0.00'),
            'operating_expenses': pl.get('operating_expenses', {}).get('total', '0.00'),
            'net_profit': pl.get('net_profit', '0.00'),
            'is_profit': pl.get('is_profit', True),
        },
        'balance_sheet': {
            'total_assets': bs.get('totals', {}).get('total_assets', '0.00'),
            'total_liabilities': bs.get('totals', {}).get('total_liabilities', '0.00'),
            'total_equity': bs.get('totals', {}).get('total_equity', '0.00'),
            'balanced': bs.get('totals', {}).get('balanced', True),
        },
        'cash_position': {
            'total_cash_bwp': cash.get('total_bwp', '0.00'),
            'accounts': cash.get('accounts', []),
        },
        'insurance_kpis': {
            'gross_written_premium': _d(gwp),
            'earned_premium':        _d(earned_premium),
            'claims_incurred':       _d(claims_incurred),
            'operating_expenses':    _d(total_opex),
            'loss_ratio':            _d(loss_ratio),
            'expense_ratio':         _d(expense_ratio),
            'combined_ratio':        _d(combined_ratio),
            'profit_margin':         _d(profit_margin),
            'pat':                   _d(net_profit),
            'underwriting_result':   'Profitable' if combined_ratio < Decimal('100') else 'Unprofitable',
        },
        'budget_comparison': budget_summary,
    }


# ---------------------------------------------------------------------------
# Asset Register
# ---------------------------------------------------------------------------

def build_asset_register(as_of, *, category=None, status_filter=None, company_id=None):
    """
    Fixed Assets register as at *as_of*.

    For each asset returns: tag, name, category, cost, accumulated
    depreciation as at *as_of*, NBV as at *as_of*, location, custodian, status.

    Filters:
        category       — AssetCategory.id (UUID) or category code
        status_filter  — one of Asset.Status values
        company_id     — Company.id (UUID)

    CFO directive 2026-05-20 (FAR Defects Memo DR-003): exclude assets
    whose `purchase_date` is later than the reporting `as_of` — those
    are post-period acquisitions that must not appear in an FY-end
    register. Also exclude the `migrated_duplicate` quarantine bucket
    so duplicates don't bleed back into reports until CFO sign-off
    completes the hard-delete pass.
    """
    # Imported here to avoid circular imports at module load time
    from assets.models import Asset, DepreciationEntry

    qs = Asset.objects.select_related('category', 'company').all()
    # DR-003 cut-off: a FAR is the snapshot AS AT as_of — assets bought
    # after that date legally aren't on the books yet.
    qs = qs.filter(purchase_date__lte=as_of)
    # DR-002 quarantine: hide flagged duplicates from the register.
    qs = qs.exclude(status='migrated_duplicate')
    if company_id:
        qs = qs.filter(company_id=company_id)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if category:
        # Accept either a UUID or a category code
        if len(str(category)) == 36 and '-' in str(category):
            qs = qs.filter(category_id=category)
        else:
            qs = qs.filter(category__code=category)

    items = []
    totals_cost = ZERO
    totals_accum = ZERO
    totals_nbv = ZERO

    for asset in qs:
        # Booked depreciation at or before as_of
        booked = DepreciationEntry.objects.filter(
            asset=asset,
            period_end_date__lte=as_of,
            reversed_at__isnull=True,
        ).aggregate(t=Sum('amount'))['t'] or ZERO

        accum = (asset.opening_accumulated_depreciation or ZERO) + (booked or ZERO)
        nbv = (asset.cost or ZERO) - accum

        items.append({
            'id':                 str(asset.id),
            'tag_number':         asset.tag_number,
            'external_ref':       asset.external_ref,
            'name':               asset.name,
            'category_code':      asset.category.code,
            'category_name':      asset.category.name,
            'company_code':       asset.company.code,
            'cost':               _d(asset.cost or ZERO),
            'accumulated_depr':   _d(accum),
            'net_book_value':     _d(nbv),
            'salvage_value':      _d(asset.salvage_value or ZERO),
            'method':             asset.method,
            'useful_life_months': asset.useful_life_months,
            'purchase_date':      asset.purchase_date.isoformat() if asset.purchase_date else None,
            'in_service_date':    asset.in_service_date.isoformat() if asset.in_service_date else None,
            'last_depr_date':     asset.last_depreciation_date.isoformat() if asset.last_depreciation_date else None,
            'location':           asset.location,
            'custodian':          asset.custodian,
            'status':             asset.status,
        })

        totals_cost += asset.cost or ZERO
        totals_accum += accum
        totals_nbv += nbv

    # Group totals by category (handy for a balance-sheet tie-out)
    by_category = {}
    for it in items:
        bucket = by_category.setdefault(it['category_code'], {
            'category_code': it['category_code'],
            'category_name': it['category_name'],
            'count': 0,
            'cost': ZERO,
            'accumulated_depr': ZERO,
            'net_book_value': ZERO,
        })
        bucket['count'] += 1
        bucket['cost'] += Decimal(it['cost'])
        bucket['accumulated_depr'] += Decimal(it['accumulated_depr'])
        bucket['net_book_value'] += Decimal(it['net_book_value'])
    by_category_list = [
        {**b,
         'cost': _d(b['cost']),
         'accumulated_depr': _d(b['accumulated_depr']),
         'net_book_value': _d(b['net_book_value'])}
        for b in by_category.values()
    ]

    return {
        'as_of':       as_of.isoformat(),
        'count':       len(items),
        'items':       items,
        'by_category': by_category_list,
        'totals': {
            'cost':             _d(totals_cost),
            'accumulated_depr': _d(totals_accum),
            'net_book_value':   _d(totals_nbv),
        },
    }


# ---------------------------------------------------------------------------
# Asset Movement Report (IAS 16 Property, Plant & Equipment roll-forward)
# ---------------------------------------------------------------------------

def build_asset_movement(from_date, to_date, *, company_id=None):
    """
    IAS 16 Asset Movement Report — roll-forward by category between
    `from_date` (inclusive) and `to_date` (inclusive).

    Output per category:
      gross_cost:       opening, additions, disposals, closing
      accumulated_depr: opening, charge,    disposals, closing
      nbv:              opening, closing

    Excludes status='migrated_duplicate' (DR-002 quarantine bucket).
    """
    from assets.models import Asset, DepreciationEntry, AssetDisposal

    if from_date > to_date:
        raise ValueError(f"from_date {from_date} must be <= to_date {to_date}")

    base_qs = (
        Asset.objects.select_related('category')
        .exclude(status='migrated_duplicate')
    )
    if company_id:
        base_qs = base_qs.filter(company_id=company_id)

    buckets = {}

    def _bucket(asset):
        cat = asset.category
        b = buckets.setdefault(cat.code, {
            'category_code': cat.code,
            'category_name': cat.name,
            'gross_cost':       {'opening': ZERO, 'additions': ZERO, 'disposals': ZERO, 'closing': ZERO},
            'accumulated_depr': {'opening': ZERO, 'charge':    ZERO, 'disposals': ZERO, 'closing': ZERO},
        })
        return b

    for a in base_qs.iterator(chunk_size=200):
        cost = a.cost or ZERO
        b = _bucket(a)

        # Structural audit 2026-05-24: AssetDisposal.Status has no 'posted'
        # value — the canonical "posted to GL" state on this model is APPROVED
        # (per assets/models.py:512 — "Approved (posted to GL)"). Prior query
        # filtered on a non-existent state and silently returned no disposals,
        # making the IAS 16 asset-movement disposals column always zero.
        disposal = AssetDisposal.objects.filter(asset=a, status='approved').first()
        disposed_before = bool(disposal and disposal.disposal_date <  from_date)
        disposed_within = bool(disposal and from_date <= disposal.disposal_date <= to_date)

        # Gross cost
        if a.purchase_date and a.purchase_date <  from_date and not disposed_before:
            b['gross_cost']['opening']   += cost
        if a.purchase_date and from_date <= a.purchase_date <= to_date:
            b['gross_cost']['additions'] += cost
        if disposed_within:
            b['gross_cost']['disposals']        += (disposal.cost_at_disposal or cost)
            b['accumulated_depr']['disposals']  += (disposal.accumulated_depr_at_disposal or ZERO)
        if (a.purchase_date and a.purchase_date <= to_date
                and not (disposal and disposal.disposal_date <= to_date)):
            b['gross_cost']['closing']   += cost

        # Accumulated depreciation
        if a.purchase_date and a.purchase_date < from_date and not disposed_before:
            opening_accum = a.opening_accumulated_depreciation or ZERO
            booked_pre = DepreciationEntry.objects.filter(
                asset=a,
                period_end_date__lt=from_date,
                reversed_at__isnull=True,
            ).aggregate(t=Sum('amount'))['t'] or ZERO
            b['accumulated_depr']['opening'] += (opening_accum + booked_pre)

        charge = DepreciationEntry.objects.filter(
            asset=a,
            period_end_date__gte=from_date,
            period_end_date__lte=to_date,
            reversed_at__isnull=True,
        ).aggregate(t=Sum('amount'))['t'] or ZERO
        b['accumulated_depr']['charge'] += charge

    rows = []
    tot_gross = {'opening': ZERO, 'additions': ZERO, 'disposals': ZERO, 'closing': ZERO}
    tot_accum = {'opening': ZERO, 'charge':    ZERO, 'disposals': ZERO, 'closing': ZERO}
    tot_nbv   = {'opening': ZERO, 'closing':   ZERO}

    for code in sorted(buckets):
        b = buckets[code]
        b['accumulated_depr']['closing'] = (
            b['accumulated_depr']['opening']
            + b['accumulated_depr']['charge']
            - b['accumulated_depr']['disposals']
        )
        b['nbv'] = {
            'opening': b['gross_cost']['opening'] - b['accumulated_depr']['opening'],
            'closing': b['gross_cost']['closing'] - b['accumulated_depr']['closing'],
        }
        rows.append({
            'category_code':    b['category_code'],
            'category_name':    b['category_name'],
            'gross_cost':       {k: _d(v) for k, v in b['gross_cost'].items()},
            'accumulated_depr': {k: _d(v) for k, v in b['accumulated_depr'].items()},
            'nbv':              {k: _d(v) for k, v in b['nbv'].items()},
        })
        for k in tot_gross: tot_gross[k] += b['gross_cost'][k]
        for k in tot_accum: tot_accum[k] += b['accumulated_depr'][k]
        for k in tot_nbv:   tot_nbv[k]   += b['nbv'][k]

    return {
        'from_date': from_date.isoformat(),
        'to_date':   to_date.isoformat(),
        'rows':      rows,
        'totals': {
            'gross_cost':       {k: _d(v) for k, v in tot_gross.items()},
            'accumulated_depr': {k: _d(v) for k, v in tot_accum.items()},
            'nbv':              {k: _d(v) for k, v in tot_nbv.items()},
        },
    }


# ---------------------------------------------------------------------------
# Related Party Transactions (IAS 24 / NBFIRA disclosure)
# ---------------------------------------------------------------------------

def build_related_party_transactions(from_date, to_date, *, fiscal_year_start=None, company_id=None):
    """
    Related-party JE lines for the given window, plus a fiscal-year-to-date roll-up.

    Returns:
      window  -- list of related-party lines between from_date and to_date
      fy_to_date -- list of related-party lines from fiscal_year_start to to_date
      by_contact_window  -- subtotals per related party for the window
      by_contact_fy      -- subtotals per related party for the fiscal year
      summary -- totals (count, debit, credit) for both buckets
    """
    from billing.models import Contact
    from ledger.models import JournalEntryLine

    if fiscal_year_start is None:
        # CFO directive 2026-05-20: derive FY start from the company's
        # fy_end_month, not hardcoded July.
        fiscal_year_start = _get_fiscal_year_start(
            to_date, _fy_end_month_for(company_id),
        )

    base = (
        JournalEntryLine.objects
        .filter(journal_entry__status__in=[
            'posted', 'pending_approval',
        ])
        .filter(is_related_party=True)
        .select_related('account', 'contact', 'journal_entry')
    )

    def _slice(qs, frm, to):
        return qs.filter(
            journal_entry__entry_date__gte=frm,
            journal_entry__entry_date__lte=to,
        )

    window_qs = _slice(base, from_date, to_date)
    fy_qs     = _slice(base, fiscal_year_start, to_date)

    def _row(line):
        je = line.journal_entry
        return {
            'entry_number':  je.entry_number,
            'entry_date':    je.entry_date.isoformat(),
            'status':        je.status,
            'description':   je.description,
            'account_code':  line.account.code,
            'account_name':  line.account.name,
            'contact_name':  line.contact.name if line.contact_id else '—',
            'relationship':  (line.contact.related_party_relationship
                              if line.contact_id and line.contact.is_related_party else ''),
            'debit_bwp':     _d(line.debit_bwp),
            'credit_bwp':    _d(line.credit_bwp),
            'line_description': line.description or '',
        }

    window_rows = [_row(l) for l in window_qs.order_by('-journal_entry__entry_date')]
    fy_rows     = [_row(l) for l in fy_qs.order_by('-journal_entry__entry_date')]

    def _by_contact(qs):
        out = {}
        for line in qs.select_related('contact'):
            key = line.contact_id or 'unknown'
            name = line.contact.name if line.contact_id else 'No contact attached'
            rel = (line.contact.related_party_relationship
                   if line.contact_id and line.contact.is_related_party else '')
            bucket = out.setdefault(str(key), {
                'contact_id': str(key) if line.contact_id else None,
                'contact_name': name,
                'relationship': rel,
                'count': 0,
                'debit_bwp': ZERO,
                'credit_bwp': ZERO,
            })
            bucket['count'] += 1
            bucket['debit_bwp']  += line.debit_bwp  or ZERO
            bucket['credit_bwp'] += line.credit_bwp or ZERO
        return [
            {**b,
             'debit_bwp':  _d(b['debit_bwp']),
             'credit_bwp': _d(b['credit_bwp'])}
            for b in out.values()
        ]

    return {
        'window': {
            'from': from_date.isoformat(),
            'to':   to_date.isoformat(),
            'rows': window_rows,
            'count': len(window_rows),
            'by_contact': _by_contact(window_qs),
        },
        'fiscal_year_to_date': {
            'from': fiscal_year_start.isoformat(),
            'to':   to_date.isoformat(),
            'rows': fy_rows,
            'count': len(fy_rows),
            'by_contact': _by_contact(fy_qs),
        },
        'summary': {
            'window_count':        len(window_rows),
            'fiscal_year_count':   len(fy_rows),
            'window_debit_total':  _d(sum(((l.debit_bwp  or ZERO) for l in window_qs), ZERO)),
            'window_credit_total': _d(sum(((l.credit_bwp or ZERO) for l in window_qs), ZERO)),
            'fy_debit_total':      _d(sum(((l.debit_bwp  or ZERO) for l in fy_qs), ZERO)),
            'fy_credit_total':     _d(sum(((l.credit_bwp or ZERO) for l in fy_qs), ZERO)),
        },
    }


# ---------------------------------------------------------------------------
# MA Balance Sheet — groups by Account.fs_line_item per the docx layout
# CFO directive 2026-05-20 (Manus TB-audit follow-up + post-PR6).
# ---------------------------------------------------------------------------

def build_ma_balance_sheet(as_of, company_id=None):
    """
    Balance sheet rendered against the MA section layout defined in
    reporting/ma_bs_spec.py. Lines aggregate by `Account.fs_line_item`
    rather than `account_type`. The fs_line_item labels are seeded by
    reporting/management/commands/seed_ma_classifications.

    Returns:
      {
        'as_of': ...,
        'sections': [
          {id, label, side, lines: [{label, amount}], subtotal_label, subtotal},
          ...
        ],
        'totals': {
          'total_assets', 'total_liabilities', 'total_equity',
          'liabilities_and_equity', 'balanced',
        },
      }

    Sign convention (matches the MA workbook display):
      Assets   → positive number = positive balance (net Dr)
      Liabilities → display as POSITIVE for natural credit balance
                    (i.e. amount we owe). Sign flipped relative to GL.
      Equity   → display as POSITIVE for credit balance (capital + reserves).
    """
    from collections import defaultdict
    from ledger.models import Account
    from reporting.ma_bs_spec import SECTIONS as _BS_SECTIONS

    # CFO directive 2026-06-09 (Legakwa BS reconfig — approved): for NON-ADIC
    # entities with a per-entity BS template, route to the entity_bs_engine and
    # return its output verbatim (same shape as this function). ADIC + ADIL stay
    # on the frozen ma_bs_spec.py path below. Mirrors the 2026-06-08 P&L router
    # pattern (commit d8b3ec7). Dispatches on company.code, NOT entity_type
    # (ADIL is miscoded as 'trading' but is life insurance, so MUST stay on the
    # frozen insurance BS until that seed is corrected).
    if company_id is not None:
        try:
            from core.models import Company as _Company
            _co = _Company.objects.filter(id=company_id).only('id', 'code').first()
            if _co and _co.code.upper() not in ('ADIC', 'ADIL'):
                from reporting.entity_bs_templates import get_template as _get_bs_tpl
                if _get_bs_tpl(_co.code) is not None:
                    from reporting.entity_bs_engine import build_entity_bs
                    return build_entity_bs(_co.code, as_of)
        except Exception:    # noqa: BLE001 — never let the router break ADIC's path
            pass

    base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__lte=as_of,
    )
    if company_id:
        base = base.filter(journal_entry__company_id=company_id)

    # Aggregate Dr/Cr by account_id
    agg = _agg_je_lines(base)

    # Roll up by fs_line_item
    by_label: dict[str, tuple[Decimal, Decimal]] = defaultdict(
        lambda: (ZERO, ZERO)
    )
    if not agg:
        # Empty — return structurally complete report with zeros
        out_sections = []
        for sec in _BS_SECTIONS:
            out_sections.append({
                'id': sec['id'],
                'label': sec['label'],
                'side': sec['side'],
                'lines': [
                    {'label': ln, 'amount': '0.00'} for ln in sec['lines']
                ],
                'subtotal_label': sec['subtotal_label'],
                'subtotal': '0.00',
            })
        return {
            'as_of': str(as_of),
            'sections': out_sections,
            'totals': {
                'total_assets': '0.00',
                'total_liabilities': '0.00',
                'total_equity': '0.00',
                'liabilities_and_equity': '0.00',
                'balanced': True,
            },
        }

    accounts = Account.objects.filter(id__in=agg.keys()).only(
        'id', 'fs_line_item', 'code', 'name', 'account_type',
    )
    # True per-account-type totals over EVERY BS account (mapped or not). Used
    # below to surface any balance the spec sections miss as an explicit
    # "Other (unclassified)" line, so the statement balances and nothing is
    # silently dropped. CFO forensic-pass fix 2026-06-04.
    type_net = {'asset': ZERO, 'liability': ZERO, 'equity': ZERO}
    for acct in accounts:
        dr, cr = agg.get(acct.id, (ZERO, ZERO))
        at = acct.account_type
        if at == 'asset':
            type_net['asset'] += (dr - cr)
        elif at == 'liability':
            type_net['liability'] += (cr - dr)
        elif at == 'equity':
            type_net['equity'] += (cr - dr)
        label = (acct.fs_line_item or '').strip()
        if not label:
            continue
        bdr, bcr = by_label[label]
        by_label[label] = (bdr + dr, bcr + cr)

    # Labels that appear in MORE THAN ONE side are dual-mapped (e.g.
    # 'Claims Payable - All Risk' lives in both Current Assets and Current
    # Liabilities). For those, net Dr lands on Assets only and net Cr lands
    # on Liabilities only — never both. The "other" side renders as 0 so we
    # don't double-count the balance as a negative contra.
    _label_sides: dict[str, set[str]] = {}
    for _sec in _BS_SECTIONS:
        for _ln in _sec['lines']:
            _label_sides.setdefault(_ln, set()).add(_sec['side'])
    _dual_mapped = {_l for _l, _s in _label_sides.items() if len(_s) > 1}

    # Per-line amount: sign convention by section side
    def line_amount(label: str, side: str) -> Decimal:
        dr, cr = by_label.get(label, (ZERO, ZERO))
        if label in _dual_mapped:
            # Net Dr lands on Asset side only; net Cr lands on Liab side only.
            net_dr = dr - cr
            if side == 'asset':
                return net_dr if net_dr > ZERO else ZERO
            return (-net_dr) if net_dr < ZERO else ZERO
        if side == 'asset':
            return dr - cr            # net Dr positive on Assets
        return cr - dr                # net Cr positive on Liab / Equity

    # Build output
    out_sections = []
    total_assets = ZERO
    total_liab   = ZERO
    total_equity = ZERO

    for sec in _BS_SECTIONS:
        side = sec['side']
        lines_out = []
        subtotal = ZERO
        for ln in sec['lines']:
            amt = line_amount(ln, side)
            # Dual-mapped labels (e.g. 'Claims Payable - All Risk') carry their
            # net balance on ONE side; the other side computes to 0. Suppress
            # that phantom 0.00 line so the same label doesn't render in two
            # BS sections at once (bug fcdf7078 — Oprah). Totals are unchanged
            # (a 0 contributes nothing to the subtotal).
            if ln in _dual_mapped and amt == ZERO:
                continue
            lines_out.append({'label': ln, 'amount': _d(amt)})
            subtotal += amt
        out_sections.append({
            'id': sec['id'],
            'label': sec['label'],
            'side': side,
            'lines': lines_out,
            'subtotal_label': sec['subtotal_label'],
            'subtotal': _d(subtotal),
        })
        if side == 'asset':
            total_assets += subtotal
        elif side == 'liability':
            total_liab += subtotal
        else:
            total_equity += subtotal

    # Add current-year P&L into Equity ("Profit for the Year") so the
    # equation balances and matches the P&L report.
    # Use MA canonical PAT (same source as /reports/profit-loss) so both
    # reports agree. Raw GL sum is the fallback when MA is unavailable.
    fy_start = _get_fiscal_year_start(as_of, _fy_end_month_for(company_id))
    pl_base = _posted_lines(company_id=company_id).filter(
        journal_entry__entry_date__gte=fy_start,
        journal_entry__entry_date__lte=as_of,
    )
    if company_id:
        pl_base = pl_base.filter(journal_entry__company_id=company_id)
    pl_agg = _agg_je_lines(pl_base)
    pl_accounts = Account.objects.filter(id__in=pl_agg.keys()).only(
        'id', 'account_type',
    )
    pl_net_raw = ZERO
    for acct in pl_accounts:
        dr, cr = pl_agg.get(acct.id, (ZERO, ZERO))
        if acct.account_type == 'revenue':
            pl_net_raw += cr - dr
        elif acct.account_type == 'expense':
            pl_net_raw -= dr - cr

    # Prefer MA canonical PAT so P&L and BS "Profit for the Year" match.
    # Any gap between MA PAT and raw GL is surfaced via the equity plug below.
    pl_net = pl_net_raw
    try:
        from reporting.ma_pl import build_ma_pl
        ma_result = build_ma_pl(fy_start, as_of, company_id=company_id)
        ma_pat = ma_result.get('totals', {}).get('pat')
        if ma_pat is not None:
            pl_net = Decimal(str(ma_pat))
    except Exception:  # noqa: BLE001
        pass  # fall back to raw GL

    # Insert "Profit for the Year" as the second line of Equity
    for sec in out_sections:
        if sec['id'] == 'equity':
            sec['lines'].insert(1, {
                'label': 'Profit for the Year',
                'amount': _d(pl_net),
            })
            new_sub = Decimal(sec['subtotal']) + pl_net
            sec['subtotal'] = _d(new_sub)
            total_equity += pl_net
            break

    # ---- Catch-all: surface any BS balance the spec sections do not capture as
    # an explicit "Other (unclassified)" line, so A = L + E always holds and the
    # residual is VISIBLE for Finance to reclassify (never silently dropped).
    # The full TB balances, so true_assets = true_liab + true_equity + PL; adding
    # each side's residual therefore guarantees the statement balances.
    # CFO forensic-pass fix 2026-06-04.
    def _add_other(section_id, label, amount):
        for sec in out_sections:
            if sec['id'] == section_id:
                sec['lines'].append({'label': label, 'amount': _d(amount)})
                sec['subtotal'] = _d(Decimal(sec['subtotal']) + amount)
                return
    resid_asset = type_net['asset'] - total_assets
    if abs(resid_asset) >= Decimal('0.01'):
        _add_other('current_assets', 'Other Assets (unclassified)', resid_asset)
        total_assets += resid_asset
    resid_liab = type_net['liability'] - total_liab
    if abs(resid_liab) >= Decimal('0.01'):
        _add_other('current_liabilities', 'Other Liabilities (unclassified)', resid_liab)
        total_liab += resid_liab
    # Equity plug: A - L - (mapped equity + current-year PL) = the accumulated
    # PRIOR-period retained earnings not closed to an equity line. The legacy BS
    # carries this as prior_year_pl; the MA layout had no line for it, which was
    # the root of the imbalance. Surface it explicitly so A = L + E holds.
    resid_equity = total_assets - total_liab - total_equity
    if abs(resid_equity) >= Decimal('0.01'):
        _add_other('equity', 'Retained Earnings / Other Equity (prior periods)', resid_equity)
        total_equity += resid_equity

    le = total_liab + total_equity
    return {
        'as_of':         str(as_of),
        'currency_code': _company_currency(company_id),
        'sections':      out_sections,
        'totals': {
            'total_assets':           _d(total_assets),
            'total_liabilities':      _d(total_liab),
            'total_equity':           _d(total_equity),
            'liabilities_and_equity': _d(le),
            'balanced':               abs(total_assets - le) < Decimal('1.00'),
        },
    }


# ---------------------------------------------------------------------------
# Cash Flow Statement — indirect method (CFO directive 2026-05-20)
# ---------------------------------------------------------------------------

def build_cash_flow(from_date, to_date, company_id=None):
    """
    Indirect-method cash flow statement for [from_date, to_date].

    Operating Activities:
      PAT (Profit After Tax for the period)
      + Depreciation                       (non-cash, add back)
      + Finance Cost                       (non-cash for reclass to financing)
      + Taxation                           (separated for clarity)
      ─────────────────
      Operating before working capital
      + Changes in working capital:
        Δ Trade Receivables           (decrease in receivables = source of cash)
        Δ Other Receivables
        Δ Reinsurance Provisions
        Δ Related Party Receivables
        Δ Subrogation Receivables
        Δ Salvage Receivables
        Δ Unearned Premium Reserve    (increase = source of cash)
        Δ Trade & Other Payables      (increase = source of cash)
        Δ Due to Reinsurers
        Δ Claims Payable - All Risk
        Δ IBNR - BS
        Δ Tax Payable
        Δ Severance & Leave liabilities
      Δ Tax paid                          (= -movement in Tax Payable + Taxation)
      ─────────────────
      Net Cash from Operating Activities

    Investing Activities:
      Δ Property, Plant & Equipment        (purchase = use of cash)
      Δ Right of Use - Asset

    Financing Activities:
      Δ Short-term Loan                   (drawdown = source of cash)
      Δ Long-term Loan
      Δ Lease Liabilities
      Δ Current Lease Liability
      Δ Stated Capital                     (issuance = source of cash)

    + Opening Cash + Net Change = Closing Cash.
    Reconciliation: Closing - Opening compared to the working-capital
    walk gives an integrity check.
    """
    from collections import defaultdict
    from ledger.models import Account

    # Helper: aggregate by fs_line_item over a date window
    def _agg_by_label(date_from=None, date_to=None):
        qs = _posted_lines(company_id=company_id)
        if date_from is not None:
            qs = qs.filter(journal_entry__entry_date__gte=date_from)
        if date_to is not None:
            qs = qs.filter(journal_entry__entry_date__lte=date_to)
        if company_id:
            qs = qs.filter(journal_entry__company_id=company_id)
        agg = _agg_je_lines(qs)
        if not agg:
            return {}
        # Bucket Dr/Cr by fs_line_item
        accounts = Account.objects.filter(id__in=agg.keys()).only(
            'id', 'fs_line_item',
        )
        out: dict[str, tuple[Decimal, Decimal]] = defaultdict(
            lambda: (ZERO, ZERO)
        )
        for acct in accounts:
            label = (acct.fs_line_item or '').strip()
            if not label:
                continue
            dr, cr = agg.get(acct.id, (ZERO, ZERO))
            bdr, bcr = out[label]
            out[label] = (bdr + dr, bcr + cr)
        return out

    # Asset-side balance: Dr - Cr (positive when asset)
    # Liability/Equity balance: Cr - Dr
    def _bal(by_label, label, side='asset'):
        dr, cr = by_label.get(label, (ZERO, ZERO))
        return (dr - cr) if side == 'asset' else (cr - dr)

    opening = _agg_by_label(date_to=from_date - __import__('datetime').timedelta(days=1))
    closing = _agg_by_label(date_to=to_date)

    # Get the PAT from the MA P&L for the same window
    from reporting.ma_pl import build_ma_pl
    pl = build_ma_pl(from_date, to_date, company_id=company_id)
    t = pl.get('totals', {})

    def _to_dec(s):
        if s is None or s == '':
            return ZERO
        return Decimal(str(s))

    pat            = _to_dec(t.get('pat'))
    depreciation   = abs(_to_dec(t.get('depreciation')))
    finance_cost   = abs(_to_dec(t.get('finance_cost')))
    taxation       = abs(_to_dec(t.get('taxation')))

    # Working capital deltas. Increase in an ASSET = use of cash (subtract).
    # Increase in a LIABILITY = source of cash (add).
    wc_assets = [
        'Trade Receivables', 'Other Receivables', 'Reinsurance Provisions',
        'Related Party Receivables', 'Subrogation Receivables',
        'Salvage Receivables',
    ]
    wc_liabilities = [
        'Unearned Premium Reserve', 'Trade & Other Payables',
        'Due to Reinsurers', 'Claims Payable - All Risk', 'IBNR - BS',
        'Severance & Leave liabilities',
    ]

    wc_changes: list[dict] = []
    wc_net = ZERO
    for label in wc_assets:
        delta = _bal(closing, label, 'asset') - _bal(opening, label, 'asset')
        impact = -delta   # ΔAsset positive → cash out
        wc_net += impact
        wc_changes.append({'label': f'Δ {label}', 'amount': _d(impact)})
    for label in wc_liabilities:
        delta = _bal(closing, label, 'liability') - _bal(opening, label, 'liability')
        impact = delta    # ΔLiab positive → cash in
        wc_net += impact
        wc_changes.append({'label': f'Δ {label}', 'amount': _d(impact)})

    operating_before_wc = pat + depreciation + finance_cost + taxation
    net_operating = operating_before_wc + wc_net - taxation

    # Investing — PPE + ROU
    investing_lines = []
    invest_net = ZERO
    for label in ['Property, Plant & Equipment',
                  'Property, Plant & Equipment - Accumulated Depreciation',
                  'Right of Use - Asset']:
        delta = _bal(closing, label, 'asset') - _bal(opening, label, 'asset')
        impact = -delta
        invest_net += impact
        investing_lines.append({'label': f'Δ {label}', 'amount': _d(impact)})

    # Financing — loans + leases + capital
    financing_lines = []
    finance_net = ZERO
    for label in ['Short-term Loan', 'Long-term Loan', 'Lease Liabilities',
                  'Current Lease Liability',
                  'Stated Capital (Issued Share Capital)',
                  'Retained Earnings']:
        delta = _bal(closing, label, 'liability') - _bal(opening, label, 'liability')
        impact = delta
        finance_net += impact
        financing_lines.append({'label': f'Δ {label}', 'amount': _d(impact)})
    # Subtract finance cost cash payment
    financing_lines.append({
        'label': 'Finance cost paid', 'amount': _d(-finance_cost),
    })
    finance_net -= finance_cost

    # Cash positions — Bank and Cash Accounts label
    cash_open  = _bal(opening, 'Bank and Cash Accounts', 'asset')
    cash_close = _bal(closing, 'Bank and Cash Accounts', 'asset')
    net_change = net_operating + invest_net + finance_net

    return {
        'from_date':     str(from_date),
        'to_date':       str(to_date),
        'currency_code': _company_currency(company_id),
        'sections': [
            {
                'id': 'operating',
                'label': 'Cash flows from Operating Activities',
                'lines': [
                    {'label': 'Profit After Tax', 'amount': _d(pat)},
                    {'label': 'Depreciation', 'amount': _d(depreciation)},
                    {'label': 'Finance Cost', 'amount': _d(finance_cost)},
                    {'label': 'Taxation', 'amount': _d(taxation)},
                    {'label': 'Operating profit before working capital changes',
                     'amount': _d(operating_before_wc), 'subtotal': True},
                    *wc_changes,
                    {'label': 'Tax paid', 'amount': _d(-taxation)},
                ],
                'subtotal_label': 'Net Cash from Operating Activities',
                'subtotal': _d(net_operating),
            },
            {
                'id': 'investing',
                'label': 'Cash flows from Investing Activities',
                'lines': investing_lines,
                'subtotal_label': 'Net Cash from Investing Activities',
                'subtotal': _d(invest_net),
            },
            {
                'id': 'financing',
                'label': 'Cash flows from Financing Activities',
                'lines': financing_lines,
                'subtotal_label': 'Net Cash from Financing Activities',
                'subtotal': _d(finance_net),
            },
        ],
        'totals': {
            'net_change_in_cash':        _d(net_change),
            'opening_cash':              _d(cash_open),
            'closing_cash':              _d(cash_close),
            'closing_cash_from_bs':      _d(cash_close),
            'reconciliation_difference': _d((cash_close - cash_open) - net_change),
        },
    }
