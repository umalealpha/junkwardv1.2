"""
integrations/graphite_live_broker_lr.py — compute the BROKER LOSS RATIO feed from
the Graphite read replica instead of the pushed Alpha-Brain snapshot.

CFO 2026-09-08. The pushed `broker_lr` dataset feeds the standing rule that a
broker over 70% escalates to the CFO, and it was wrong two ways — both proved on
the live replica before this was written:

  1. **Wrong broker.** It reached the broker through the SELLING AGENT
     (`policies.agent_id` -> `users.agency_id`) rather than the intermediary
     recorded on the policy. 22 brokers carrying live claims never reached the
     flag at all — Marsh (P5.56m paid), Minet, Spectrum, Botshabelo, UTL, Riskco
     among them. Same fault, and the same fix, as `aware/modes/broker_analysis`.

  2. **Settled claims counted twice.** Claim cost was payments PLUS reserve
     movements, but a reserve is what later gets paid — the same money. Claim
     3781: reserved P2,000,000, settled P2,000,000, scored P4,000,000. Exactly
     2.00x on every claim that has paid out. The honest open reserve is the
     LATEST balance per claim+coverage, never a SUM over the revision rows.

This follows the route the CFO already took on 2026-09-01 for the three claims
cards (`graphite_live_claims`): where the pushed feed is wrong, Omni computes the
card itself from data it can already read.

Where it differs: the CFO's ESCALATION PANEL fails CLOSED. If the replica cannot
be read it shows no broker figures and says so, rather than reverting to the
push — the push escalates a different, wrong set of brokers, and a rule that
quietly gives the wrong answer is worse than one that says it cannot answer. The
Graphite Feeds viewer still falls through to the push, because that screen is
explicitly showing what Graphite sent.

Read-only through the sanctioned `graphite_ro` door (SELECT-only, replica-only).
Aggregates by brokerage FIRM: no policyholder, claimant or agent personal data.

NOT fixed here, because it is the CFO's call and not a coding one: the ratio
still sets claims against premium WRITTEN in the same financial year to date.
Early in a year that is a thin, volatile denominator, and it charges a broker for
claims on business it may no longer write. A true loss ratio needs earned premium
matched by underwriting year.
"""
from __future__ import annotations

import logging

from core.broker_names import merge_rows

from .graphite_ro import is_configured, query

log = logging.getLogger(__name__)

#: The one dataset computed here. Every other feed stays on the pushed snapshot.
LIVE_BROKER_LR_DATASET = 'broker_lr'

# The financial year starts 1 July. Same window the pushed feed used, so this
# change moves the CORRECTNESS of the figure and not its period — the period is a
# separate, open decision for the CFO (see the module note above).
_FY_START = ("(CASE WHEN MONTH(CURDATE()) >= 7 "
             "THEN DATE(CONCAT(YEAR(CURDATE()),'-07-01')) "
             "ELSE DATE(CONCAT(YEAR(CURDATE())-1,'-07-01')) END)")

# reported_date is sparse on this book, so the claim's date falls back the same
# way the Graphite extract does — reported, else incident, else created.
_CLAIM_DATE = "COALESCE(c.reported_date, c.incident_date, DATE(c.created_at))"

_NON_VOIDED = "(crc.is_payment_voided IS NULL OR crc.is_payment_voided = 0)"

# A CANCEL reverses premium. Negated on the absolute value so a source row that
# is already negative cannot flip back to positive.
_NET_PREM = ("CASE WHEN a.transaction_type = 'CANCEL' "
             "THEN -ABS(a.premium) ELSE a.premium END")

# Written premium for the FY: within a policy TERM keep only the latest ISSUED,
# non-deleted action, so a re-rated policy is counted once.
_PREMIUM_KEEP = (
    "SELECT MAX(id) AS id FROM policy_actions "
    "WHERE status = 'ISSUED' AND deleted_at IS NULL "
    f"AND transaction_date >= {_FY_START} AND transaction_date <= CURDATE() "
    "GROUP BY policy_id, effective_from, effective_to")

# ── the three reads ───────────────────────────────────────────────────────────
# Every one joins agencies through `p.agency_id` — the intermediary ON the policy.

_PREMIUM_SQL = (
    "SELECT ag.name AS broker, ROUND(SUM(x.net_prem),2) AS premium_fy "
    f"FROM (SELECT a.policy_id, {_NET_PREM} AS net_prem FROM policy_actions a "
    f"JOIN ({_PREMIUM_KEEP}) keep ON keep.id = a.id) x "
    "JOIN policies p ON p.id = x.policy_id "
    "JOIN agencies ag ON ag.id = p.agency_id "
    "GROUP BY ag.name")

_PAID_SQL = (
    "SELECT ag.name AS broker, COUNT(DISTINCT c.id) AS claim_count, "
    f"ROUND(SUM(CASE WHEN {_NON_VOIDED} THEN COALESCE(crc.payment_amt,0) "
    "ELSE 0 END),2) AS payment "
    "FROM claims c JOIN policies p ON p.id = c.policy_id "
    "JOIN agencies ag ON ag.id = p.agency_id "
    "LEFT JOIN claim_reserves_coverages crc ON crc.claim_id = c.id "
    f"WHERE {_CLAIM_DATE} >= {_FY_START} AND {_CLAIM_DATE} <= CURDATE() "
    "GROUP BY ag.name")

# Open reserve = the LATEST balance per claim+coverage. `balance` is a running
# outstanding figure per transaction, so summing the rows double-counts every
# revision, and adding SUM(reserve_amt) to payments double-counts every claim
# that has settled. This is the one honest way to read it.
_OUTSTANDING_SQL = (
    "SELECT ag.name AS broker, ROUND(SUM(latest.balance),2) AS outstanding FROM ("
    "SELECT crc.claim_id, crc.balance FROM claim_reserves_coverages crc "
    "JOIN (SELECT claim_id, coverage_id, MAX(id) mid FROM claim_reserves_coverages "
    "GROUP BY claim_id, coverage_id) m ON m.mid = crc.id) latest "
    "JOIN claims c ON c.id = latest.claim_id "
    "JOIN policies p ON p.id = c.policy_id "
    "JOIN agencies ag ON ag.id = p.agency_id "
    f"WHERE {_CLAIM_DATE} >= {_FY_START} AND {_CLAIM_DATE} <= CURDATE() "
    "GROUP BY ag.name")

# There are ~90 agencies on the whole book, so this ceiling is far above any
# real answer. It exists to make truncation IMPOSSIBLE to miss: the reads ask for
# one row more than the cap, and a result that reaches it raises rather than
# quietly returning a short list. A silently truncated broker list is the exact
# failure this module was written to fix.
# The annual premium the broker's LIVE policies are worth today. This is the
# denominator the CFO chose (2026-09-08) over premium written in the FY to date:
# ten weeks of written premium made Letsema read 11,683% and Spectrum 811%, which
# is arithmetic, not information. Against the book they actually hold those become
# 542% and 62% — and BOC moves the other way, 115% to 154%, which is the signal.
_INFORCE_SQL = (
    "SELECT ag.name AS broker, "
    "ROUND(SUM(CASE WHEN p.status=1 THEN p.annual_premium ELSE 0 END),2) AS inforce "
    "FROM policies p JOIN agencies ag ON ag.id = p.agency_id "
    "GROUP BY ag.name")

# A broker whose whole book is smaller than this does not escalate to the CFO —
# one claim will always exceed a tiny premium, and Sparkle Legacy tripping the
# rule on P2,475 of book buries the brokers that move real money. They stay ON
# the panel; they just sit below the escalation line. CFO 2026-09-08.
ESCALATION_MIN_BOOK = 50_000.0

_ROW_CAP = 2000


# Grouped by ag.NAME, never ag.id. Four agency records carry a name that already
# exists (FinSef 11/34, Kgare 12/41, Letsema 14/44, Nnawalt 13/47), and the rows
# below are keyed by name in Python — so grouping by id returns two rows that
# silently overwrite each other and one record's money is thrown away. Letsema
# read a P10,699 book instead of P390,049 exactly this way. Grouping by name lets
# the database add them up; merge_rows then handles the differently-SPELLED pairs.


def available() -> bool:
    return is_configured()


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _read(sql: str) -> list:
    """One read, with truncation made fatal rather than silent."""
    rows = query(sql, limit=_ROW_CAP + 1)
    if len(rows) > _ROW_CAP:
        raise RuntimeError(
            f'broker_lr: read returned more than {_ROW_CAP} rows — refusing to '
            'serve a truncated broker list to the escalation rule')
    return rows


def build(dataset: str):
    """Rows for the broker-LR feed, computed live.

    None if this is not that dataset or the replica cannot be read. What the
    caller does with None differs by screen: the escalation panel shows nothing,
    the feeds viewer falls through to the labelled push."""
    if dataset != LIVE_BROKER_LR_DATASET or not is_configured():
        return None

    try:
        premium = _read(_PREMIUM_SQL)
        paid = _read(_PAID_SQL)
        outstanding = _read(_OUTSTANDING_SQL)
        inforce = _read(_INFORCE_SQL)
    except Exception:
        # Logged as an error, because the escalation panel goes dark on this:
        # the pushed snapshot is KNOWN WRONG (wrong broker, double-counted
        # claims) and putting it back on screen would silently restore the old,
        # understated flag list.
        log.exception('broker_lr: live computation failed — falling back to the '
                      'PUSHED snapshot, which under-reports brokers')
        return None

    prem_by = {r['broker']: _f(r.get('premium_fy')) for r in premium}
    book_by = {r['broker']: _f(r.get('inforce')) for r in inforce}
    paid_by = {r['broker']: r for r in paid}
    os_by = {r['broker']: _f(r.get('outstanding')) for r in outstanding}

    rows = []
    for broker in set(prem_by) | set(paid_by) | set(os_by) | set(book_by):
        prem = prem_by.get(broker, 0.0)
        reserve = os_by.get(broker, 0.0)
        pay_row = paid_by.get(broker) or {}
        rows.append({
            'broker': broker,
            'payment': round(_f(pay_row.get('payment')), 2),
            'reserve': round(reserve, 2),
            'premium_fy': round(prem, 2),
            'inforce_book': round(book_by.get(broker, 0.0), 2),
            'claim_count': int(pay_row.get('claim_count') or 0),
            # Kept for shape-compatibility with the pushed feed: the open-reserve
            # ratio only. The panel computes the incurred ratio the rule uses.
            'lr_on_reserve': (round(reserve / prem, 4) if prem > 0 else None),
        })

    # Graphite lets the same broker exist twice (no uniqueness on `agencies`), and
    # a split record puts the premium on one row and the claims on the other — so
    # BOTH halves score wrongly and the 70% rule escalates on a false ratio.
    # Merge first, then take the ratio. When Underwriting merges the records at
    # source this simply stops matching and the numbers are unchanged.
    rows = merge_rows(rows, 'broker',
                      ('payment', 'reserve', 'premium_fy', 'inforce_book',
                       'claim_count'))
    for r in rows:
        prem = r['premium_fy']
        r['lr_on_reserve'] = round(r['reserve'] / prem, 4) if prem > 0 else None

    rows.sort(key=lambda r: -(r['lr_on_reserve'] if r['lr_on_reserve'] is not None else -1))
    return rows
