"""Read a Graphite policy to pre-fill a RENEWAL quote and surface the client's
claims — over the existing read-only Graphite bridge (aware.engine).

Keyed on the POLICY NUMBER the underwriter types, never on a name. That is
deliberate: matching by name is exactly how one client's file became another's,
the problem the Quote Builder exists to end. If the number isn't found, we say
so and the underwriter fills the quote in by hand.

Only NON-personal fields cross the wire: the business name (a company name), the
class, last year's premium, the sum insured, and CLAIMS AGGREGATES (counts and
totals — never individual claim detail). No sensitive personal-data columns are
selected; the shared guard would reject the query if any were.
"""
from __future__ import annotations

import re

_POLICY_RE = re.compile(r'^[A-Za-z0-9/_-]{4,40}$')


def lookup_policy(policy_number: str) -> dict:
    pn = (policy_number or '').strip()
    if not _POLICY_RE.match(pn):
        return {'found': False, 'error': 'That does not look like a policy number.'}

    from aware.engine import run_select_params

    rows = run_select_params(
        "SELECT p.policyNumber AS policy_number, p.business_name, "
        "p.annual_premium, p.sum_assured, p.customer_id, "
        "pr.name AS product_name, pr.line_of_business "
        "FROM policies p "
        "LEFT JOIN products pr ON pr.id = p.product_id "
        "WHERE p.policyNumber = %s LIMIT 1",
        [pn],
    )
    if not rows:
        return {'found': False, 'error': 'No policy with that number was found.'}
    r = rows[0]

    claims = run_select_params(
        "SELECT COUNT(*) AS n, COALESCE(SUM(nc.reserve_amount),0) AS reserve, "
        "COALESCE(SUM(nc.paid_amount),0) AS paid "
        "FROM new_claims nc JOIN policies pp ON pp.id = nc.policy_id "
        "WHERE pp.customer_id = %s",
        [r.get('customer_id')],
    )
    c = claims[0] if claims else {}

    return {
        'found': True,
        'policy_number': r.get('policy_number') or pn,
        'client_name': (r.get('business_name') or '').strip(),
        'class_of_business': (r.get('product_name') or r.get('line_of_business') or '').strip(),
        'prior_annual_premium': str(r.get('annual_premium') or ''),
        'sum_insured': str(r.get('sum_assured') or ''),
        'claims': {
            'count': int(c.get('n') or 0),
            'reserve': str(c.get('reserve') or '0'),
            'paid': str(c.get('paid') or '0'),
        },
    }
