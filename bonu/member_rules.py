"""
bonu/member_rules.py — the same member, again and again.

CFO 2026-08-03: *"how can we know which member is a fraud."*

Every rule in `forensics.py` looks at ONE invoice, or one firm's habits. None of them can see
the pattern that costs a legal-benefit scheme the most money: **one member using the benefit
over and over**, often through a different firm each time so no single bill ever looks odd.

That question is answerable without knowing who anybody is, because it only needs to know that
two bills concern the SAME person — which is what the member token gives us. Three checks:

  MEMBER_OVER_ANNUAL_LIMIT  past the 90,000 a member is allowed in a calendar year — a breach of
                        cover, not a judgement call
  MEMBER_MANY_MATTERS   one member on several separate matters in a year
  MEMBER_MULTIPLE_FIRMS one member using two or more firms — the classic shape, because each
                        firm only sees its own file and assumes it is a first claim
  MEMBER_REPEAT_TYPE    the same member, the same kind of case, twice

Every finding names the token, never a person, and every one is phrased as a question for the
scheme administrator to check against the membership — because a member with three genuine
matters is unlucky, not a fraud, and the system must not confuse the two.

**Still missing, and it is the big one:** whether that member's premium was actually PAID, and
whether they were on cover on the day the work was done. That lives in Graphite, not here.
`unmatched_members()` below is what hands the list over for that check.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

Z = Decimal('0')
ZERO = Z          # spelled out where a finding deliberately carries no money
MANY_MATTERS = 3           # three separate matters in a year is worth a look
MULTI_FIRM_MIN = 2         # two firms for one member is the pattern that matters

# CFO 2026-08-03: "the total admissible legal cost per member is 90,000 BWP in total per year,
# we need to flag if it goes above". A hard benefit limit, and the first rule in this module
# that is a breach of cover rather than a question of judgement — past this line the scheme
# is paying for something it does not owe.
#
# THE YEAR IS THE CALENDAR YEAR — 1 January to 31 December (CFO, 3 August 2026). I first built
# this as a rolling twelve months to be safe against an unknown year-end; the CFO has now given
# the actual definition, so the breach test is the calendar year and nothing else. A benefit
# limit is a contractual line, and measuring it over a window the contract does not use would
# produce breaches that cannot be enforced.
#
# The rolling figure is kept, but only as a WATCH: spend that clears the limit across a
# year-end without breaching either single year is worth seeing, and is reported as such rather
# than as a breach.
MEMBER_ANNUAL_LIMIT = Decimal('90000')
ROLLING_DAYS = 365


def annual_limit():
    """The limit, overridable in settings without a code change."""
    try:
        from django.conf import settings
        return Decimal(str(getattr(settings, 'BONU_MEMBER_ANNUAL_LIMIT', MEMBER_ANNUAL_LIMIT)))
    except BaseException:      # noqa: BLE001
        return MEMBER_ANNUAL_LIMIT


def spend_by_calendar_year(rows):
    """{2025: 120000, 2026: 40000} — the benefit year, 1 January to 31 December.

    An undated row is charged to the invoice's year, and if even that is missing it goes to a
    `None` bucket that is reported rather than dropped: money with no date cannot be silently
    excluded from a benefit limit.
    """
    out = {}
    for r in rows:
        d = r.service_date or getattr(r.invoice, 'invoice_date', None)
        year = d.year if d else None
        out[year] = out.get(year, Z) + (r.amount or Z)
    return out


def worst_rolling_year(rows):
    """The costliest 12-month window for one member: (spend, start, end).

    A sliding window over the dated rows. Anything undated is added in, because leaving money
    out of a benefit-limit check would understate the breach.
    """
    dated = sorted(((r.service_date or r.invoice.invoice_date), (r.amount or Z))
                   for r in rows if (r.service_date or r.invoice.invoice_date))
    undated = sum((r.amount or Z) for r in rows
                  if not (r.service_date or r.invoice.invoice_date))
    if not dated:
        return undated, None, None
    best, best_span = Z, (dated[0][0], dated[0][0])
    start = 0
    running = Z
    for end in range(len(dated)):
        running += dated[end][1]
        while (dated[end][0] - dated[start][0]).days > ROLLING_DAYS:
            running -= dated[start][1]
            start += 1
        if running > best:
            best = running
            best_span = (dated[start][0], dated[end][0])
    return best + undated, best_span[0], best_span[1]


def _finding(code, severity, title, detail, amount, question, firm=None, line=None):
    return {'code': code, 'severity': severity, 'title': title, 'detail': detail,
            'amount_at_risk': amount, 'question_for_firm': question,
            'firm': firm, 'line': line, 'invoice': getattr(line, 'invoice', None)}


def run_member_rules(lines):
    """Findings about members rather than about firms. Pure function over the rows given."""
    lines = [l for l in lines if (l.member_token or '').strip()]
    out = []
    if not lines:
        return out

    by_member = defaultdict(list)
    for l in lines:
        by_member[l.member_token].append(l)

    limit = annual_limit()

    for tokn, rows in by_member.items():
        matters = {(r.invoice.firm_id, r.matter_ref or r.invoice.invoice_number) for r in rows}
        firms = {r.invoice.firm_id: r.invoice.firm for r in rows}
        spend = sum((r.amount or Z) for r in rows)
        dates = sorted(r.service_date or r.invoice.invoice_date for r in rows
                       if (r.service_date or r.invoice.invoice_date))
        window = f'{dates[0]} to {dates[-1]}' if dates else 'dates not recorded'

        # ── over the annual benefit limit, per CALENDAR year ────────────────────────
        by_year = spend_by_calendar_year(rows)
        breached_a_year = False
        for year, spent in sorted(by_year.items(), key=lambda kv: -(kv[1])):
            if spent <= limit:
                continue
            breached_a_year = True
            over = spent - limit
            when = f'in {year}' if year else 'on bills with no date'
            out.append(_finding(
                'MEMBER_OVER_ANNUAL_LIMIT', 'high',
                f'{year or "Undated"}: over the {float(limit):,.0f} limit by {float(over):,.0f}',
                f'Member {tokn} cost {float(spent):,.2f} {when} across {len(matters)} matter(s) '
                f'and {len(firms)} firm(s). The admissible legal cost is {float(limit):,.0f} per '
                f'member per year (1 January to 31 December), so {float(over):,.2f} is above what '
                f'the benefit covers.',
                over,
                f'Member {tokn} passed the {float(limit):,.0f} legal benefit limit {when}. '
                f'Please confirm which matters were authorised after the limit was reached.',
                firm=list(firms.values())[0], line=rows[0]))

        # A watch, not a breach: clears the limit across a year-end without breaching a year.
        rolling, w_from, w_to = worst_rolling_year(rows)
        if not breached_a_year and rolling > limit:
            out.append(_finding(
                'MEMBER_LIMIT_STRADDLES_YEAR', 'medium',
                f'{float(rolling):,.0f} in twelve months, but inside the limit each year',
                f'Member {tokn} cost {float(rolling):,.2f} between {w_from} and {w_to}, which is '
                f'above {float(limit):,.0f} — but split across two calendar years, so neither '
                f'year breaches on its own. Correct under the benefit as written; worth seeing '
                f'because it is what deliberate spreading would look like.',
                Z,
                '',
                firm=list(firms.values())[0], line=rows[0]))

        if len(firms) >= MULTI_FIRM_MIN:
            names = ', '.join(sorted(f.name for f in firms.values()))
            out.append(_finding(
                'MEMBER_MULTIPLE_FIRMS', 'high',
                f'One member used {len(firms)} different firms',
                f'Member {tokn} appears on {len(matters)} matter(s) across {len(firms)} firms '
                f'({names}), {window}, totalling {float(spend):,.2f}. Each firm only sees its '
                f'own file, so none of them can tell this is a repeat claim.',
                # No money attached ON PURPOSE. Using a member's whole spend here double-counted
                # against the limit breach for the same person and inflated the weekly headline
                # (P1.54M when the defensible figure was P756k). "Money at risk" has to mean
                # money we could actually get back; this rule raises a question, and the severity
                # carries its weight.
                ZERO,
                f'Please confirm whether member {tokn} disclosed an existing matter with '
                f'another firm when instructing you.',
                firm=list(firms.values())[0], line=rows[0]))

        if len(matters) >= MANY_MATTERS:
            out.append(_finding(
                'MEMBER_MANY_MATTERS', 'medium',
                f'One member on {len(matters)} separate matters',
                f'Member {tokn}: {len(matters)} matters, {window}, totalling '
                f'{float(spend):,.2f}. Not wrong on its own — a member can be unlucky — but '
                f'worth checking against what the benefit actually covers.',
                ZERO,          # a question, not money owed — see MEMBER_MULTIPLE_FIRMS above
                '',
                firm=list(firms.values())[0], line=rows[0]))

        by_type = defaultdict(set)
        for r in rows:
            if r.matter_type and r.matter_type != 'other':
                by_type[r.matter_type].add(r.matter_ref or r.invoice.invoice_number)
        for mtype, refs in by_type.items():
            if len(refs) >= 2:
                out.append(_finding(
                    'MEMBER_REPEAT_TYPE', 'medium',
                    f'Same member, same kind of case, {len(refs)} times',
                    f'Member {tokn} has {len(refs)} separate {mtype} matters ({", ".join(sorted(refs))}). '
                    f'A second case of the same type for one member is either a genuinely new '
                    f'matter or the first one being billed twice under a new reference.',
                    Z,
                    f'Please confirm the {mtype} matters {", ".join(sorted(refs))} are separate '
                    f'instructions and not a continuation of the same case.',
                    firm=list(firms.values())[0], line=rows[0]))

    out.sort(key=lambda f: (-float(f['amount_at_risk'] or 0), f['code']))
    return out


def member_summary(lines):
    """How much of the spend can be tied to a member at all — the honest headline.

    If most spend has no member behind it, every member-based check is running on a fraction
    of the money and the screen must say so rather than implying full coverage.
    """
    lines = list(lines)
    total = sum((l.amount or Z) for l in lines)
    with_token = [l for l in lines if (l.member_token or '').strip()]
    tokens = {l.member_token for l in with_token}
    tied = sum((l.amount or Z) for l in with_token)

    by_member = defaultdict(lambda: {'matters': set(), 'firms': set(), 'spend': Z, 'rows': []})
    for l in with_token:
        m = by_member[l.member_token]
        m['matters'].add(l.matter_ref or l.invoice.invoice_number)
        m['firms'].add(l.invoice.firm_id)
        m['spend'] += (l.amount or Z)
        m['rows'].append(l)

    top = sorted(
        ({'member': k, 'matters': len(v['matters']), 'firms': len(v['firms']),
          'spend': v['spend']} for k, v in by_member.items()),
        key=lambda r: -r['spend'])[:25]

    limit = annual_limit()
    over_limit = []
    for tokn, v in by_member.items():
        for year, spent in spend_by_calendar_year(v['rows']).items():
            if spent > limit:
                over_limit.append({'member': tokn, 'year': year, 'spend': spent,
                                   'over': spent - limit,
                                   'matters': len(v['matters']), 'firms': len(v['firms'])})
    over_limit.sort(key=lambda r: -r['over'])

    return {
        'annual_limit': limit,
        'members_over_limit': len(over_limit),
        'amount_over_limit': sum(r['over'] for r in over_limit),
        'over_limit': over_limit[:25],
        'total_spend': total,
        'spend_tied_to_a_member': tied,
        'coverage_pct': (tied / total * 100).quantize(Decimal('0.1')) if total else None,
        'members_identified': len(tokens),
        'members_on_multiple_firms': sum(1 for v in by_member.values() if len(v['firms']) > 1),
        'members_with_many_matters': sum(1 for v in by_member.values()
                                         if len(v['matters']) >= MANY_MATTERS),
        'top_members': top,
        'limit_note': (f'The admissible legal cost is {float(limit):,.0f} per member per benefit '
                       f'year, and the year is 1 January to 31 December (CFO, 3 August 2026).'),
        'note': ('Members are identified by a one-way token, not a name — the same person '
                 'always produces the same token and the token cannot be turned back into a '
                 'name. Whether their premium was paid is not known here: that needs the '
                 'membership lookup in Graphite.'),
    }


def unmatched_members(lines):
    """The list to hand to the premium check: every member token and what it cost us.

    This is deliberately the LAST function in the module, because it is the handover point for
    the guardrail the CFO actually asked for — "control the members who don't pay". Matching a
    token to a policy, and that policy to whether the premium was paid on the service date, is
    a Graphite lookup. Until that exists, this at least says exactly which members to check
    and how much money is riding on each one.
    """
    s = member_summary(lines)
    return [{'member': r['member'], 'spend': float(r['spend']), 'matters': r['matters'],
             'firms': r['firms']} for r in s['top_members']]
