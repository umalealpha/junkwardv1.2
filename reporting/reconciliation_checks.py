"""
reporting/reconciliation_checks.py — every "two builders, one number"
test for ADIC. Each function returns a list of dicts; the management
command upserts them into Reconciliation rows.

Each check returns:
  {
    period_label, period_start, period_end,
    metric,
    source_a_name, source_a_value,
    source_b_name, source_b_value,
  }

Pure Python. Read-only. No DB writes. No I/O outside the existing
builders. Add new checks by appending a function to CHECKS at the
bottom; the runner enumerates that list.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Callable, Iterable

from django.db.models import Sum

from ledger.models import JournalEntry, JournalEntryLine
from reporting.ma_pl import build_ma_pl
from reporting.reports import (
    build_balance_sheet,
    build_cash_position,
    build_coa_ma_tree,
    build_ma_balance_sheet,
)


# ADIC fiscal periods to reconcile every run. New ones can be appended.
PERIODS: list[tuple[str, date, date]] = [
    ('FY25',     date(2024, 7, 1), date(2025, 6, 30)),
    ('FY26_9M',  date(2025, 7, 1), date(2026, 3, 31)),
]


def _jel_sum(filters: dict, start: date, end: date, company_id: str) -> Decimal:
    qs = JournalEntryLine.objects.filter(
        journal_entry__company_id=company_id,
        journal_entry__status='posted',
        journal_entry__entry_date__gte=start,
        journal_entry__entry_date__lte=end,
        **filters,
    )
    a = qs.aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp'))
    return (a['d'] or Decimal('0')) - (a['c'] or Decimal('0'))


# ─── individual checks ───────────────────────────────────────────────
def check_nep_pl_vs_tree(company_id: str) -> list[dict]:
    out = []
    for label, start, end in PERIODS:
        pl   = build_ma_pl(start, end, company_id=company_id)
        tree = build_coa_ma_tree(company_id=company_id, as_of=end, from_date=start)
        pl_v   = Decimal(pl['totals']['net_earned_premium'])
        tree_v = next(
            Decimal(s['subtotal_bwp']) for s in tree['pl']
            if s['id'] == 'net_earned_premium_block'
        )
        out.append({
            'period_label': label, 'period_start': start, 'period_end': end,
            'metric':       'NEP',
            'source_a_name': 'MA P&L',          'source_a_value': pl_v,
            'source_b_name': 'CoA-MA-tree',     'source_b_value': tree_v,
        })
    return out


def check_gwp_pl_vs_raw_100xxx(company_id: str) -> list[dict]:
    out = []
    for label, start, end in PERIODS:
        pl  = build_ma_pl(start, end, company_id=company_id)
        pl_v  = Decimal(pl['totals']['gross_written_premium'])
        raw_v = -_jel_sum({'account__code__startswith': '100'}, start, end, company_id)
        out.append({
            'period_label': label, 'period_start': start, 'period_end': end,
            'metric':       'GWP (all 100xxx)',
            'source_a_name': 'MA P&L (spec)',   'source_a_value': pl_v,
            'source_b_name': 'JE raw 100xxx',   'source_b_value': raw_v,
        })
    return out


def check_pat_pl_vs_tree(company_id: str) -> list[dict]:
    out = []
    for label, start, end in PERIODS:
        pl   = build_ma_pl(start, end, company_id=company_id)
        tree = build_coa_ma_tree(company_id=company_id, as_of=end, from_date=start)
        pl_v   = Decimal(pl['totals']['pat'])
        tree_v = sum((Decimal(s['subtotal_bwp']) for s in tree['pl']), Decimal('0'))
        out.append({
            'period_label': label, 'period_start': start, 'period_end': end,
            'metric':       'PAT',
            'source_a_name': 'MA P&L',                  'source_a_value': pl_v,
            'source_b_name': 'CoA-MA-tree (sum sect)',  'source_b_value': tree_v,
        })
    return out


def check_cash_vs_bs_line(company_id: str) -> list[dict]:
    out = []
    for label, start, end in PERIODS:
        cp = build_cash_position(company_id=company_id, as_of=end)
        bs = build_ma_balance_sheet(end, company_id=company_id)
        cp_v = Decimal(cp['total_bwp'])
        bs_v = Decimal('0')
        for sec in bs.get('sections', []):
            if sec.get('id') == 'current_assets':
                for ln in sec.get('lines', []):
                    if ln.get('label') == 'Bank and Cash Accounts':
                        bs_v = Decimal(ln.get('amount', '0'))
        out.append({
            'period_label': label, 'period_start': start, 'period_end': end,
            'metric':       'Cash',
            'source_a_name': 'cash-position',           'source_a_value': cp_v,
            'source_b_name': 'MA BS "Bank and Cash"',   'source_b_value': bs_v,
        })
    return out


def check_total_assets_bs_vs_tree(company_id: str) -> list[dict]:
    out = []
    for label, start, end in PERIODS:
        bs   = build_ma_balance_sheet(end, company_id=company_id)
        tree = build_coa_ma_tree(company_id=company_id, as_of=end, from_date=start)
        bs_v   = Decimal(bs.get('totals', {}).get('total_assets', '0'))
        tree_v = sum(
            (Decimal(s['subtotal_bwp']) for s in tree['bs'] if s['side'] == 'asset'),
            Decimal('0'),
        )
        out.append({
            'period_label': label, 'period_start': start, 'period_end': end,
            'metric':       'Total Assets',
            'source_a_name': 'MA BS',                   'source_a_value': bs_v,
            'source_b_name': 'CoA-MA-tree (asset sum)', 'source_b_value': tree_v,
        })
    return out


def check_total_assets_legacy_vs_ma(company_id: str) -> list[dict]:
    out = []
    for label, start, end in PERIODS:
        legacy = build_balance_sheet(end, company_id=company_id)
        ma     = build_ma_balance_sheet(end, company_id=company_id)
        leg_v = Decimal(legacy.get('totals', {}).get('total_assets', '0'))
        ma_v  = Decimal(ma.get('totals', {}).get('total_assets', '0'))
        out.append({
            'period_label': label, 'period_start': start, 'period_end': end,
            'metric':       'Total Assets',
            'source_a_name': 'Legacy BS',  'source_a_value': leg_v,
            'source_b_name': 'MA BS',      'source_b_value': ma_v,
        })
    return out


def check_bs_balances(company_id: str) -> list[dict]:
    """MA BS Total Assets vs Liabilities+Equity. Hard contract."""
    out = []
    for label, start, end in PERIODS:
        ma = build_ma_balance_sheet(end, company_id=company_id)
        ta = Decimal(ma.get('totals', {}).get('total_assets', '0'))
        le = Decimal(ma.get('totals', {}).get('liabilities_and_equity', '0'))
        out.append({
            'period_label': label, 'period_start': start, 'period_end': end,
            'metric':       'BS balances (Assets = L+E)',
            'source_a_name': 'MA BS Total Assets',         'source_a_value': ta,
            'source_b_name': 'MA BS Liabilities+Equity',   'source_b_value': le,
        })
    return out


# ─── registry ────────────────────────────────────────────────────────
CHECKS: list[Callable[[str], list[dict]]] = [
    check_nep_pl_vs_tree,
    check_gwp_pl_vs_raw_100xxx,
    check_pat_pl_vs_tree,
    check_cash_vs_bs_line,
    check_total_assets_bs_vs_tree,
    check_total_assets_legacy_vs_ma,
    check_bs_balances,
]


def run_all(company_id: str) -> Iterable[dict]:
    """Yield every check result. Caller decides what to do with each row."""
    for fn in CHECKS:
        for row in fn(company_id):
            yield row
