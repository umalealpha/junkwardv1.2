"""Graphite Aware — MODE: Broker Analysis.

A fixed, reviewed report (NO LLM, NO free-text SQL) over the Graphite read
replica: the production book by intermediary (agency). Every figure is a
business-entity aggregate — agency names, premium sums, policy/claim counts.
No policyholder personal data is touched, so this mode is DPA-clean.

A broker is the intermediary recorded ON THE POLICY (`policies.agency_id`), not
the agency the selling user sits under (`policies.agent_id` -> `users.agency_id`).
The two disagree for 21,059 live policies, and whole brokers — Spectrum,
Botshabelo, Minet, Marsh, UTL — show a real book on the policy field and NOTHING
via the agent, their premium landing in the direct channel instead. Policies with
no `agency_id` are left unattributed rather than back-filled from the agent: they
are overwhelmingly Unicoin / Alpha Direct's own book (which this report excludes
anyway), and mixing two attribution rules would take a broker's premium from one
and its claims from the other.

Metrics (v1):
  * In-force book = SUM(annual_premium) of active policies. A clean book-size
    measure that avoids the re-rating duplicates in policy_actions.
  * New business = NEWBUSINESS / ISSUED actions in the trailing 12 months.
  * Claims = claim COUNT in the trailing 12 months (via the claim's policy).

Loss ratio is deliberately NOT computed here. Claim reserves are recorded as
revision rows (naive SUM double-counts) and claims are multi-period while
premium is annualised — a naive ratio misleads and would wrongly trip the
>70% CFO flag. The real incurred loss ratio (period-matched, recoveries
netted) is the next, separately-validated build.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..engine import run_select

# In-house channels — NOT external intermediaries. Matched case-insensitively
# as a substring of the agency name.
_DIRECT_MARKERS = ('alpha direct insurance co', 'unicoin')


def _is_direct(name: str) -> bool:
    n = (name or '').lower()
    return any(m in n for m in _DIRECT_MARKERS)


def _rows(sql: str) -> List[Dict[str, Any]]:
    _, rows = run_select(sql)
    return rows


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def broker_report(user=None) -> Dict[str, Any]:
    inforce = _rows(
        "SELECT a.name AS broker, "
        "COUNT(CASE WHEN p.status=1 THEN 1 END) AS active_pol, "
        "ROUND(SUM(CASE WHEN p.status=1 THEN p.annual_premium ELSE 0 END),0) AS inforce_gwp "
        "FROM policies p JOIN agencies a ON p.agency_id=a.id "
        "GROUP BY a.id,a.name HAVING inforce_gwp>0 ORDER BY inforce_gwp DESC LIMIT 100")
    nb = _rows(
        "SELECT a.name AS broker, COUNT(*) AS nb_cnt, ROUND(SUM(pa.annual_premium),0) AS nb_gwp "
        "FROM policy_actions pa JOIN policies p ON pa.policy_id=p.id "
        "JOIN agencies a ON p.agency_id=a.id "
        "WHERE pa.transaction_type='NEWBUSINESS' AND pa.status='ISSUED' "
        "AND pa.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) "
        "GROUP BY a.id,a.name LIMIT 100")
    # Net claims PAID in the trailing 12 months: sum of non-voided payments
    # net of salvage/subrogation recoveries. Payments are additive cash flows,
    # so this is reliable (unlike a loss ratio — see notes).
    paid = _rows(
        "SELECT a.name AS broker, ROUND(SUM(CASE WHEN crc.is_payment_voided=0 "
        "THEN COALESCE(crc.payment_amt,0) ELSE 0 END) "
        "- SUM(COALESCE(crc.salvage_payment,0)+COALESCE(crc.subrogation_payment,0)),0) AS net_paid "
        "FROM claim_reserves_coverages crc "
        "JOIN claim_reserves cr ON crc.reserve_id=cr.id "
        "JOIN claims cl ON crc.claim_id=cl.id "
        "JOIN policies p ON cl.policy_id=p.id JOIN agencies a ON p.agency_id=a.id "
        "WHERE cr.date >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) "
        "GROUP BY a.id,a.name LIMIT 200")
    # Current OUTSTANDING reserve: `balance` is a running outstanding figure per
    # transaction, so the true open reserve is the LATEST row per claim+coverage
    # (summing all rows would double-count revisions). No CTE — run_select's
    # guard requires the statement to start with SELECT.
    outstanding = _rows(
        "SELECT a.name AS broker, ROUND(SUM(latest.balance),0) AS os_reserve FROM ("
        "SELECT crc.claim_id, crc.balance FROM claim_reserves_coverages crc "
        "JOIN (SELECT claim_id,coverage_id,MAX(id) mid FROM claim_reserves_coverages "
        "GROUP BY claim_id,coverage_id) m ON m.mid=crc.id) latest "
        "JOIN claims cl ON latest.claim_id=cl.id "
        "JOIN policies p ON cl.policy_id=p.id JOIN agencies a ON p.agency_id=a.id "
        "GROUP BY a.id,a.name LIMIT 200")

    # Policies carrying no broker at all fall outside every agency bucket above.
    # Counted so the percentages below are read against a STATED denominator
    # rather than one that quietly shrank when the attribution key changed.
    unattr = _rows(
        "SELECT COUNT(*) AS pol, ROUND(SUM(annual_premium),0) AS gwp "
        "FROM policies WHERE status=1 AND agency_id IS NULL LIMIT 1")
    u_row = unattr[0] if unattr else {}
    unattr_pol = int(u_row.get('pol') or 0)
    unattr_gwp = _num(u_row.get('gwp'))

    nb_by = {r['broker']: r for r in nb}
    paid_by = {r['broker']: r for r in paid}
    os_by = {r['broker']: r for r in outstanding}

    brokers: List[Dict[str, Any]] = []
    direct: List[Dict[str, Any]] = []
    total_gwp = 0.0
    total_pol = 0
    for r in inforce:
        name = r['broker']
        gwp = _num(r.get('inforce_gwp'))
        pol = int(r.get('active_pol') or 0)
        total_gwp += gwp
        total_pol += pol
        row = {
            'broker': name,
            'active_pol': pol,
            'inforce_gwp': gwp,
            'nb_cnt': int((nb_by.get(name) or {}).get('nb_cnt') or 0),
            'nb_gwp': _num((nb_by.get(name) or {}).get('nb_gwp')),
            'net_paid_12m': _num((paid_by.get(name) or {}).get('net_paid')),
            'outstanding': _num((os_by.get(name) or {}).get('os_reserve')),
        }
        (direct if _is_direct(name) else brokers).append(row)

    broker_gwp = sum(b['inforce_gwp'] for b in brokers)
    for b in brokers:
        b['pct'] = round(b['inforce_gwp'] / broker_gwp * 100, 1) if broker_gwp else 0.0
    direct_gwp = sum(d['inforce_gwp'] for d in direct)

    # duplicate-agency heuristic: same name once case / spacing / t/a / suffix
    # noise is stripped. Flags the split-book problem without merging silently.
    seen: Dict[str, str] = {}
    dups: List[str] = []
    for b in brokers:
        k = ''.join(b['broker'].lower().split())
        k = k.replace('t/a', '').replace('(pty)', '').replace('ltd', '')
        if k in seen and seen[k] != b['broker']:
            dups.append(b['broker'])
            dups.append(seen[k])
        seen[k] = b['broker']

    notes = [
        "In-force book = annual premium of currently-active policies "
        "(avoids the re-rating duplicates in the transaction history).",
        "New business and claims paid cover the trailing 12 months; "
        "outstanding reserve is the current open amount (latest reserve state "
        "per claim, net of payments).",
        "No loss-ratio % is shown on purpose. The premium a broker holds today "
        "does not line up with claims paid over prior periods — business has "
        "moved between brokers and the direct channel over the years — so a "
        "per-broker ratio is unreliable and would mislead. Reliable claims cost "
        "is shown as money (paid + outstanding). A true per-broker loss ratio "
        "needs a separate actuarial build (earned premium matched to claims by "
        "underwriting year).",
    ]
    if unattr_pol:
        notes.append(
            f"{unattr_pol:,} active policies (P{unattr_gwp:,.0f} of annual "
            "premium) carry no broker on the record and sit outside every "
            "figure above — the direct/broker split is of the attributed book.")
    notes.append(
        "A policy counts towards the broker named on the policy itself, not the "
        "agency its salesperson belongs to. Policies with no broker recorded are "
        "left out rather than guessed.")
    notes.append(
        "Claims paid and outstanding here cover agencies with a current "
        "in-force book; the Claims Registry report is the authoritative total "
        "for all open claims across the company.")
    if dups:
        notes.append("Possible duplicate agency record(s): "
                     + ", ".join(sorted(set(dups)))
                     + " — that broker's book is split across two rows.")

    total_paid = round(sum(x['net_paid_12m'] for x in brokers + direct))
    total_os = round(sum(x['outstanding'] for x in brokers + direct))

    from django.utils import timezone
    return {
        'generated_at': timezone.now().strftime('%d %b %Y, %H:%M'),
        'totals': {
            'inforce_gwp': round(total_gwp),
            'active_pol': total_pol,
            'agencies': len(inforce),
            'unattributed_pol': unattr_pol,
            'unattributed_gwp': round(unattr_gwp),
            'claims_paid_12m': total_paid,
            'outstanding': total_os,
        },
        'channel': {
            'direct': {
                'gwp': round(direct_gwp),
                'pol': sum(d['active_pol'] for d in direct),
                'pct': round(direct_gwp / total_gwp * 100, 1) if total_gwp else 0,
            },
            'broker': {
                'gwp': round(broker_gwp),
                'pol': sum(b['active_pol'] for b in brokers),
                'pct': round(broker_gwp / total_gwp * 100, 1) if total_gwp else 0,
            },
        },
        'brokers': sorted(brokers, key=lambda x: -x['inforce_gwp']),
        'notes': notes,
    }
