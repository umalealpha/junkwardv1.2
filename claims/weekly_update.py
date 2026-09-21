"""claims/weekly_update.py — the weekly claims update, read from the mirror.

WHY THIS EXISTS (B1, requested by Bokani Makosha). Finance rebuild this by hand
every week out of the Graphite "Claims As On Date (Inception-to-Date)" report:
export, paste, derive the group from the policy number, pivot by month, total
the reserve and the payment columns, email it. Every one of those steps is
mechanical.

WHAT THIS IS *NOT*. It is NOT a date-of-loss report. The month a claim lands in
is the month it was REPORTED (``GraphiteClaim.registered_date``), not the month
the accident happened. The two differ by weeks on a third of the book, and a
reader who assumes date-of-loss will conclude the wrong thing about a month.

INCEPTION-TO-DATE, AND WHAT THAT COSTS US. ``GraphiteClaim`` is overwritten
every night with today's position — it carries no history. So this report can
only ever say "as things stand now, grouped by the month each claim was
reported". That is exactly what an inception-to-date report means, so it is
right; but it also means last month's run can never be reproduced. Nobody should
be told these figures are a snapshot of a past date, because they are not.

THE GROUP IS DERIVED, BECAUSE GRAPHITE HAS NO GROUP COLUMN. It comes off the
leading letters of the policy number. That derivation is the one place this
module can silently lie, so an unrecognised prefix is REPORTED BY NAME and never
folded into an "Other" pile — see ``group_for``.

This module READS. It raises nothing, posts nothing, moves nothing.
"""
from __future__ import annotations

import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from django.db import DatabaseError
from django.utils import timezone

#: Policy-number prefix -> the group Finance report by. There is no Group column
#: in Graphite; this map IS the definition. Longest prefix wins, so COMG is
#: tested before any shorter commercial prefix could swallow it.
PREFIX_GROUPS: Dict[str, str] = {
    'COMG': 'Commercial',
    'DOMG': 'Domestic',
    'MIS':  'Miscellaneous',
    'BONU': 'Bonus',
}

#: Label used for a claim we could not group. It is deliberately NOT "Other":
#: "Other" reads as a decision somebody made, and this is the absence of one.
UNKNOWN_GROUP = 'UNRECOGNISED PREFIX'

#: Label for a claim with no reported date at all. Same reasoning: it is shown,
#: never quietly dropped into the current month.
UNKNOWN_MONTH = 'No reported date'

#: How many recent weeks the covering email calls out as newly reported.
DEFAULT_WEEKS = 1

#: Upper bound on one run, so the mirror can never be streamed whole by accident.
MAX_ROWS = 100_000


class SourceUnavailable(RuntimeError):
    """We could not look. Never to be reported as "there were no claims"."""


def _prefix_token(policy_number: str) -> str:
    """The leading run of letters, upper-cased. '' when there are none.

    Used only to NAME an unrecognised prefix in the report. Digits are cut off
    so 'COMD2024129965' is reported as 'COMD' and not as the whole number, which
    would make every unrecognised policy look like its own distinct problem.
    """
    s = (policy_number or '').strip().upper()
    out = []
    for ch in s:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return ''.join(out)


def group_for(policy_number: str) -> Tuple[str, bool]:
    """(group label, recognised?) for a policy number.

    THE SECOND ELEMENT IS THE WHOLE POINT. Bucketing an unknown prefix into
    "Other" is how a whole product line goes missing from a weekly report for
    months: the totals still foot, the sheet still looks complete, and nobody
    can see that 400 claims stopped being Commercial. So an unrecognised prefix
    comes back flagged, the caller counts it, and the report NAMES it.

    Longest match first: 'COMG' must never be decided by a shorter entry.
    """
    s = (policy_number or '').strip().upper()
    if not s:
        return UNKNOWN_GROUP, False
    for prefix in sorted(PREFIX_GROUPS, key=len, reverse=True):
        if s.startswith(prefix):
            return PREFIX_GROUPS[prefix], True
    return UNKNOWN_GROUP, False


def month_of(claim) -> Tuple[str, bool]:
    """(month key 'YYYY-MM', dated?) for a claim, off the REPORTED date.

    Botswana time. ``registered_date`` is a plain date and needs no conversion,
    which is the safe path. The fallback does: ``graphite_created_at`` is a
    timestamp stored in UTC, and taking ``.month`` off it directly puts every
    claim reported after 22:00 Gaborone into the following month. It is
    converted with ``timezone.localtime`` first, and settings.TIME_ZONE is
    Africa/Gaborone.

    Measured on production 2026-09-13: 604 of 4,600 mirrored claims have no
    ``registered_date`` at all. They are given ``UNKNOWN_MONTH`` rather than
    today's month — a claim with no reported date is a data problem to raise,
    not a September claim.
    """
    d = getattr(claim, 'registered_date', None)
    if d:
        return f'{d:%Y-%m}', True
    created = getattr(claim, 'graphite_created_at', None)
    if created:
        return f'{timezone.localtime(created).date():%Y-%m}', True
    return UNKNOWN_MONTH, False


def _money(v) -> Decimal:
    """An amount as money, to the thebe.

    An unreadable figure becomes zero only as a last resort, and the caller
    counts those: a silent zero understates a reserve, which is the one
    direction an insurer must never drift in.
    """
    if v is None or v == '':
        return Decimal('0.00')
    try:
        return Decimal(str(v)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def mirror_is_reporting() -> Dict[str, Any]:
    """Is the claims mirror carrying real figures at all?

    THIS EXISTS BECAUSE THE FIGURES ARRIVE SEPARATELY FROM THE CLAIMS.
    ``pull_graphite_claims`` writes the claim; ``total_reserve`` and
    ``total_payment`` only arrive with the DETAIL sync, stamped on
    ``detail_synced_at``. If the detail sync stops, every claim is still there,
    the report still builds, every column still foots — and every money figure
    in it is 0.00. It reads as a quiet month. It is a broken feed.

    Coverage on production 2026-09-13: 4,600 of 4,600 claims detail-synced
    (100%), 3,782 with a non-zero reserve. So the guard is not firing today; it
    is here for the morning the sync dies.

    Returns {'reporting', 'claims', 'detailed', 'coverage'}. ``reporting`` is
    False when there are no claims at all, or when claims exist and not one of
    them has ever had its detail pulled.
    """
    from integrations.models import GraphiteClaim

    try:
        claims = GraphiteClaim.objects.count()
        detailed = GraphiteClaim.objects.filter(
            detail_synced_at__isnull=False).count()
    except DatabaseError as exc:  # pragma: no cover - database is down
        raise SourceUnavailable(str(exc)) from exc
    return {
        'reporting': claims > 0 and detailed > 0,
        'claims': claims,
        'detailed': detailed,
        'coverage': (detailed / claims) if claims else 0.0,
    }


def rows(limit: int = MAX_ROWS) -> List[Dict[str, Any]]:
    """Every mirrored claim, flattened to what the report needs.

    Inception-to-date: there is no date filter here on purpose. The window in
    this report is the MONTH COLUMN, not a WHERE clause.
    """
    from integrations.models import GraphiteClaim

    try:
        qs = (GraphiteClaim.objects
              .only('claim_number', 'policy_number', 'registered_date',
                    'graphite_created_at', 'total_reserve', 'total_payment',
                    'status', 'detail_synced_at')
              .order_by('registered_date', 'policy_number')[:limit])
        claims = list(qs)
    except DatabaseError as exc:  # pragma: no cover - database is down
        raise SourceUnavailable(str(exc)) from exc

    out: List[Dict[str, Any]] = []
    for c in claims:
        group, recognised = group_for(c.policy_number)
        month, dated = month_of(c)
        out.append({
            'claim_number':   c.claim_number or '',
            'policy_number':  c.policy_number or '',
            'group':          group,
            'group_known':    recognised,
            'prefix':         _prefix_token(c.policy_number),
            'month':          month,
            'month_known':    dated,
            'reserve':        _money(c.total_reserve),
            'payment':        _money(c.total_payment),
            'status':         c.status or '',
            'registered_date': c.registered_date,
            'detail_synced':  c.detail_synced_at is not None,
        })
    return out


def summarise(claim_rows: List[Dict[str, Any]],
              weeks: int = DEFAULT_WEEKS,
              today: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Table 1 (group x month, reserve and payment) plus what must be confessed.

    ``table`` is the report. ``unrecognised`` and ``undated`` are the two ways
    this report can be wrong without looking wrong, so they are returned as
    first-class figures and printed — not logged and forgotten.
    """
    today = today or timezone.localdate()
    since = today - datetime.timedelta(weeks=max(1, int(weeks)))

    table: Dict[Tuple[str, str], Dict[str, Any]] = {}
    by_group: Dict[str, Dict[str, Any]] = {}
    unrecognised: Dict[str, Dict[str, Any]] = {}
    undated = 0
    no_detail = 0
    reserve_total = Decimal('0.00')
    payment_total = Decimal('0.00')
    recent = 0

    for r in claim_rows:
        key = (r['group'], r['month'])
        slot = table.setdefault(key, {
            'group': r['group'], 'month': r['month'], 'count': 0,
            'reserve': Decimal('0.00'), 'payment': Decimal('0.00'),
        })
        slot['count'] += 1
        slot['reserve'] += r['reserve']
        slot['payment'] += r['payment']

        g = by_group.setdefault(r['group'], {
            'group': r['group'], 'count': 0,
            'reserve': Decimal('0.00'), 'payment': Decimal('0.00'),
        })
        g['count'] += 1
        g['reserve'] += r['reserve']
        g['payment'] += r['payment']

        reserve_total += r['reserve']
        payment_total += r['payment']

        if not r['group_known']:
            # Named, counted, and carrying an example policy number so whoever
            # reads it can go and look the thing up rather than guess.
            u = unrecognised.setdefault(r['prefix'] or '(no prefix)', {
                'prefix': r['prefix'] or '(no prefix)', 'count': 0,
                'example': r['policy_number'] or '(blank policy number)',
                'reserve': Decimal('0.00'), 'payment': Decimal('0.00'),
            })
            u['count'] += 1
            u['reserve'] += r['reserve']
            u['payment'] += r['payment']
        if not r['month_known']:
            undated += 1
        if not r['detail_synced']:
            no_detail += 1
        if r['registered_date'] and r['registered_date'] >= since:
            recent += 1

    def _month_sort(k):
        # UNKNOWN_MONTH sorts last; it is not a date and must not pretend to be.
        return (1, '') if k[1] == UNKNOWN_MONTH else (0, k[1])

    return {
        'count': len(claim_rows),
        'reserve': reserve_total,
        'payment': payment_total,
        'table': [table[k] for k in sorted(table, key=lambda k: (_month_sort(k), k[0]))],
        'by_group': sorted(by_group.values(),
                           key=lambda s: (-s['reserve'], s['group'])),
        'unrecognised': sorted(unrecognised.values(),
                               key=lambda s: (-s['count'], s['prefix'])),
        'unrecognised_count': sum(u['count'] for u in unrecognised.values()),
        'undated': undated,
        'no_detail': no_detail,
        'recent': recent,
        'weeks': max(1, int(weeks)),
        'since': since,
        'as_at': today,
    }
