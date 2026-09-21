"""
underwriting/adoption.py

Who actually USES the underwriting automation — and who is still doing the job
by hand (CFO Amendment 3, 2026-09-08).

Two tools are measured:
  * the QUOTE BUILDER          — underwriting.models.Quote
  * the DOCUMENT GENERATOR     — underwriting.models.UnderwritingDocument
                                 (WCA certificates and the cover notes beside them)

NO NEW TABLE. Both tools already stamp the person who used them, on the row the
tool produced: `Quote.underwriter` is set by QuoteViewSet.perform_create from
the authenticated caller and can never be taken from the payload, and
`UnderwritingDocument.issued_by` is set the same way in the issue action. Both
models are AuditableMixin, so core.AuditLog carries a second copy of the same
fact. A `UwToolUse` event table would therefore be a third recording of
something already recorded twice — and it would only start counting from the
day it deployed, where these two tables answer "who used the quote builder, how
often, when last" back to the day each tool went live. Counting what is there
is the house rule (reuse first) and it is also the only route that has history.

The roster — needed for the half of the question that matters, "who has NEVER
used them" — comes from payroll.Employee: active, non-test, non-archived staff
in the Underwriting department who hold an Omni login. A person with no rows in
either table still appears, with zeroes.

Renewals (the denominator of the readiness score) come from Graphite V2 over
the EXISTING read-only bridge, `aware.engine.run_select_params`. Nothing here
opens a connection of its own and no credential appears in this file. That one
query is isolated in `graphite_renewals()` so the logic around it is testable
with the query faked — which is how it is tested, because the bridge only
reaches the replica from inside the production VPC.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db.models import Count, Max
from django.utils import timezone

from .models import Quote, UnderwritingDocument

log = logging.getLogger(__name__)

#: A department is "Underwriting" if its name contains this. Matched loosely on
#: purpose — the register carries 'Underwriting', 'Underwriting & Pricing' and
#: 'Underwriting Dept' for the same team, and none of those should drop a person
#: off a report management reads as the whole team.
DEPARTMENT_HINT = 'underwriting'

#: The UW manager, copied on every underwriter's Monday email (CFO Amendment 3,
#: 2026-09-08: "each underwriter individually, with the UW manager copied").
MANAGER_JOB_TITLES = {'underwriting manager', 'head of underwriting'}

#: Graphite's own words for a renewal in policy_actions.transaction_type.
RENEWAL_TRANSACTION_TYPES = ('RENEW', 'ANNIVERSARY-RENEW')

#: Domestic & Commercial only. MIS (the retail-store book) and Unicoin are out
#: of scope, per the CFO's decision on 8 Sep 2026 — those renew by feed, not by
#: an underwriter sitting down with a file, so counting them would invent
#: manual work nobody does.
DOM_COM_POLICY_PREFIXES = ('DOMG', 'DOMD', 'COMG', 'COMD')

#: How far back "last week" reaches.
WINDOW_DAYS = 7


def _norm(value) -> str:
    """Collapse whitespace and case — for matching a department or a name."""
    return ' '.join(str(value or '').split()).lower()


# ── the window ───────────────────────────────────────────────────────────────
def last_week_window(now=None, days: int = WINDOW_DAYS):
    """The reporting window: [now - days, now).

    A rolling seven days rather than a calendar week, because the Monday email
    is asking "what did you do since the last one" and a Monday-to-Sunday
    calendar week silently drops whatever happened on the Monday morning the
    mail goes out.

    HALF-OPEN, [start, end): a record stamped exactly `days` ago is the FIRST
    record in the window and counts; a second earlier and it is out; a record
    stamped `end` itself belongs to next week's email. One rule, applied by the
    single `created_at >= start AND created_at < end` filter in `_tally`, and
    tested at both edges — an overlapping or gapped boundary would let a quote
    be counted in two weekly emails or in none.
    """
    end = now or timezone.now()
    return end - timedelta(days=days), end


# ── the roster ───────────────────────────────────────────────────────────────
def roster():
    """Underwriting staff with an Omni login, newest-irrelevant, name order.

    Returns [{user_id, name, email, job_title, is_manager}]. Excludes QA/test
    records and archived leavers — Oprah's leave spot-check (2026-08-10) found
    automation accounts sitting on staff reports, and this is a report a manager
    reads as a list of people.
    """
    from payroll.models import Employee

    rows = []
    for emp in (Employee.objects
                .filter(status=Employee.Status.ACTIVE,
                        user__isnull=False,
                        is_test_record=False,
                        is_archived=False)
                .select_related('user')
                .order_by('full_name')):
        if DEPARTMENT_HINT not in _norm(emp.department):
            continue
        rows.append({
            'user_id':   emp.user_id,
            'name':      (emp.full_name or emp.user.get_full_name()
                          or emp.user.username).strip(),
            'email':     (emp.email or emp.user.email or '').strip(),
            'job_title': (emp.job_title or '').strip(),
            'is_manager': _norm(emp.job_title) in MANAGER_JOB_TITLES,
        })
    return rows


def manager_emails(people=None) -> list:
    """The UW manager address(es) to cc. Empty when nobody holds the title —
    the email still goes to the underwriter; a missing manager must not stop
    the send (and the report says the cc list was empty)."""
    return [p['email'] for p in (people if people is not None else roster())
            if p['is_manager'] and p['email']]


# ── usage, counted off the rows the tools already write ──────────────────────
def _tally(model, user_field: str, start, end):
    """(uses in window, last use ever) per user id, for one tool.

    Two aggregates, not a query per person: this report runs for the whole
    department. `last used` is deliberately ALL-TIME — a person who last built
    a quote in June should see June, not a blank that reads like "never".
    """
    base = model.objects.filter(**{f'{user_field}__isnull': False})
    in_window = dict(
        base.filter(created_at__gte=start, created_at__lt=end)
            .values_list(user_field).annotate(n=Count('id')).values_list(user_field, 'n'))
    last_ever = dict(
        base.values_list(user_field).annotate(m=Max('created_at'))
            .values_list(user_field, 'm'))
    return in_window, last_ever


def usage(start, end, people=None):
    """Per-underwriter tool usage for the window.

    Returns [{...roster fields, quotes, quotes_last_used, documents,
              documents_last_used, ever_used}] — one row per person on the
    roster INCLUDING the people with nothing at all, because those are the rows
    the CFO opens this screen to see.
    """
    people = roster() if people is None else people
    q_win, q_last = _tally(Quote, 'underwriter_id', start, end)
    d_win, d_last = _tally(UnderwritingDocument, 'issued_by_id', start, end)

    rows = []
    for p in people:
        uid = p['user_id']
        quotes = int(q_win.get(uid, 0))
        docs = int(d_win.get(uid, 0))
        rows.append({
            **p,
            'quotes': quotes,
            'quotes_last_used': q_last.get(uid),
            'documents': docs,
            'documents_last_used': d_last.get(uid),
            'ever_used': bool(q_last.get(uid) or d_last.get(uid)),
        })
    return rows


# ── the AI readiness score ───────────────────────────────────────────────────
def readiness_score(eligible: int, tool_assisted: int):
    """AI readiness for ONE underwriter, 0-100. None when it cannot be scored.

    THE FORMULA (CFO Amendment 3, 2026-09-08 — per underwriter, not one company
    number):

        score = 100 x min(tool_assisted, eligible) / eligible

        eligible      = renewals booked by that person in the window, Domestic &
                        Commercial only (Graphite V2). This is the work the
                        Quote builder could have done for them.
        tool_assisted = quotes that person actually built in the Quote builder
                        in the same window.

    Capped at the denominator so the score is a percentage of the job, not a
    league table you can win by drafting spare quotes — 100 means "everything
    eligible went through the tool", and nothing means more than that.

    eligible == 0 -> None, never 0. A person with no renewals to do has not
    failed to use anything, and a zero there would be a lie the CFO would be
    asked to act on. The screen and the email show the inputs (X renewals, Y
    quotes) beside the number so nobody has to trust the number alone.

    Documents (WCA / cover notes) are NOT in this ratio. A WCA certificate is
    not a renewal, so putting it in the numerator would inflate the score with
    unrelated work. Document usage is reported in its own column instead.
    """
    eligible = int(eligible or 0)
    if eligible <= 0:
        return None
    assisted = min(max(int(tool_assisted or 0), 0), eligible)
    return round(100 * assisted / eligible)


def readiness_band(score) -> str:
    """Plain words for the number, so a non-coder can read the column."""
    if score is None:
        return 'Not scored'
    if score >= 80:
        return 'Using the tools'
    if score >= 40:
        return 'Partly manual'
    return 'Still manual'


# ── the ONE Graphite query ───────────────────────────────────────────────────
def graphite_renewals(start, end) -> dict:
    """Renewals booked in [start, end), per producing person, D&C only.

    The ONLY function in this module that touches Graphite. Runs over the
    existing read-only bridge (`aware.engine.run_select_params`) — the same door
    the Aware reports and underwriting/renewal_lookup.py already use. No DSN, no
    credential and no new connection lives here.

    Attribution is by the STAFF NAME on `policies.agent_id` (Graphite's own
    `users` table). Graphite has no column for "the Omni underwriter", so the
    producing user is the closest honest answer; where that name does not match
    a person on the roster the renewal still counts in `team_total` and lands in
    `unattributed`, which is what the team-total fallback is for. Staff first
    and last names only cross the wire — the shared guard rejects any
    personal-data column, `email` included, so the match is on name.

    Returns {'available', 'by_person', 'team_total', 'unattributed', 'note'}.
    `available` False means the replica could not be reached from here; every
    caller must then say the renewal figures are unavailable rather than print a
    zero that reads like "you renewed nothing".
    """
    like = ' OR '.join(["p.policyNumber LIKE %s"] * len(DOM_COM_POLICY_PREFIXES))
    placeholders = ', '.join(['%s'] * len(RENEWAL_TRANSACTION_TYPES))
    sql = (
        "SELECT u.firstName AS first_name, u.lastName AS last_name, "
        "COUNT(*) AS n "
        "FROM policy_actions pa "
        "JOIN policies p ON p.id = pa.policy_id "
        "LEFT JOIN users u ON u.id = p.agent_id "
        "WHERE pa.status = 'ISSUED' "
        f"AND pa.transaction_type IN ({placeholders}) "
        "AND pa.transaction_date >= %s AND pa.transaction_date < %s "
        f"AND ({like}) "
        "GROUP BY u.firstName, u.lastName"
    )
    params = (list(RENEWAL_TRANSACTION_TYPES)
              + [start, end]
              + [f'{pfx}%' for pfx in DOM_COM_POLICY_PREFIXES])

    try:
        from aware.engine import run_select_params
        rows = run_select_params(sql, params)
    except Exception as e:                       # noqa: BLE001
        # The replica is only reachable from inside the production VPC, and a
        # weekly email must not die because a report cannot see it. Fail OPEN
        # with `available: False` and let the caller say so in words.
        log.warning('underwriting adoption: Graphite renewals unavailable: %s', e)
        return {'available': False, 'by_person': {}, 'team_total': 0,
                'unattributed': 0,
                'note': 'Renewal figures were not available from Graphite.'}

    by_person, team_total, unattributed = {}, 0, 0
    for r in rows or []:
        n = int(r.get('n') or 0)
        team_total += n
        key = _norm(f"{r.get('first_name') or ''} {r.get('last_name') or ''}")
        if key:
            by_person[key] = by_person.get(key, 0) + n
        else:
            unattributed += n
    return {'available': True, 'by_person': by_person, 'team_total': team_total,
            'unattributed': unattributed, 'note': ''}


# ── the report both the screen and the email are built from ──────────────────
def weekly_report(now=None, days: int = WINDOW_DAYS, renewals_fn=None) -> dict:
    """Everything the adoption screen and the Monday email need, once.

    `renewals_fn(start, end)` is injected so the surrounding logic is testable
    without the Graphite replica (which is not reachable outside production).
    Defaults to the real query.
    """
    start, end = last_week_window(now=now, days=days)
    people = roster()
    rows = usage(start, end, people=people)
    renewals = (renewals_fn or graphite_renewals)(start, end)

    attributed = 0
    for row in rows:
        mine = int(renewals['by_person'].get(_norm(row['name']), 0))
        attributed += mine
        row['renewals'] = mine
        # The gap IS the point the CFO wants made: work that went through by
        # hand which the tool would have done in a fraction of the time.
        row['gap'] = max(mine - row['quotes'], 0)
        row['readiness'] = readiness_score(mine, row['quotes'])
        row['readiness_band'] = readiness_band(row['readiness'])

    team = {
        'renewals_available': renewals['available'],
        'renewals_note':      renewals['note'],
        'renewals_total':     renewals['team_total'],
        # Renewals Graphite could not tie to anybody on the roster. Pre-agreed
        # fallback (CFO Amendment 3): report the team total and say so, rather
        # than quietly attributing them or dropping them.
        'renewals_unattributed': (renewals['team_total'] - attributed
                                  if renewals['available'] else 0),
        'quotes_total':       sum(r['quotes'] for r in rows),
        'documents_total':    sum(r['documents'] for r in rows),
        'headcount':          len(rows),
        'never_used':         sum(1 for r in rows if not r['ever_used']),
    }
    # THE TEAM FIGURE IS WEIGHTED BY RENEWALS, not a plain average of the row
    # scores (/qctest 2026-09-08, confirmed by Fable 5.1).
    #
    # It used to be sum(scores)/len(scores). On 30 days of real data that read
    # 50 / 100 while 17 of 1,181 renewals had actually gone through the Quote
    # builder — 1.4%. Two underwriters with ONE renewal each scored 100 and
    # outweighed a colleague who did 151 by hand, and the same behaviour read 0
    # over 7 days and 50 over 30. A tile the CFO acts on cannot be 48 points out.
    #
    # Weighting by each person's renewals fixes all three faults at once: the
    # tile reconciles to the rows beneath it, one renewal moves it by one
    # renewal's worth, and it stays steady as the window changes. The per-person
    # cap survives inside the numerator, so no one can lift the team by building
    # spare quotes.
    scored = [r for r in rows if r['readiness'] is not None]
    weight = sum(r['renewals'] for r in scored)
    team['readiness_avg'] = (
        round(100 * sum(min(r['quotes'], r['renewals']) for r in scored) / weight)
        if weight else None
    )
    # The two figures the tile is worked out from, so the number can always be
    # checked against them rather than trusted.
    team['readiness_numerator'] = sum(min(r['quotes'], r['renewals']) for r in scored)
    team['readiness_denominator'] = weight
    team['scored_headcount'] = len(scored)

    return {
        'window': {'start': start, 'end': end, 'days': days},
        'rows': rows,
        'team': team,
    }
