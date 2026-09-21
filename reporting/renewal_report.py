"""Renewal report (Workstream B / B1) — policies renewing in a chosen month.

Built to the CFO + Finance settled spec (17-Sep-2026, see the board bug pack and
memory p-renewal-broker-decisions-sep2026):

- Reads GRAPHITE (read-only replica) via integrations.graphite_ro — the same door
  Aware and the age-analysis report use.
- A policy's renewal is driven off the INVOICE, not a policy-header date: the
  ANCHOR action is the latest ISSUED ANNIVERSARY-RENEW; if the policy has not
  reached its first anniversary yet, the ISSUED NEWBUSINESS action (its very
  first). Only those two transaction types count; a monthly RENEW or an
  endorsement is never the anchor. Only ISSUED actions — a QUOTE-stage renewal
  does not appear.
- MONTH ONLY. The renewal month is MONTH(anchor.effective_from) — the policy's
  anniversary month. Every policy renewing in that month appears, any year. There
  is deliberately no year filter (Finance: "the year is NOT a filter").
- Every qualifying in-force policy appears EXACTLY ONCE (one anchor per policy).
- First-year policies (no anniversary yet — anchored on NEWBUSINESS) are INCLUDED
  and flagged.
- Renewal expiry = the next anniversary = anchor.effective_from + 1 year, so a
  monthly policy shows its annual renewal, not its one-month billing period.

KNOWN FAST-FOLLOWS (v1 ships without perfect versions of these two, clearly):
- Sum insured: the header column `policies.sum_assured` is null for most
  commercial policies and the per-coverage table the repo named
  (`policy_specified_items`) does not exist in the live schema — so SI is left
  blank pending the real coverage source. Everything else is live-validated.
- Renewal / in-force premium: taken from the policy header (`annual_premium`,
  `premium`) — the current-state figure. On heavily-endorsed policies the header
  and the individual action rows diverge; the header is the canonical current
  value and is what the age-style reports use.
"""
from __future__ import annotations

from datetime import date

from integrations import graphite_ro

# premium_freq -> label. The DomCom crons + RenewalPipelineExport use this map;
# some legacy files map 2=Quarterly, which is WRONG for DomCom (5=Quarterly).
FREQ_LABELS = {
    1: 'Monthly', 2: 'Three Instalments', 3: 'Annual',
    4: 'Semiannual', 5: 'Quarterly', 6: 'Manual Input',
}

# The column order the report shows / exports, to the Finance-agreed list.
COLUMNS = [
    'Policy Number', 'Insured Name', 'Broker', 'Agent',
    'Product / Line of Business', 'First-Year', 'Renewal Date', 'Renewal Expiry',
    'Payment Frequency', 'Policy Status', 'Sum Insured',
    'Renewal Premium (Annual)', 'In-force Premium (Per Period)',
]

# Anchor = latest ISSUED anniversary-renew, else the ISSUED new-business action.
# Header premiums come off the policy row (current state). %s is the month param;
# %% escapes the LIKE wildcard for the pymysql driver.
_SQL = """
SELECT p.policyNumber,
       TRIM(CONCAT(COALESCE(c.firstName,''),' ',COALESCE(c.lastName,''))) AS insured_name,
       ab.name AS broker_name,
       TRIM(CONCAT(COALESCE(ag.firstName,''),' ',COALESCE(ag.lastName,''))) AS agent_name,
       prod.name AS product_name,
       pa.effective_from AS renewal_effective,
       p.premium_freq AS premium_freq,
       p.sum_assured AS sum_insured,
       p.annual_premium AS renewal_premium,
       p.premium AS inforce_premium,
       (anniv.id IS NULL) AS is_first_year
FROM policies p
LEFT JOIN (SELECT policy_id, MAX(id) id FROM policy_actions
           WHERE transaction_type='ANNIVERSARY-RENEW' AND status='ISSUED' AND deleted_at IS NULL
           GROUP BY policy_id) anniv ON anniv.policy_id = p.id
LEFT JOIN (SELECT policy_id, MIN(id) id FROM policy_actions
           WHERE transaction_type='NEWBUSINESS' AND status='ISSUED' AND deleted_at IS NULL
           GROUP BY policy_id) nb ON nb.policy_id = p.id
JOIN policy_actions pa ON pa.id = COALESCE(anniv.id, nb.id)
LEFT JOIN customer c ON c.id = p.customer_id
LEFT JOIN users ag ON ag.id = p.agent_id
LEFT JOIN agencies ab ON ab.id = p.agency_id
LEFT JOIN products prod ON prod.id = p.product_id
WHERE p.status = 1
  AND ({prefix})
  AND MONTH(pa.effective_from) = %s
ORDER BY p.policyNumber
"""


def _prefix_clause(section: str | None) -> str:
    """The policy-number prefix filter for the section. Domestic = DOMG,
    Commercial = COMG; no filter = both (the Graphite-native DomCom book)."""
    s = (section or '').strip().lower()
    if s in ('domestic', 'dom', 'domg'):
        return "p.policyNumber LIKE 'DOMG%%'"
    if s in ('commercial', 'com', 'comg'):
        return "p.policyNumber LIKE 'COMG%%'"
    return "p.policyNumber LIKE 'DOMG%%' OR p.policyNumber LIKE 'COMG%%'"


def _plus_one_year(d):
    """The next anniversary. Handles 29 Feb by falling back to 28 Feb."""
    if not isinstance(d, date):
        return None
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def _ddmmyyyy(d) -> str:
    return d.strftime('%d-%m-%Y') if isinstance(d, date) else ''


def _money(v) -> str:
    if v is None:
        return ''
    return f'{float(v):,.2f}'


def shape_row(r: dict) -> dict:
    """One raw DB row -> the display dict, in COLUMNS order (as a dict keyed by
    header). Pure — no DB access — so it is unit-tested directly."""
    eff = r.get('renewal_effective')
    return {
        'Policy Number': r.get('policyNumber') or '',
        'Insured Name': (r.get('insured_name') or '').strip(),
        'Broker': r.get('broker_name') or '',
        'Agent': (r.get('agent_name') or '').strip(),
        'Product / Line of Business': r.get('product_name') or '',
        'First-Year': 'Yes' if r.get('is_first_year') else '',
        'Renewal Date': _ddmmyyyy(eff),
        'Renewal Expiry': _ddmmyyyy(_plus_one_year(eff)),
        'Payment Frequency': FREQ_LABELS.get(int(r.get('premium_freq') or 0), 'Unknown'),
        'Policy Status': 'Active',
        'Sum Insured': _money(r.get('sum_insured')),
        'Renewal Premium (Annual)': _money(r.get('renewal_premium')),
        'In-force Premium (Per Period)': _money(r.get('inforce_premium')),
    }


def fetch_renewals(month: int, section: str | None = None, *,
                   runner=None, limit: int = 20000) -> list[dict]:
    """Every policy renewing in `month` (1-12), optionally filtered to a section.
    Returns a list of display dicts (COLUMNS-keyed), one per policy.

    `runner` defaults to the Graphite read-only reader; a test passes a stub that
    returns canned rows, so the shaping is proven without a live database.
    """
    if not (1 <= int(month) <= 12):
        raise ValueError('month must be 1-12')
    run = runner or graphite_ro.query
    sql = _SQL.format(prefix=_prefix_clause(section))
    rows = run(sql, [int(month)], limit=limit)
    return [shape_row(r) for r in rows]


def to_matrix(shaped: list[dict]) -> list[list]:
    """Header row + one row per policy, in COLUMNS order — for xlsx/csv."""
    return [list(COLUMNS)] + [[row.get(c, '') for c in COLUMNS] for row in shaped]
