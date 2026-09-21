"""realpay/failed_debits.py — the weekly failed-debits chase list, by agent.

WHY THIS EXISTS (CFO 2026-09-11, off Rose Mokgware's 11-Sep email "ALPHA DIRECT
FAILED DEBITS"). Every week an Accounts Assistant exported the failed debit
orders by hand, pasted them into a spreadsheet, and emailed Underwriting and the
account handlers to go and chase the clients. The export and the email are
mechanical. Only the chasing is not.

WHAT THIS IS *NOT*. Omni already runs a failed-debits report daily
(``reporting.finance_monitoring.build_failed_debits``, Keetile Mokhendo's pack,
17-Aug-2026) which emails counts and a link to the UniCoin debtors pair. That
one is not replaced and is not duplicated — this module CALLS it for the data,
including its bank-code triage, so there is one definition of "a failed debit"
and one place where a status code is interpreted. What this adds is the three
things Rose's email had and that report does not:

  1. the AGENT who owns the policy, so the list can be split by who must phone;
  2. the agent-sold book only (see below);
  3. a spreadsheet a person can work down.

SCOPE — THE AGENT-SOLD BOOK, and that is a deliberate exclusion. Rose's real
file is COMG / DOMG / DOM policies: commercial and domestic business written by
a named agent who can be asked to phone the client. The Instant/MIS book fails
in the thousands every week (every one of the twenty most recent failures on
live is MIS) and nobody chases those one by one; they are a cancellation-rules
problem. Mixed in, they would bury the eighteen rows somebody can act on and the
email would stop being read within a month.

NO CUSTOMER CONTACT DETAILS. Rose's spreadsheet carried the client's name and
email address. This carries neither, for two reasons that happen to agree: the
read-only Graphite user is not granted the contact columns at all, and the
people receiving this already hold the client relationship. The policy number is
what they type into Graphite to act.

This module READS. It raises nothing, posts nothing, moves nothing.
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from django.utils import timezone

from integrations import graphite_ro

#: The books a person actually chases. Matched on the leading letters of the
#: policy number, which is how Graphite separates them — the same prefix rule
#: ``graphite_feed`` already uses for broker commission.
CHASEABLE_PREFIXES = ('COM', 'DOM')

#: How far back a weekly run looks. Seven days, so consecutive Friday runs meet
#: exactly: no day covered twice, and none skipped.
DEFAULT_DAYS = 7

#: Upper bound on one run, so a widened window can never stream the whole table.
MAX_ROWS = 5000


class GraphiteUnavailable(RuntimeError):
    """We could not look. Never to be reported as "there were none"."""


def window(days: int = DEFAULT_DAYS, today: Optional[datetime.date] = None):
    """(start, end) inclusive of both ends, in Botswana time.

    Botswana, because "last week" is a business week in Gaborone. The job fires
    at 12:00 UTC — 14:00 there — and on UTC that is still the same date, but a
    manual run late in the evening would silently shift the window by a day.
    """
    end = today or timezone.localdate()
    return end - datetime.timedelta(days=int(days) - 1), end


def is_chaseable(policy_number: str) -> bool:
    """True for the commercial/domestic book, false for Instant/MIS.

    Checked on the row rather than in SQL because the underlying query belongs
    to the daily report, which must keep covering the whole book.
    """
    return (policy_number or '').upper().startswith(CHASEABLE_PREFIXES)


def _agents_for(policy_numbers: List[str], chunk: int = 500) -> Dict[str, Dict[str, str]]:
    """{policyNumber: {'agent': ..., 'agency': ...}} for the given policies.

    The agent is ``policies.agent_id -> users.id``. The AGENCY is
    ``policies.agency_id``, NOT the agent's own agency: those two disagree on
    21,059 policies, and taking the agent's agency is the documented way to
    attribute a policy to the wrong broker (aware/tests_broker_join.py).

    The read-only Graphite user is granted only id, agency_id, firstName and
    lastName on ``users`` — there is no route from here to a contact detail even
    by accident.
    """
    out: Dict[str, Dict[str, str]] = {}
    if not policy_numbers or not graphite_ro.is_configured():
        return out
    uniq = sorted({p for p in policy_numbers if p})
    for i in range(0, len(uniq), chunk):
        batch = uniq[i:i + chunk]
        ph = ','.join(['%s'] * len(batch))
        rows = graphite_ro.query(
            f"""SELECT p.policyNumber AS pn, p.premium AS premium,
                       u.firstName AS fn, u.lastName AS ln, ag.name AS agency
                FROM policies p
                LEFT JOIN users    u  ON u.id  = p.agent_id
                LEFT JOIN agencies ag ON ag.id = p.agency_id
                WHERE p.policyNumber IN ({ph})""",
            batch, limit=len(batch))
        for r in (rows or []):
            name = ' '.join(x for x in ((r.get('fn') or '').strip(),
                                        (r.get('ln') or '').strip()) if x)
            out[r['pn']] = {
                'agent': name,
                'agency': (r.get('agency') or '').strip(),
                'premium': r.get('premium'),
            }
    return out


#: Statuses meaning RealPay came back with an ANSWER for a debit. These are
#: RealPay's own codes, confirmed in writing by Nedine Olivier-Vorster
#: (Technical Account Manager) on 2026-09-10 — not inferred:
#:   A=FUTURE · E=ERROR · F=FAILED · I=CANCELLED · R=RETRY · S=SUCCESSFUL · W=PROCESSING
_OUTCOME_STATUSES = ('S', 'F', 'E', 'R', 'W')
#: FUTURE. On a date already PAST, a row still sitting at 'A' is not "scheduled"
#: — it is a debit whose result never came back.
_NO_OUTCOME = 'A'


def book_is_reporting(days: int = DEFAULT_DAYS,
                      today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Is the failure feed for this book alive at all?

    THIS EXISTS BECAUSE THE ANSWER TODAY IS NO. Measured on the live replica
    2026-09-11: the commercial/domestic book held 312 September instalments,
    every one status 'A' (FUTURE) on a date already past — no result ever
    returned — and no success or failure recorded since early June. Over the
    same eleven days the Instant book logged 3,336 successes and 2,666 failures.

    Pramod Bisen (ADRisk IT) reported the identical finding independently on
    2026-09-10: **RealPay's instalment advice for DOM/COM stopped on 3 June
    2026 at 16:45** and has been exactly zero since, while MIS never missed a
    beat. The feed had been running at 6,400–7,400 advices a month. Those
    products are still collecting — 661 successful DOMG and 234 successful COMG
    in the last 30 days — but the successes arrive by a different route, and
    **the advice feed is the one that carries the failures**. So every DOM/COM
    debit that failed since 3 June has left no record anywhere in Graphite.

    Without this check the weekly email would say "0 failed debits" every
    Friday — for a week whose real list had eighteen worth P31,016 — and it
    would read as good news. A silent false all-clear on money owed is worse
    than no report, so the job refuses to send rather than reassure.

    Returns {'reporting', 'outcomes', 'no_outcome'}. ``reporting`` is False when
    the book has debits due in the window and not one result came back.
    """
    if not graphite_ro.is_configured():
        raise GraphiteUnavailable('Graphite read-only bridge is not configured.')
    start, end = window(days, today)
    ph = ','.join(['%s'] * len(_OUTCOME_STATUSES))
    rows = graphite_ro.query(
        f"""SELECT SUM(InstalmentStatus IN ({ph})) AS outcomes,
                   SUM(InstalmentStatus = %s)       AS no_outcome
            FROM realpay_contract_installments
            WHERE InstalmentActionDate >= %s AND InstalmentActionDate < %s
              AND (clientNumber LIKE 'COM%%' OR clientNumber LIKE 'DOM%%')""",
        list(_OUTCOME_STATUSES) + [_NO_OUTCOME, start.isoformat(),
                                   (end + datetime.timedelta(days=1)).isoformat()],
        limit=1)
    r = rows[0] if rows else {}
    outcomes = int(r.get('outcomes') or 0)
    no_outcome = int(r.get('no_outcome') or 0)
    return {
        'reporting': outcomes > 0 or no_outcome == 0,
        'outcomes': outcomes,
        'no_outcome': no_outcome,
    }


def _money(v) -> Decimal:
    """An amount as money. Unreadable becomes zero only as a last resort — a
    silent zero understates the total this report exists to raise."""
    if v is None or v == '':
        return Decimal('0.00')
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def failed_rows(days: int = DEFAULT_DAYS,
                today: Optional[datetime.date] = None) -> List[Dict[str, Any]]:
    """The week's chaseable failed debits, biggest first.

    Raises ``GraphiteUnavailable`` when the replica cannot be read, so the
    caller can tell a broken morning from a quiet one.
    """
    from reporting.finance_monitoring import ReplicaUnavailable, build_failed_debits

    start, end = window(days, today)
    try:
        built = build_failed_debits(date_from=start, date_to=end, limit=MAX_ROWS)
    except ReplicaUnavailable as exc:
        raise GraphiteUnavailable(str(exc)) from exc

    rows = [r for r in built['rows'] if is_chaseable(r.get('policy_number'))]
    extra = _agents_for([r['policy_number'] for r in rows])

    out = []
    for r in rows:
        meta = extra.get(r['policy_number'], {})
        out.append({
            'policy_number': r['policy_number'],
            # Stored ISO with a time and a Z; the date is what a reader wants.
            'action_date':   str(r.get('action_date') or '')[:10],
            'amount':        _money(r.get('amount')),
            'retry_count':   int(r.get('retry_count') or 0),
            # Plain English, from the shared bank-code dictionary — not the raw
            # bank string. One definition of what a code means, used by both
            # this and the daily report.
            'reason':        r.get('reason_plain') or '',
            'policy_status': r.get('policy_status') or 'Unknown',
            'premium':       _money(meta.get('premium')),
            'agency':        meta.get('agency', ''),
            'agent':         meta.get('agent', ''),
            # 'call_centre' once it has already been retried and still failed.
            'bucket':        r.get('list_bucket') or '',
        })
    out.sort(key=lambda r: (-r['amount'], r['policy_number']))
    return out


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Headline figures plus the per-agent and per-broker splits.

    ``by_agent`` is who must phone (the covering email). ``by_broker`` is the
    BROKER the policy is attributed to — ``policies.agency_id``, the same
    attribution rule ``aware.modes.broker_analysis`` uses — which is what the
    workbook is now grouped and subtotalled by (CFO 2026-09-12, Bokani
    Makosha's request).
    """
    by_agent: Dict[str, Dict[str, Any]] = {}
    by_broker: Dict[str, Dict[str, Any]] = {}
    total = Decimal('0.00')
    for r in rows:
        total += r['amount']
        who = r['agent'] or 'Unassigned'
        slot = by_agent.setdefault(who, {'agent': who, 'count': 0,
                                         'amount': Decimal('0.00')})
        slot['count'] += 1
        slot['amount'] += r['amount']

        broker = r['agency'] or 'Unassigned'
        bslot = by_broker.setdefault(broker, {'broker': broker, 'count': 0,
                                              'amount': Decimal('0.00')})
        bslot['count'] += 1
        bslot['amount'] += r['amount']
    return {
        'count': len(rows),
        'amount': total,
        # A failure on a policy that is no longer active is a different job:
        # stop the mandate, do not phone the client for money.
        'non_active': sum(1 for r in rows if r['policy_status'] != 'Active'),
        # Already retried and still failing — the ones worth a phone call.
        'repeat': sum(1 for r in rows if r['retry_count'] >= 2),
        'by_agent': sorted(by_agent.values(),
                           key=lambda s: (-s['amount'], s['agent'])),
        'by_broker': sorted(by_broker.values(),
                            key=lambda s: (-s['amount'], s['broker'])),
    }
