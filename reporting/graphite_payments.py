"""
reporting/graphite_payments.py

Report builder for the Graphite V2 payment-transaction feed mirrored into omni
(integrations.GraphitePaymentTransaction). This is the omni-side, human-facing
view of the same data the Graphite Reporting Portal serves at
reporting.alphadirect.co.bw/reports/transactions — kept in sync by the
`pull_graphite_payments` management command.

Read-only. No GL involvement. Returns a dict in the shape the reporting views
expect (rows + summary + meta), so it slots into reporting/views.py like the
other builders.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Count, Sum

from integrations.models import GraphitePaymentSyncRun, GraphitePaymentTransaction


def _d(v) -> float:
    return float(v or 0)


def build_graphite_payments(
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    partner: str = '',
    status: str = '',
    policy_number: str = '',
    include_refunds: bool = True,
    limit: int = 1000,
    offset: int = 0,
) -> Dict[str, Any]:
    """Build the Graphite payments report from the local mirror.

    Filters mirror the Reporting Portal: date window (on source recorded_at),
    payment partner, status, policy number. `limit`/`offset` page the row list;
    the summary totals are computed over the FULL filtered set, not the page.
    """
    qs = GraphitePaymentTransaction.objects.all()

    if date_from:
        qs = qs.filter(source_recorded_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(source_recorded_at__date__lte=date_to)
    if partner:
        qs = qs.filter(payment_method=partner)
    if status:
        qs = qs.filter(status_norm=status.strip().lower())
    if policy_number:
        qs = qs.filter(policy_number=policy_number)
    if not include_refunds:
        qs = qs.filter(is_refund=False)

    total_count = qs.count()
    total_amount = qs.aggregate(s=Sum('amount'))['s'] or Decimal('0')

    # Summary by partner
    by_partner: List[Dict[str, Any]] = [
        {
            'partner': r['payment_method'] or '(blank)',
            'count':   r['n'],
            'amount':  _d(r['amt']),
        }
        for r in qs.values('payment_method')
                    .annotate(n=Count('id'), amt=Sum('amount'))
                    .order_by('-amt')
    ]

    # Summary by status
    by_status: List[Dict[str, Any]] = [
        {
            'status': r['status_norm'] or '(blank)',
            'count':  r['n'],
            'amount': _d(r['amt']),
        }
        for r in qs.values('status_norm')
                    .annotate(n=Count('id'), amt=Sum('amount'))
                    .order_by('-n')
    ]

    # Page of rows
    limit = max(1, min(int(limit), 5000))
    offset = max(0, int(offset))
    rows: List[Dict[str, Any]] = [
        {
            'graphite_id':       t.graphite_id,
            'policy_number':     t.policy_number,
            'reference_number':  t.reference_number,
            'amount':            _d(t.amount),
            'payment_method':    t.payment_method,
            'status':            t.status,
            'is_refund':         t.is_refund,
            'is_reverse':        t.is_reverse,
            'payment_frequency': t.payment_frequency,
            'paid_at':           t.paid_at.isoformat() if t.paid_at else None,
            'recorded_at':       (t.source_recorded_at.isoformat()
                                  if t.source_recorded_at else None),
            'product_name':      t.product_name,
            'plan_name':         t.plan_name,
            'customer_name':     t.customer_name,
            'agent_name':        t.agent_name,
            'dpo_trans_id':      t.dpo_trans_id,
            'dpo_company_ref':   t.dpo_company_ref,
        }
        for t in qs[offset:offset + limit]
    ]

    # Data freshness — last successful sweep
    last_run = (
        GraphitePaymentSyncRun.objects
        .filter(status__in=[GraphitePaymentSyncRun.Status.SUCCESS,
                            GraphitePaymentSyncRun.Status.PARTIAL])
        .order_by('-finished_at')
        .first()
    )

    return {
        'report': 'graphite_payments',
        'source': 'Graphite V2 Finance API (mirrored into omni)',
        'filters': {
            'date_from':     date_from.isoformat() if date_from else None,
            'date_to':       date_to.isoformat() if date_to else None,
            'partner':       partner or None,
            'status':        status or None,
            'policy_number': policy_number or None,
            'include_refunds': include_refunds,
        },
        'summary': {
            'total_count':  total_count,
            'total_amount': _d(total_amount),
            'by_partner':   by_partner,
            'by_status':    by_status,
        },
        'rows': rows,
        'meta': {
            'returned':   len(rows),
            'offset':     offset,
            'limit':      limit,
            'has_more':   offset + len(rows) < total_count,
            'freshness': {
                'last_synced_at': (last_run.finished_at.isoformat()
                                   if last_run and last_run.finished_at else None),
                'last_run_status': last_run.status if last_run else None,
                'last_window': (
                    f'{last_run.window_from}→{last_run.window_to}'
                    if last_run else None
                ),
            },
        },
    }
