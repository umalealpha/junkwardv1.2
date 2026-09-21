"""[B1] Renewal Automation — who renews in a given month, read from Graphite.

Finance runs this to work the renewals list. It replaces the manual Graphite
export-and-clean that Bokani Makosha's team does by hand each month.

THE RULE, as Finance settled it on 14-Sep-2026 (board item [B2], verbatim):

    "The most important thing to look at is the invoice. The last ANNIVERSARY
    issued invoice will tell you when the policy renews, but some policies have
    not reached their anniversary yet so NEWBUSINESS issued, meaning the very
    first invoice, will tell you when the policy renews. So only 2 things,
    ANNIVERSARY RENEW and NEWBUSINESS-ISSUED. Furthermore, include any policy
    whose renewal date falls in a specific month, regardless of what year it
    started."

So: the anniversary day/month comes off the LAST `ANNIVERSARY-RENEW` action, and
from the FIRST `NEWBUSINESS` action for a policy that has not reached its first
anniversary — those are flagged as a first renewal. `RENEW` rows are the cron's
monthly/quarterly billing cycles (28,529 of them live, against 2,529 real
anniversaries) and are ignored entirely; counting them would put a monthly
policy on the list twelve times.

MONTH ONLY. The year is deliberately not a filter — the original spec made
renewal year mandatory and Finance's answer overrides it.

THE ONE THING STILL UNANSWERED, so it is a switch and not a guess: whether an
`ANNIVERSARY-RENEW` sitting at QUOTE counts. The original spec said include it,
Finance has since written "ANNIVERSARY issued" twice. Default is ISSUED only
(Finance's own words); `include_quotes` shows the other reading without a code
change, so nobody has to pick for them.

Measured on the live replica 15-Sep-2026, and worth telling Finance: the answer
barely matters for WHO appears. Of the live domestic/commercial policies, 1,305
carry only a quoted anniversary and 736 carry one newer than their issued one —
but a quoted anniversary sits in the same month as the term it renews, so only
FOUR policies change month. October returns 310 rows either way. What the switch
really changes is the premium and expiry shown, and whether a policy reads as a
first renewal. The decision is a labelling one, not a "who do we chase" one.

On an anniversary row the premium comes off the ACTION, falling back to the
policy row when the action has none — which is 1,640 of 2,140 live anniversary
policies, so the fallback is the normal path, not an edge case. Where both exist
they agree on 470 of 500 sampled and never by a factor of 12 or 4, so they carry
the same per-invoice meaning.

INFORCE PREMIUM is the stored premium untouched — see the note on
PERIODS_PER_YEAR below, which is the one place this report could have handed
Finance a wrong number and did not.

SUM INSURED IS NOT IN GRAPHITE for this book. `policies.sum_assured` is empty on
all 4,134 live domestic/commercial policies, and the replica exposes no section
or cover table to compute it from. The column is therefore present and blank,
with `notes` saying why on every response — a product-level default would look
like the policy's own figure and be wrong on most rows.

Read-only, through the sanctioned `graphite_ro` door. Scope is DOMESTIC and
COMMERCIAL (DOMG/COMG); Instant (MIS, 206,402 policies) is excluded, as is any
row flagged `is_test_policy`.
"""
from __future__ import annotations

import logging
from datetime import date

from django.utils import timezone

from .graphite_lookup_views import FREQ, POLICY_STATUS
from .graphite_ro import is_configured, query

log = logging.getLogger(__name__)

# How many times a year the customer is billed, per Graphite's frequency code.
# FREQ (1 Monthly / 3 Annual / 5 Quarterly) is Omni's existing map and stays the
# single source for the LABEL; this is only the arithmetic that goes with it.
# Codes outside it are left blank and reported as an exception rather than
# guessed — code 6 alone carries 226 domestic/commercial policies.
PERIODS_PER_YEAR = {1: 12, 3: 1, 5: 4}

LOB_CHOICES = ('domestic', 'commercial')

# The live book is ~4,100 domestic and commercial policies, so this is five times
# the headroom. `graphite_ro.query` uses fetchmany, which TRUNCATES SILENTLY, so
# `build` treats a full read as a failure rather than a short list.
_READ_LIMIT = 20000

# One row per policy: the anniversary anchor comes from the last ANNIVERSARY-RENEW,
# or the first NEWBUSINESS when the policy has not reached its anniversary yet.
_SQL = """
SELECT p.policyNumber                                   AS policy_number,
       COALESCE(NULLIF(TRIM(p.business_name), ''),
                TRIM(CONCAT(COALESCE(c.firstName, ''), ' ',
                            COALESCE(c.lastName, '')))) AS insured_name,
       ag.name                                          AS broker_name,
       TRIM(CONCAT(COALESCE(u.firstName, ''), ' ',
                   COALESCE(u.lastName, '')))           AS agent_name,
       pr.name                                          AS product_name,
       p.premium_freq                                   AS freq_code,
       p.status                                         AS status_code,
       p.sum_assured                                    AS sum_insured,
       p.annual_premium                                 AS annual_premium,
       av.eff                                           AS anniv_from,
       av.exp                                           AS anniv_to,
       av.prem                                          AS anniv_premium,
       nb.eff                                           AS newbus_from,
       nb.exp                                           AS newbus_to
  FROM policies p
  LEFT JOIN customer c  ON c.id  = p.customer_id
  LEFT JOIN agencies ag ON ag.id = p.agency_id
  LEFT JOIN users u     ON u.id  = p.agent_id
  LEFT JOIN products pr ON pr.id = p.product_id
  LEFT JOIN (
        SELECT a.policy_id, a.effective_from AS eff, a.effective_to AS exp,
               a.annual_premium AS prem, MAX(a.id) AS action_id
          FROM policy_actions a
          JOIN (SELECT policy_id, MAX(effective_from) eff
                  FROM policy_actions
                 WHERE transaction_type = 'ANNIVERSARY-RENEW'
                   AND status IN %(anniv_statuses)s
                   AND deleted_at IS NULL
                 GROUP BY policy_id) m
            ON m.policy_id = a.policy_id AND m.eff = a.effective_from
         WHERE a.transaction_type = 'ANNIVERSARY-RENEW'
           AND a.status IN %(anniv_statuses)s
           AND a.deleted_at IS NULL
         GROUP BY a.policy_id, a.effective_from, a.effective_to, a.annual_premium
  ) av ON av.policy_id = p.id
  LEFT JOIN (
        SELECT a.policy_id, MIN(a.effective_from) AS eff, MIN(a.effective_to) AS exp
          FROM policy_actions a
         WHERE a.transaction_type = 'NEWBUSINESS'
           AND a.status = 'ISSUED'
           AND a.deleted_at IS NULL
         GROUP BY a.policy_id
  ) nb ON nb.policy_id = p.id
 WHERE p.policyNumber REGEXP %(prefix_re)s
   AND COALESCE(p.is_test_policy, 0) = 0
   AND p.status = 1
 ORDER BY p.policyNumber, av.action_id DESC
"""


def _next_occurrence(anchor: date, today: date) -> date | None:
    """The next time this anniversary day/month comes round, on or after today.

    The anchor is the term the invoice records, which for an established policy
    is in the past. A renewals clerk works the date ahead of them, so the row
    carries both: the anchor it was derived from, and the date to action.
    """
    if anchor is None:
        return None
    for year in (today.year, today.year + 1):
        try:
            candidate = anchor.replace(year=year)
        except ValueError:          # 29 February on a non-leap year
            candidate = anchor.replace(year=year, day=28)
        if candidate >= today:
            return candidate
    return None


def build(month: int, *, lob: str = '', include_quotes: bool = False,
          today: date | None = None) -> dict:
    """Every in-force domestic/commercial policy renewing in *month*.

    Returns rows plus an `exceptions` list — a policy with no usable invoice, or
    a billing frequency Graphite does not explain, is reported, never dropped
    silently and never given an invented value.
    """
    if month not in range(1, 13):
        raise ValueError('month must be 1-12')
    if lob and lob not in LOB_CHOICES:
        raise ValueError(f'lob must be one of {LOB_CHOICES} or blank')
    if not is_configured():
        return {'available': False, 'month': month, 'rows': [], 'exceptions': [],
                'reason': 'The Graphite read-only connection is not configured, '
                          'so this report cannot be produced.'}

    today = today or timezone.localdate()
    prefix_re = {'domestic': '^DOMG', 'commercial': '^COMG'}.get(lob, '^(DOMG|COMG)')
    statuses = ('ISSUED', 'QUOTE') if include_quotes else ('ISSUED',)

    raw = query(_SQL, {'anniv_statuses': statuses, 'prefix_re': prefix_re},
                limit=_READ_LIMIT)

    # A read that comes back empty, or exactly full, is a broken read — not a
    # month with no renewals. Saying "no policy renews in October" on either
    # would be the false all-clear this report exists to avoid.
    if not raw:
        return {'available': False, 'month': month, 'rows': [], 'exceptions': [],
                'notes': [], 'reason': 'Graphite returned no in-force domestic or '
                          'commercial policies at all. That is the connection, not '
                          'the month — nothing can be read right now.'}
    if len(raw) >= _READ_LIMIT:
        return {'available': False, 'month': month, 'rows': [], 'exceptions': [],
                'notes': [], 'reason': f'Graphite returned the maximum '
                          f'{_READ_LIMIT:,} rows, so the list would be cut short '
                          f'and some renewals missing. Raise the limit before '
                          f'trusting this report.'}

    rows, exceptions, seen = [], [], set()
    for r in raw:
        anchor = r['anniv_from'] or r['newbus_from']
        if anchor is None:
            exceptions.append({'policy_number': r['policy_number'],
                               'problem': 'No issued anniversary or new-business '
                                          'invoice, so the renewal date is unknown.'})
            continue
        if anchor.month != month:
            continue
        if r['policy_number'] in seen:
            exceptions.append({
                'policy_number': r['policy_number'],
                'problem': 'Graphite holds more than one anniversary invoice on '
                           'the same date for this policy, so it is shown once '
                           'and the duplicate is flagged for Underwriting.'})
            continue
        seen.add(r['policy_number'])

        # Named for the fact, not for the unanswered rule: with quotes excluded
        # (the default), 1,305 live policies have a quoted anniversary that this
        # cannot see, and badging those a "first renewal" would state as fact
        # something Finance has not decided.
        no_issued_anniversary = r['anniv_from'] is None
        expiry = r['newbus_to'] if no_issued_anniversary else r['anniv_to']

        freq_code = r['freq_code']
        freq_code = int(freq_code) if str(freq_code or '').isdigit() else None
        periods = PERIODS_PER_YEAR.get(freq_code)
        # The anniversary ACTION carries the premium for the term being
        # renewed, but it is null on 1,640 of 2,140 live anniversary policies,
        # so the policy row is the normal fallback rather than an edge case.
        annual = None if no_issued_anniversary else r['anniv_premium']
        if annual is None:
            annual = r['annual_premium']

        # The stored figure IS what the customer pays each period. Annualising
        # is the part that needs the frequency, so an unexplained code costs the
        # annual column and never the in-force one Finance asked for.
        #
        # A missing or negative premium is left EMPTY, never rounded into a
        # confident P0.00. About 220 of the 4,112 live policies have no usable
        # figure (36 anniversary rows with both dead, 184 first-term rows at
        # NULL or zero) and at least one carries a real negative, so roughly
        # eighteen rows a month would otherwise assert a premium nobody holds.
        if annual is None or float(annual) <= 0:
            inforce = annualised = None
            exceptions.append({
                'policy_number': r['policy_number'],
                'problem': 'Graphite holds no premium for this policy, so both '
                           'premium columns are empty rather than showing zero.',
                'on_the_list': True})
        elif periods is None:
            inforce, annualised = round(float(annual), 2), None
            exceptions.append({
                'policy_number': r['policy_number'],
                'problem': f'Billing frequency {freq_code!r} is not one Graphite '
                           f'explains, so the annual renewal premium cannot be '
                           f'worked out. The in-force premium below is still '
                           f'what they pay each period.',
                'on_the_list': True})
        else:
            inforce = round(float(annual), 2)
            annualised = round(float(annual) * periods, 2)

        rows.append({
            'policy_number': r['policy_number'],
            'insured_name': (r['insured_name'] or '').strip(),
            'broker_name': (r['broker_name'] or '').strip(),
            'agent_name': (r['agent_name'] or '').strip(),
            'product': (r['product_name'] or '').strip(),
            'renewal_effective_date': anchor.isoformat(),
            'renewal_expiry_date': expiry.isoformat() if expiry else '',
            'next_renewal_date': (lambda d: d.isoformat() if d else '')(
                _next_occurrence(anchor, today)),
            'payment_frequency': FREQ.get(freq_code, '') or f'Code {freq_code}',
            'policy_status': POLICY_STATUS.get(r['status_code'], ''),
            'sum_insured': r['sum_insured'] or '',
            'renewal_premium': annualised,
            'inforce_premium': inforce,
            'no_issued_anniversary': no_issued_anniversary,
        })

    rows.sort(key=lambda x: (x['next_renewal_date'], x['policy_number']))
    notes = []
    dropped = [e for e in exceptions if not e.get('on_the_list')]
    flagged = [e for e in exceptions if e.get('on_the_list')]
    if any(not r['sum_insured'] for r in rows):
        notes.append('Sum insured is blank because Graphite does not hold it for '
                     'domestic and commercial policies. It is not a loading error.')
    return {
        'available': True,
        'notes': notes,
        'month': month,
        'lob': lob,
        'include_quotes': include_quotes,
        'rows': rows,
        'exceptions': exceptions,
        'dropped': dropped,
        'flagged': flagged,
        'count': len(rows),
        'first_renewal_count': sum(1 for x in rows
                                   if x['no_issued_anniversary']),
    }
