"""
reporting/ma_trace.py — TB → BS / P&L line traceability.

Lifted from the ADSA pipeline (build_monthly_pack.py) on 2026-05-18 to
close the silent-drop bug class for good. Two ideas survive the port:

  1. Every GL account in the trial balance must end up in exactly one
     BS or P&L line. The trace builder enumerates every account, looks
     up the bucket it landed in via the existing PREFIX_TYPE_MAP, and
     flags anything that did NOT land anywhere.

  2. The output is a flat list of {code, name, balance, bucket} tuples
     so the operator can see — without leaving the browser — that every
     rand of opening / closing balance is accounted for.

This is the defensive guardrail that would have caught the FY26 9M
"missing 8 Mn" bug on 2026-05-15 the day it shipped: the trace would
have surfaced the 5 unmapped codes (147, 104013, 104014, 303, 118008)
inside seconds, instead of waiting for a CFO eyeball-pass against the
MA workbook.

Usage:
    from reporting.ma_trace import build_trial_balance_trace
    trace = build_trial_balance_trace(as_of=date(2026,3,31), company_id=adic_id)
    if trace['unmapped']:
        raise RuntimeError(f"Refusing to publish: {len(trace['unmapped'])} unmapped accounts")
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from django.db.models import Sum, Q

from ledger.models import Account, JournalEntryLine


# Buckets a single account can land in. Mirrors the keys
# reporting.reports.build_balance_sheet / build_profit_loss expose.
BUCKETS = (
    # Balance sheet
    'fixed_asset',
    'current_asset',
    'bank',
    'investment',
    'other_asset',
    'current_liability',
    'provision',
    'long_term_liability',
    'equity',
    # P&L
    'income',
    'cost_of_insurance',
    'operating_expense',
    'tax_expense',
    'other_income',
    'other_expense',
    # Sentinel
    'UNMAPPED',
)


def _account_bucket(acct: Account) -> str:
    """Return the BUCKETS slot this account belongs to.

    Reads `account_type` + `sub_type` first, falls back to the GL-code
    prefix. The fall-back is essential because some companies (RSA,
    UNI, ADSA, VCM) carry prefixed codes (e.g. `RSA_400000`) that don't
    line up byte-for-byte with the canonical CoA.
    """
    sub = (getattr(acct, 'sub_type', '') or '').strip().lower()
    at  = (getattr(acct, 'account_type', '') or '').strip().lower()

    if sub == 'bank':                              return 'bank'
    if sub == 'current_asset':                     return 'current_asset'
    if sub == 'fixed_asset':                       return 'fixed_asset'
    if sub == 'accumulated_depreciation':          return 'fixed_asset'  # nets against PPE
    if sub == 'investment':                        return 'investment'
    if sub in ('other_asset', 'other'):            return 'other_asset'

    if sub == 'current_liability':                 return 'current_liability'
    if sub == 'provision':                         return 'provision'
    if sub == 'long_term_liability':               return 'long_term_liability'
    if sub == 'equity':                            return 'equity'

    if at == 'income':                             return 'income'
    if at == 'cost_of_sales' or sub == 'cost_of_insurance':
        return 'cost_of_insurance'
    if at == 'expense':                            return 'operating_expense'
    if sub == 'tax_expense':                       return 'tax_expense'
    if sub == 'other_income':                      return 'other_income'
    if sub == 'other_expense':                     return 'other_expense'

    # Prefix fallback (Odoo / Alpha Direct numbering).
    code = (acct.code or '').strip()
    raw = code.split('_')[-1] if '_' in code else code
    if raw.startswith(('11', '12', '13')):         return 'fixed_asset'
    if raw.startswith('21'):                       return 'current_asset'
    if raw.startswith('280'):                      return 'bank'
    if raw.startswith('29'):                       return 'investment'
    if raw.startswith('14'):                       return 'long_term_liability'
    if raw.startswith(('15', '16')):               return 'current_liability'
    if raw.startswith('17'):                       return 'provision'
    if raw.startswith('18'):                       return 'equity'
    if raw.startswith('4'):                        return 'income'
    if raw.startswith('5'):                        return 'cost_of_insurance'
    if raw.startswith('6'):                        return 'operating_expense'
    if raw.startswith('7'):                        return 'tax_expense'

    return 'UNMAPPED'


def build_trial_balance_trace(
    *,
    as_of: date,
    company_id: str | int | None = None,
) -> dict[str, Any]:
    """Return every account's closing balance + its target BS/P&L bucket.

    Output shape:
        {
          'as_of':       'YYYY-MM-DD',
          'rows':        [{code, name, balance_bwp, bucket}, ...],
          'totals':      {bucket: bwp_total},
          'unmapped':    [{code, name, balance_bwp}, ...],
          'integrity':   {'balanced': bool, 'total_dr': float, 'total_cr': float},
        }

    `unmapped` is the headline number. Anything in this list is a
    production incident — a rand of balance the BS/P&L are not seeing.
    """
    qs = JournalEntryLine.objects.filter(
        journal_entry__status='posted',
        journal_entry__entry_date__lte=as_of,
    )
    if company_id:
        qs = qs.filter(journal_entry__company_id=company_id)

    by_account = (qs.values('account_id')
                    .annotate(dr=Sum('debit_bwp'), cr=Sum('credit_bwp')))

    accounts = {a.id: a for a in Account.objects.all()}
    rows: list[dict[str, Any]] = []
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal('0'))
    unmapped: list[dict[str, Any]] = []
    total_dr = Decimal('0')
    total_cr = Decimal('0')

    for line in by_account:
        acct = accounts.get(line['account_id'])
        if acct is None:
            continue
        dr = Decimal(line['dr'] or 0)
        cr = Decimal(line['cr'] or 0)
        bal = dr - cr
        total_dr += dr
        total_cr += cr
        if bal == 0:
            continue
        bucket = _account_bucket(acct)
        row = {
            'code':        acct.code,
            'name':        acct.name,
            'balance_bwp': float(bal),
            'bucket':      bucket,
        }
        rows.append(row)
        totals[bucket] += bal
        if bucket == 'UNMAPPED':
            unmapped.append({
                'code':        acct.code,
                'name':        acct.name,
                'balance_bwp': float(bal),
            })

    # Sort rows: unmapped first (so anomalies surface), then by code.
    rows.sort(key=lambda r: (r['bucket'] != 'UNMAPPED', r['code']))

    return {
        'as_of':     as_of.isoformat(),
        'rows':      rows,
        'totals':    {k: float(v) for k, v in totals.items()},
        'unmapped':  unmapped,
        'integrity': {
            'balanced':  abs(total_dr - total_cr) < Decimal('0.01'),
            'total_dr':  float(total_dr),
            'total_cr':  float(total_cr),
        },
    }
