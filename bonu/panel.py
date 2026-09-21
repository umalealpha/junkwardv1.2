"""
bonu/panel.py — measuring the panel, not just the invoice.

Three things the forensic rules cannot tell you, because each one needs a view ACROSS
invoices rather than inside one:

**1. The league table.** Which firm is expensive, which one is slow, which one folds the
moment it is asked a question. A firm that concedes half of what we query is telling you
something about the other half. This is the table you take into a rate negotiation, and it
also answers the cheaper daily question: who gets the next case.

**2. Early warning per case.** A divorce that has cost double what our divorces normally
cost is worth a phone call at 2x. Discovering it at 5x is just accounting.

**3. What the open cases will cost us.** Open matters times typical cost is an estimate of
legal spend already incurred and not yet billed. Without it BONU surprises you at month
end. This is the number that later feeds the ledger — nothing here posts anything.

Every figure is computed from our OWN history. No benchmark is imported from anywhere,
because a Botswana panel rate is not a published number.
"""
from __future__ import annotations

from decimal import Decimal
from django.utils import timezone

Z = Decimal('0')
MIN_SAMPLE = 4          # fewer than this and "typical" is one case wearing a hat
# 'other' is not a case type, it is the absence of one. Taking a median across every
# unclassified matter and then calling individual cases "2x normal" against it is noise
# dressed as a finding — live on 2026-08-03 it declared 163 cases hot on a single bucket
# of 620 unclassified ledger rows. Unclassified spend gets counted, never compared.
UNCLASSIFIED = 'other'


def _median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2


def typical_cost_by_matter_type(lines, thresholds=None):
    """What a case of each type normally costs US, from our own bills.

    Median per matter, not mean: one runaway litigation would drag a mean up and then
    every other case would look cheap by comparison. An agreed figure on an approval
    limit always wins over the computed one.
    """
    per_matter = {}
    for l in lines:
        key = (l.matter_type, l.matter_ref or f'line-{l.pk}')
        per_matter[key] = per_matter.get(key, Z) + (l.amount or Z)

    by_type = {}
    for (mtype, _ref), total in per_matter.items():
        by_type.setdefault(mtype, []).append(total)

    out = {}
    for mtype, totals in by_type.items():
        comparable = mtype != UNCLASSIFIED and len(totals) >= MIN_SAMPLE
        out[mtype] = {
            'matters': len(totals),
            'typical': _median(totals) if comparable else None,
            'basis': ('our own median' if comparable
                      else ('case type was never set — nothing to compare against'
                            if mtype == UNCLASSIFIED else 'too few cases to say')),
            'total_spend': sum(totals, Z),
        }

    for th in (thresholds or []):
        row = out.setdefault(th.matter_type, {'matters': 0, 'typical': None,
                                              'basis': '', 'total_spend': Z})
        if th.typical_cost is not None:
            row['typical'] = th.typical_cost
            row['basis'] = 'agreed figure on the approval limit'
        row['approval_above'] = th.approval_above
        row['warn_multiple'] = th.warn_multiple
    return out


def case_early_warnings(lines, thresholds=None):
    """Matters running hot against their own case type. Cheapest possible early warning.

    Returns one row per matter, worst first, each with a plain sentence a person can act
    on. A matter with no comparable history is reported as "no comparison yet" rather than
    quietly dropped — an unmeasurable case is exactly the kind that runs away.
    """
    typical = typical_cost_by_matter_type(lines, thresholds)
    th_by_type = {t.matter_type: t for t in (thresholds or [])}

    per_matter = {}
    for l in lines:
        ref = l.matter_ref or ''
        if not ref:
            continue
        key = (l.invoice.firm_id, ref)
        row = per_matter.setdefault(key, {
            'firm_id': l.invoice.firm_id, 'firm': l.invoice.firm.name, 'matter_ref': ref,
            'matter_type': l.matter_type, 'member_ref': l.member_ref, 'spend': Z, 'lines': 0,
            'first_seen': None, 'last_seen': None,
        })
        row['spend'] += (l.amount or Z)
        row['lines'] += 1
        for field, better in (('first_seen', min), ('last_seen', max)):
            if l.service_date:
                cur = row[field]
                row[field] = l.service_date if cur is None else better(cur, l.service_date)

    out = []
    for row in per_matter.values():
        t = typical.get(row['matter_type']) or {}
        tc = t.get('typical')
        th = th_by_type.get(row['matter_type'])
        warn_at = Decimal(str(th.warn_multiple)) if th else Decimal('2.00')

        row['typical_cost'] = tc
        row['approval_above'] = th.approval_above if th else None
        row['multiple'] = (row['spend'] / tc).quantize(Decimal('0.01')) if tc else None
        row['over_approval'] = bool(th and row['spend'] > th.approval_above)
        row['running_hot'] = bool(tc and row['multiple'] is not None and row['multiple'] >= warn_at)

        if row['over_approval'] and row['multiple']:
            row['message'] = (f"{row['matter_ref']} has reached {row['spend']:,.0f} — "
                              f"{row['multiple']}x what a {row['matter_type']} case normally costs "
                              f"us, and past the {th.approval_above:,.0f} approval limit.")
        elif row['over_approval']:
            row['message'] = (f"{row['matter_ref']} has reached {row['spend']:,.0f}, past the "
                              f"{th.approval_above:,.0f} approval limit for this case type.")
        elif row['running_hot']:
            row['message'] = (f"{row['matter_ref']} is at {row['multiple']}x the normal cost of a "
                              f"{row['matter_type']} case ({row['spend']:,.0f} against "
                              f"{tc:,.0f}). Worth a call now.")
        elif row['matter_type'] == UNCLASSIFIED:
            # Silent on purpose: 620 unclassified rows would otherwise produce 620 shrugs.
            row['message'] = ''
        elif tc is None:
            row['message'] = (f"{row['matter_ref']}: we have too few {row['matter_type']} cases to "
                              f"say what normal looks like, so this one cannot be compared yet.")
        else:
            row['message'] = ''
        out.append(row)

    out.sort(key=lambda r: (r['multiple'] is None, -(r['multiple'] or Z), -r['spend']))
    return out


def league_table(firms, lines, cases=None, queries=None, as_of=None):
    """One row per firm: what they cost, how fast they move, how they answer a query.

    'Concede rate' is deliberately included and deliberately labelled: it is the share of
    queried money the firm gave back. A high rate is not proof of dishonesty, but it is
    proof that querying that firm pays — which is the decision this table has to support.
    """
    as_of = as_of or timezone.localdate()
    cases = list(cases or [])
    queries = list(queries or [])

    rows = []
    for firm in firms:
        f_lines = [l for l in lines if l.invoice.firm_id == firm.pk]
        matters = {l.matter_ref for l in f_lines if l.matter_ref}
        spend = sum((l.amount or Z) for l in f_lines)

        hourly = [l for l in f_lines if l.basis == 'hourly' and l.units and l.rate]
        hours = sum((l.units or Z) for l in hourly)
        eff_rate = (sum((l.amount or Z) for l in hourly) / hours).quantize(Decimal('0.01')) \
            if hours else None

        f_cases = [c for c in cases if c.firm_id == firm.pk]
        closed = [c for c in f_cases if c.closed_on and c.instructed_on]
        days_to_close = _median([(c.closed_on - c.instructed_on).days for c in closed])
        open_cases = [c for c in f_cases if c.is_open]
        quiet = [c for c in open_cases
                 if (c.days_quiet(as_of) or 0) > 30]

        f_q = [q for q in queries if q.firm_id == firm.pk]
        queried = sum((q.amount_queried or Z) for q in f_q)
        conceded = sum((q.amount_conceded or Z) for q in f_q)
        answered = [q for q in f_q if q.replied_on and q.sent_on]

        rows.append({
            'firm_id': str(firm.pk),
            'firm': firm.name,
            'agreed_hourly_rate': firm.agreed_hourly_rate,
            'spend': spend,
            'matters': len(matters),
            'cost_per_matter': (spend / len(matters)).quantize(Decimal('0.01')) if matters else None,
            'effective_hourly_rate': eff_rate,
            'over_tariff': bool(eff_rate and firm.agreed_hourly_rate
                                and eff_rate > firm.agreed_hourly_rate),
            'open_cases': len(open_cases),
            'cases_gone_quiet': len(quiet),
            'median_days_to_close': days_to_close,
            'queries_raised': len(f_q),
            'amount_queried': queried,
            'amount_conceded': conceded,
            'concede_rate': (conceded / queried * 100).quantize(Decimal('0.1')) if queried else None,
            'median_days_to_answer': _median([(q.replied_on - q.sent_on).days for q in answered]),
        })

    # Most expensive per matter first — that is the column a negotiation starts from.
    rows.sort(key=lambda r: -(r['cost_per_matter'] or Z))
    return rows


def unbilled_estimate(cases, lines, thresholds=None, as_of=None):
    """What the open cases are likely to cost, less what has already been billed on them.

    This is an ESTIMATE and says so in its own output. It exists so that month-end is not
    the first time anyone hears about legal spend already incurred. It posts nothing.
    """
    as_of = as_of or timezone.localdate()
    typical = typical_cost_by_matter_type(lines, thresholds)

    billed_by_case = {}
    for l in lines:
        if l.matter_ref:
            key = (l.invoice.firm_id, l.matter_ref)
            billed_by_case[key] = billed_by_case.get(key, Z) + (l.amount or Z)

    rows, total, unmeasurable = [], Z, 0
    for c in cases:
        if not c.is_open:
            continue
        tc = (typical.get(c.matter_type) or {}).get('typical')
        billed = billed_by_case.get((c.firm_id, c.case_ref), Z)
        if tc is None:
            unmeasurable += 1
            continue
        remaining = tc - billed
        if remaining < Z:
            remaining = Z            # already cost more than typical; do not accrue negative
        total += remaining
        rows.append({
            'case_ref': c.case_ref, 'firm_id': str(c.firm_id), 'matter_type': c.matter_type,
            'status': c.status, 'typical_cost': tc, 'billed_so_far': billed,
            'still_to_come': remaining, 'days_open': (as_of - c.instructed_on).days,
        })

    rows.sort(key=lambda r: -r['still_to_come'])
    return {
        'as_of': as_of.isoformat(),
        'open_cases': sum(1 for c in cases if c.is_open),
        'estimated': total,
        'cases_priced': len(rows),
        'cases_not_priced': unmeasurable,
        'caveat': ('An estimate from our own median cost per case type, less what has already '
                   'been billed. It is not a journal and nothing here posts to the ledger.'),
        'rows': rows[:60],
    }


def unconfirmed_classification(lines):
    """How much of the spend rests on a case type nobody confirmed.

    A filter by case type is only trustworthy if this number is small, so it is reported
    next to every spend-by-type table rather than left for someone to wonder about.
    """
    total = sum((l.amount or Z) for l in lines)
    guessed = sum((l.amount or Z) for l in lines
                  if l.matter_type_source in ('ai', 'default'))
    return {
        'total': total,
        'unconfirmed': guessed,
        'unconfirmed_pct': (guessed / total * 100).quantize(Decimal('0.1')) if total else None,
        'note': ('Case types marked as an AI suggestion or never set. Confirm these before '
                 'quoting spend by case type.'),
    }
