"""Graphite Aware — MODE: Top 50 Dom.

The 50 largest ACTIVE domestic (DOMG) policies by annualised premium.
Read-only via the guarded run_select path. Individual policyholder names
come back masked to initials (the engine's PII mask); business entities
show in full. Exec-whitelist only.
"""
from __future__ import annotations

from typing import Any, Dict

from ..engine import mask_rows, run_select

_FREQ = {'1': 'Monthly', '3': 'Annual', '5': 'Quarterly',
         1: 'Monthly', 3: 'Annual', 5: 'Quarterly'}


def top_domestic_report(user=None) -> Dict[str, Any]:
    # Direct joins (the packaged GetPoliciesDetailsView takes ~14s; this is
    # ~15ms). Modes do NOT auto-mask, so we mask_rows() ourselves before any
    # name leaves: individual policyholder names -> initials, business names
    # (entities) kept in full.
    cols, rows = run_select(
        "SELECT p.policyNumber, "
        "TRIM(CONCAT_WS(' ',cu.firstName,cu.lastName)) AS customerName, "
        "p.business_name, a.name AS broker, p.annual_premium, p.premium_freq, "
        "p.term_start_date "
        "FROM policies p LEFT JOIN customer cu ON p.customer_id=cu.id "
        "LEFT JOIN agencies a ON p.agency_id=a.id "
        "WHERE p.status=1 AND p.policyNumber LIKE 'DOMG%' "
        "ORDER BY p.annual_premium DESC LIMIT 50")
    rows = mask_rows(cols, rows)

    out = []
    total = 0.0
    for i, r in enumerate(rows, 1):
        try:
            ap = float(r.get('annual_premium') or 0)
        except (TypeError, ValueError):
            ap = 0.0
        total += ap
        name = ((r.get('business_name') or '').strip()
                or (r.get('customerName') or '').strip() or '—')
        out.append({
            'rank': i,
            'policy': r.get('policyNumber'),
            'customer': name,
            'broker': r.get('broker') or 'Direct / unassigned',
            'annual_premium': ap,
            'freq': _FREQ.get(r.get('premium_freq'), str(r.get('premium_freq') or '')),
            'since': str(r.get('term_start_date') or '')[:10],
        })

    from django.utils import timezone
    return {
        'generated_at': timezone.now().strftime('%d %b %Y, %H:%M'),
        'rows': out,
        'count': len(out),
        'top50_gwp': round(total),
        'notes': [
            "The 50 largest active domestic (DOMG) policies by annualised premium.",
            "Individual policyholder names are masked to initials; business "
            "entities show in full. Read-only.",
            "Annualised premium puts monthly / quarterly / annual policies on "
            "the same yearly basis for ranking.",
        ],
    }
