"""Graphite Aware — MODE: Claims Registry (open + pending).

Safe-fields-only register of open/pending claims (CFO decision 2026-07-07):
claim number, policy number, status, dates, broker, paid-to-date and
outstanding reserve. NO claimant names, Omang, contact, medical or free-text
incident detail is selected — so nothing needs masking.

Open = status in Pending / Approved / Reopen / Open (i.e. not Closed/Rejected).
Money comes from claim_reserves_coverages: paid = non-voided payments;
outstanding = latest running balance per claim+coverage (never SUM all rows —
they are revision history). The list shows the largest open claims by
outstanding reserve (the exposure that matters); totals cover ALL open claims.
"""
from __future__ import annotations

from typing import Any, Dict

from ..engine import run_select

_OPEN = "('Pending','Approved','Reopen','Open')"
_LIST_CAP = 200


def _one(sql: str, key: str, default=0):
    _, rows = run_select(sql)
    return (rows[0].get(key) if rows else default) or default


def claims_registry_report(user=None) -> Dict[str, Any]:
    # status breakdown (all open)
    _, st = run_select(
        f"SELECT status, COUNT(*) AS n FROM claims WHERE status IN {_OPEN} "
        "GROUP BY status ORDER BY n DESC LIMIT 20")
    by_status = {r['status']: int(r['n']) for r in st}
    open_count = sum(by_status.values())

    total_paid = float(_one(
        "SELECT ROUND(SUM(CASE WHEN crc.is_payment_voided=0 "
        "THEN COALESCE(crc.payment_amt,0) ELSE 0 END),0) AS v "
        f"FROM claim_reserves_coverages crc JOIN claims cl ON crc.claim_id=cl.id "
        f"WHERE cl.status IN {_OPEN} LIMIT 1", 'v'))
    total_os = float(_one(
        "SELECT ROUND(SUM(latest.balance),0) AS v FROM ("
        "SELECT crc.claim_id, crc.balance FROM claim_reserves_coverages crc "
        "JOIN (SELECT claim_id,coverage_id,MAX(id) mid FROM claim_reserves_coverages "
        "GROUP BY claim_id,coverage_id) m ON m.mid=crc.id) latest "
        f"JOIN claims cl ON latest.claim_id=cl.id WHERE cl.status IN {_OPEN} LIMIT 1", 'v'))

    _, rows = run_select(
        "SELECT cl.claim_number, p.policyNumber, cl.status, cl.claim_sub_status, "
        "cl.reported_date, cl.created_at, a.name AS broker, "
        "COALESCE(pd.paid,0) AS paid, COALESCE(os.os,0) AS outstanding "
        "FROM claims cl "
        "LEFT JOIN policies p ON cl.policy_id=p.id "
        "LEFT JOIN agencies a ON p.agency_id=a.id "
        "LEFT JOIN (SELECT claim_id, SUM(CASE WHEN is_payment_voided=0 "
        "THEN COALESCE(payment_amt,0) ELSE 0 END) paid FROM claim_reserves_coverages "
        "GROUP BY claim_id) pd ON pd.claim_id=cl.id "
        "LEFT JOIN (SELECT latest.claim_id, SUM(latest.balance) os FROM ("
        "SELECT crc.claim_id, crc.balance FROM claim_reserves_coverages crc "
        "JOIN (SELECT claim_id,coverage_id,MAX(id) mid FROM claim_reserves_coverages "
        "GROUP BY claim_id,coverage_id) m ON m.mid=crc.id) latest "
        "GROUP BY latest.claim_id) os ON os.claim_id=cl.id "
        f"WHERE cl.status IN {_OPEN} ORDER BY outstanding DESC LIMIT {_LIST_CAP}")

    out = []
    for r in rows:
        status = r.get('status') or ''
        sub = (r.get('claim_sub_status') or '').strip()
        out.append({
            'claim': r.get('claim_number'),
            'policy': r.get('policyNumber'),
            'status': (f"{status} · {sub}" if sub else status),
            'reported': str(r.get('reported_date') or r.get('created_at') or '')[:10],
            'broker': r.get('broker') or 'Direct / unassigned',
            'paid': float(r.get('paid') or 0),
            'outstanding': float(r.get('outstanding') or 0),
        })

    from django.utils import timezone
    return {
        'generated_at': timezone.now().strftime('%d %b %Y, %H:%M'),
        'open_count': open_count,
        'by_status': by_status,
        'total_paid': round(total_paid),
        'total_outstanding': round(total_os),
        'shown': len(out),
        'rows': out,
        'notes': [
            f"Open + pending = {open_count} claims (Pending / Approved / Reopen / "
            "Open). Totals above cover all of them.",
            f"The table lists the {len(out)} largest by outstanding reserve — the "
            "biggest open exposure. Ask for a full export if you need every row.",
            "Safe fields only: claim & policy number, status, date, broker, paid "
            "and reserve. No claimant names, ID numbers, contacts or injury detail.",
        ],
    }
