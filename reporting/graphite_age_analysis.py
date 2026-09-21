"""
reporting/graphite_age_analysis.py

Report builder for the Graphite V2 premium-debtors age analysis, pulled
read-only from the Graphite BW replica (see integrations/graphite_age.py).

Shapes the pre-aggregated aging rows into the same summary+rows structure the
omni AR Aging report uses, so the frontend page matches the AR Aging look.
omni-only — no Graphite changes.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional

TWO = Decimal('0.01')
ZERO = Decimal('0')


def _d(v) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:  # noqa: BLE001 — a bad cell must not kill the report
        return ZERO


def _f(v) -> float:
    return float(_d(v).quantize(TWO, rounding=ROUND_HALF_UP))


def build_graphite_age_analysis(
    *,
    search: str = '',
    status: str = '',
    limit: int = 20000,
    _rows: Optional[list] = None,
) -> Dict[str, Any]:
    """Build the age-analysis report. `_rows` is injectable for tests."""
    if _rows is None:
        from integrations.graphite_age import fetch_age_analysis
        raw = fetch_age_analysis(search=search, status=status, limit=limit)
    else:
        raw = _rows

    rows = []
    tot_invoice = tot_paid = tot_balance = ZERO
    # The four buckets ARE the source columns (they sum to balance_outstanding);
    # there is no separate "current" — aging starts at the 0-30 column.
    b30 = b60 = b90 = b120 = ZERO

    for r in raw:
        bal = _d(r.get('balance_outstanding'))
        d30 = _d(r.get('days_30'))
        d60 = _d(r.get('days_60'))
        d90 = _d(r.get('days_90'))
        d120 = _d(r.get('days_120_plus'))

        tot_invoice += _d(r.get('invoice_total'))
        tot_paid    += _d(r.get('payment_total'))
        tot_balance += bal
        b30 += d30; b60 += d60; b90 += d90; b120 += d120

        rows.append({
            'policy_number':      r.get('policy_number') or '',
            'client_name':        r.get('client_name') or '',
            'agent_name':         r.get('agent_name') or '',
            'product_name':       r.get('product_name') or '',
            'policy_status':      r.get('policy_status') or '',
            'frequency':          r.get('frequency') or '',
            'invoice_total':      _f(r.get('invoice_total')),
            'payment_total':      _f(r.get('payment_total')),
            'refund_total':       _f(r.get('refund_total')),
            'balance_outstanding': _f(bal),
            'days_30':            _f(d30),
            'days_60':            _f(d60),
            'days_90':            _f(d90),
            'days_120_plus':      _f(d120),
            'policy_created_at':  (str(r.get('policy_created_at'))
                                   if r.get('policy_created_at') else None),
        })

    return {
        'report': 'graphite_age_analysis',
        'source': 'Graphite V2 (read-only replica) — premium debtors aging',
        'filters': {'search': search or None, 'status': status or None},
        'summary': {
            'policy_count':  len(rows),
            'total_invoice': _f(tot_invoice),
            'total_paid':    _f(tot_paid),
            'total_balance': _f(tot_balance),
            # Buckets are the source aging columns (sum to total_balance).
            'buckets': {
                '0-30':     _f(b30),
                '31-60':    _f(b60),
                '61-90':    _f(b90),
                '120+':     _f(b120),
            },
        },
        'rows': rows,
    }
