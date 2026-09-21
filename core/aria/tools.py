"""
core/aria/tools.py — grounded tool helpers for ARIA Phase 1.

Each function returns a small dict the prompt can reference verbatim.
Pure read-only queries, scoped to the caller's company selection.

CFO directive 2026-05-22: ARIA must ground every numeric reply in omni
data — never in DeepSeek's training set. These tools are what give it
the right of way to interrupt with proactive alerts.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from django.utils import timezone


ZERO = Decimal('0.00')


def get_tb_status(today: date | None = None, company_id: Optional[str] = None) -> dict:
    """TB balanced/unbalanced + opening + period movement."""
    today = today or timezone.localdate()
    try:
        from reporting.reports import build_trial_balance
        r = build_trial_balance(today, company_id=company_id)
        return {
            'as_of':        r.get('as_of') or str(today),
            'rows':         len(r.get('rows', [])),
            'total_debits': str(r.get('totals', {}).get('total_debits', '0')),
            'total_credits':str(r.get('totals', {}).get('total_credits', '0')),
            'balanced':     bool(r.get('totals', {}).get('balanced', False)),
        }
    except Exception as exc:    # noqa: BLE001
        return {'error': str(exc)[:200]}


def get_pl_summary(from_date: date, to_date: date, company_id: Optional[str] = None) -> dict:
    """MA P&L totals — GWP, NEP, Gross Profit, EBITDA, PBT, PAT."""
    try:
        from reporting.ma_pl import build_ma_pl
        r = build_ma_pl(from_date, to_date, company_id=company_id)
        t = r.get('totals', {})
        return {
            'from':   from_date.isoformat(),
            'to':     to_date.isoformat(),
            'gwp':    str(t.get('gross_written_premium', '0')),
            'nep':    str(t.get('net_earned_premium', '0')),
            'gp':     str(t.get('gross_profit', '0')),
            'ebitda': str(t.get('ebitda', '0')),
            'pbt':    str(t.get('pbt', '0')),
            'pat':    str(t.get('pat', '0')),
            'gross_loss_ratio': str(t.get('gross_loss_ratio', '0')),
            'net_loss_ratio':   str(t.get('net_loss_ratio',   '0')),
        }
    except Exception as exc:    # noqa: BLE001
        return {'error': str(exc)[:200]}


def get_pending_approvals(user) -> dict:
    """JEs + POs awaiting the caller's approval."""
    try:
        from ledger.models import JournalEntry
        from procurement.models import PurchaseOrder
        je_count = JournalEntry.objects.filter(
            status__in=['draft', 'pending_approval'],
        ).count()
        po_pending_fm  = PurchaseOrder.objects.filter(status='pending_fm_approval').count()
        po_pending_cfo = PurchaseOrder.objects.filter(status='pending_cfo_approval').count()
        return {
            'je_drafts_or_pending': je_count,
            'po_pending_fm':        po_pending_fm,
            'po_pending_cfo':       po_pending_cfo,
            'total':                je_count + po_pending_fm + po_pending_cfo,
        }
    except Exception as exc:    # noqa: BLE001
        return {'error': str(exc)[:200]}


def get_anomaly_count(today: date | None = None) -> dict:
    """Count open Exceptions raised by the integrity scan."""
    today = today or timezone.localdate()
    try:
        from exceptions.models import Exception as ExceptionRow
        n_open = ExceptionRow.objects.filter(status__in=['open', 'investigating']).count()
        return {'open_exceptions': n_open}
    except Exception as exc:    # noqa: BLE001
        return {'open_exceptions': 0, 'error': str(exc)[:200]}


def route_hint(path: str) -> str:
    """Map current page → one-line hint ARIA can offer in the system prompt."""
    if not path:
        return ''
    p = path.rstrip('/').lower()
    hints = {
        '/dashboard':                   'cash + AR + AP tiles + MTD net profit',
        '/cfo':                         'pending approvals, related-party, asset sign-offs',
        '/reports/profit-loss':         'MA P&L — Net Earned Premium → PBT → PAT',
        '/reports/balance-sheet':       'BS by section, balanced flag',
        '/reports/trial-balance':       'TB, period-based',
        '/reports/tax-reconciliation':  'IAS 12 — ETR analysis, current vs deferred',
        '/reports/consolidated':        'multi-entity consolidation worksheet',
        '/reports/audit-pack':          'auditor bundle — TB+PL+BS+JEs',
        '/reports/expense-analysis':    'expenses by category, period-over-period',
        '/reports/vat-return':          'VAT 200 prep — output, input, net',
        '/reports/asset-register':      'fixed asset register',
        '/journal-entries':             'JE list — search by entry_number',
        '/purchase-orders':             'POs by status — bulk CSV + PDF zip on the toolbar',
        '/assets':                      'fixed assets — depreciation + disposals',
        '/banking':                     'bank reconciliation',
        '/hris':                        'HRIS — restricted to whitelist + HR team',
        '/hris/payroll':                'payroll — baseline + amendments → next period',
        '/hris/inbox':                  'manager command-center — pending leave + kudos + onboarding',
        '/hris/leave':                  'leave applications + manager approval queue',
        '/hris/rewards':                'kudos feed + send-kudos',
        '/hris/assess':                 'PMS 1-4 scale + 9-box calibration',
        '/contacts':                    'customers + vendors',
        '/invoices':                    'AR invoices',
        '/bills':                       'AP bills',
        '/payments':                    'payments + receipts',
        '/petty-cash':                  'petty cash vouchers',
        '/investments':                 'investment portfolio',
        '/reinsurance/treaties':        'reinsurance treaties',
    }
    # prefix match — first hit wins
    for prefix, hint in hints.items():
        if p == prefix or p.startswith(prefix + '/'):
            return hint
    return ''
