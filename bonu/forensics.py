"""
bonu/forensics.py — the over-billing checks.

CFO 2026-08-03: *"lawyers are crooks and they can cheat us."* Fair warning, so these are
the tests a forensic auditor actually runs on a legal bill, in order of how often they
catch something real:

  1. the same matter billed twice          (DUP_MATTER)
  2. the same amount from the same firm on the same day  (DUP_AMOUNT)
  3. a rate above the agreed tariff        (RATE_OVER_TARIFF)
  4. units x rate does not equal the amount charged      (ARITHMETIC)
  5. the lines do not add up to the invoice total        (NOT_FOOTING)
  6. one fee earner billing more than 12h in a day       (IMPOSSIBLE_DAY)
  7. work dated on a Sunday or a public holiday          (NON_WORKING_DAY)
  8. a line far above that firm's own normal for the service  (OUTLIER_VS_OWN)
  9. round-number bias across a firm's bills             (ROUND_NUMBERS)
 10. Benford's law on the leading digit                  (BENFORD)
 11. a matter billed with no BONU member reference       (NO_MEMBER_REF)
 12. work dated before the member joined / outside the invoice period (OUT_OF_PERIOD)

Every rule returns a MONEY-AT-RISK figure, because "suspicious" is not actionable and
"P43,000 billed twice on matter 118/2026" is. Nothing here accuses anybody: each finding
carries the question to put to the firm, which is what a query letter needs.

None of these need AI. The AI pass in `bonu/ai_review.py` runs AFTER them, over the
residue, to spot shapes a rule cannot describe.
"""
from __future__ import annotations

import datetime
from collections import Counter, defaultdict
from decimal import Decimal

ZERO = Decimal('0')
from statistics import median

# Benford's expected share of leading digits 1-9.
BENFORD = {1: .301, 2: .176, 3: .125, 4: .097, 5: .079, 6: .067, 7: .058, 8: .051, 9: .046}
MAX_FEE_EARNER_HOURS_PER_DAY = Decimal('12')
OUTLIER_MULTIPLE = Decimal('4')          # 4x the firm's own median for that service
MIN_LINES_FOR_STATS = 12                 # below this, distribution tests are noise


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def _finding(code, severity, title, detail, amount, question, invoice=None, line=None, firm=None):
    return {'code': code, 'severity': severity, 'title': title, 'detail': detail,
            'amount_at_risk': _d(amount), 'question_for_firm': question,
            'invoice': invoice, 'line': line, 'firm': firm}


# An invoice reconstructed from the general ledger has a firm, a reference, a date and an
# amount — and nothing else, because that is all the books ever held. Rules that need real
# invoice detail must stay silent on those rows rather than "finding" the absence of data we
# never imported. Proven live on 2026-08-03: without this gate the missing-member-reference
# rule fired on all 620 ledger rows and reported P6.6M at risk against P4.8M of actual
# spend — a screen full of noise on day one.
LEDGER_DERIVED_MARKER = 'Odoo GL import'


def _has_line_detail(line) -> bool:
    """False when this row came from the ledger and therefore never had line detail."""
    return LEDGER_DERIVED_MARKER not in (getattr(line.invoice, 'source_file', '') or '')


def _sla_fee_findings(out, fee, hits, firm):
    """Is this agreed monthly fee charged once per month, or more than once?"""
    months = sorted({(l.service_date or l.invoice.invoice_date).strftime('%Y-%m')
                     for l in hits if (l.service_date or l.invoice.invoice_date)})
    charged, distinct = len(hits), len(months)
    if charged > distinct:
        # The same month's fee charged more than once is money straight back.
        out.append(_finding(
            'SLA_FEE_TWICE_IN_A_MONTH', 'high',
            f'The {float(fee):,.0f} monthly fee was charged {charged} times over '
            f'{distinct} month(s)',
            f'The agreed monthly fee appears {charged} times but only {distinct} distinct '
            f'month(s) are covered ({", ".join(months[:8])}). Anything beyond one per month is a '
            f'duplicate of a fee we have already paid.',
            fee * (charged - distinct),
            f'The monthly fee of {float(fee):,.0f} appears {charged} times for {distinct} '
            f'month(s). Please confirm which months these relate to and credit any duplicate.',
            firm=firm, line=hits[0]))
    else:
        out.append(_finding(
            'SLA_FEE_AS_AGREED', 'low',
            f'Monthly fee of {float(fee):,.0f} charged for {distinct} month(s) — as agreed',
            f'{", ".join(months[:12])}. This is an agreed SLA fee, not an over-charge. What is '
            f'worth checking is the caseload it was meant to buy.',
            ZERO, '', firm=firm, line=hits[0]))


def run_rules(lines, holidays=frozenset(), retainer_fees=None):
    """Run every rule over an iterable of BonuInvoiceLine (with invoice+firm loaded).

    Returns a list of finding dicts, worst money first. Pure function over the rows it
    is given — no queries, no writes — so it is trivially testable and can be run over
    a single invoice or the whole year.
    """
    lines = list(lines)
    out = []
    if not lines:
        return out
    # A firm's agreed monthly SLA fee is NOT an over-charge, however far it sits above their
    # usual per-matter figure. CFO 2026-08-03: "the 40,000 monthly its a sla we have with the
    # legal firm to manage 40 clients cost control." Before this, the recurring fee was the
    # single largest 'finding' on the screen — the system was accusing a firm of over-billing
    # for charging exactly what we agreed to pay it.
    #
    # ONE FIRM CAN HOLD SEVERAL SLAs. CFO, same day: "the same legal firm has two SLAs. One is
    # for south for 40,000 BWP and somewhere around 85,000 to manage north." So this is a LIST
    # of fees per firm — keyed on a single fee, the second agreement would still have been
    # reported as over-billing.
    fees_by_firm = {}
    for k, v in (retainer_fees or {}).items():
        vals = v if isinstance(v, (list, tuple, set, frozenset)) else [v]
        fees_by_firm[str(k)] = sorted({_d(x) for x in vals if _d(x) > 0})

    def _matching_fee(line):
        """The agreed fee this line equals, or None. Several SLAs per firm are normal."""
        for fee in fees_by_firm.get(str(line.invoice.firm_id), ()):
            if abs(_d(line.amount) - fee) <= Decimal('0.05'):
                return fee
        return None

    def _is_retainer_fee(line):
        return _matching_fee(line) is not None

    by_firm = defaultdict(list)
    for l in lines:
        by_firm[l.invoice.firm_id].append(l)

    # ── 1. the same matter billed twice ─────────────────────────────────────────
    seen_matter = defaultdict(list)
    for l in lines:
        if l.matter_ref and l.service_date:
            seen_matter[(l.invoice.firm_id, l.matter_ref.strip().lower(),
                         l.service_date, l.service_code.strip().lower())].append(l)
    for key, group in seen_matter.items():
        if len(group) > 1:
            dup = sum(_d(x.amount) for x in group[1:])
            out.append(_finding(
                'DUP_MATTER', 'high',
                f'Matter {group[0].matter_ref} billed {len(group)} times for the same day',
                f'{len(group)} lines on matter {group[0].matter_ref}, service date '
                f'{group[0].service_date}, same service code. Amounts: '
                + ', '.join(f'{float(x.amount):,.2f}' for x in group),
                dup,
                f'Matter {group[0].matter_ref} carries {len(group)} charges for work on '
                f'{group[0].service_date}. Please confirm these are separate pieces of work '
                f'and not the same item billed more than once.',
                invoice=group[0].invoice, line=group[1], firm=group[0].invoice.firm))

    # ── 2. identical amount, same firm, same day ────────────────────────────────
    same_amt = defaultdict(list)
    for l in lines:
        if l.service_date and _d(l.amount) > 0:
            same_amt[(l.invoice.firm_id, l.service_date, _d(l.amount))].append(l)
    for (firm_id, day, amt), group in same_amt.items():
        if len(group) >= 3 and len({(x.matter_ref or '').lower() for x in group}) == 1:
            out.append(_finding(
                'DUP_AMOUNT', 'medium',
                f'{len(group)} identical charges of {float(amt):,.2f} on {day}',
                f'Same firm, same day, same matter, same amount, {len(group)} times.',
                amt * (len(group) - 1),
                f'There are {len(group)} charges of exactly {float(amt):,.2f} on {day} for the '
                f'same matter. Please break down what each one covers.',
                invoice=group[0].invoice, firm=group[0].invoice.firm))

    # ── 3. rate above the agreed tariff ─────────────────────────────────────────
    for l in lines:
        firm = l.invoice.firm
        tariff = getattr(firm, 'agreed_hourly_rate', None)
        if l.basis == 'hourly' and tariff and l.rate and _d(l.rate) > _d(tariff):
            over = (_d(l.rate) - _d(tariff)) * _d(l.units or 1)
            out.append(_finding(
                'RATE_OVER_TARIFF', 'high',
                f'Charged {float(l.rate):,.2f}/h against an agreed {float(tariff):,.2f}/h',
                f'Matter {l.matter_ref or "(none)"}, {float(l.units or 0)} units.',
                over,
                f'This line is charged at {float(l.rate):,.2f} per hour. Our agreed panel rate '
                f'is {float(tariff):,.2f}. Please re-issue at the agreed rate or point us to the '
                f'written variation.',
                invoice=l.invoice, line=l, firm=firm))

    # ── 4. units x rate does not equal the amount ───────────────────────────────
    for l in lines:
        ok = l.arithmetic_ok
        if ok is False:
            diff = _d(l.amount) - _d(l.recomputed)
            out.append(_finding(
                'ARITHMETIC', 'high' if diff > 0 else 'low',
                'Units x rate does not equal the amount charged',
                f'{float(l.units or 0)} x {float(l.rate or 0)} = {float(l.recomputed or 0):,.2f}, '
                f'but the line says {float(l.amount):,.2f}.',
                diff if diff > 0 else 0,
                f'Line {l.line_no} shows {float(l.units or 0)} units at {float(l.rate or 0):,.2f} '
                f'but is charged {float(l.amount):,.2f}. Please correct or explain.',
                invoice=l.invoice, line=l, firm=l.invoice.firm))

    # ── 5. the invoice does not foot ─────────────────────────────────────────────
    for inv in {l.invoice_id: l.invoice for l in lines}.values():
        if inv.lines.exists() and inv.foots is False:
            gap = inv.line_total - _d(inv.subtotal)
            out.append(_finding(
                'NOT_FOOTING', 'high',
                f'Invoice {inv.invoice_number} does not add up',
                f'Lines total {float(inv.line_total):,.2f} against a stated subtotal of '
                f'{float(inv.subtotal):,.2f}.',
                abs(gap),
                f'Invoice {inv.invoice_number}: the itemised lines come to '
                f'{float(inv.line_total):,.2f} but the invoice claims '
                f'{float(inv.subtotal):,.2f}. Please re-issue.',
                invoice=inv, firm=inv.firm))

    # ── 6. more than 12 billed hours in one day by one fee earner ───────────────
    per_earner_day = defaultdict(lambda: [Decimal('0'), []])
    for l in lines:
        if l.basis == 'hourly' and l.fee_earner and l.service_date and l.units:
            k = (l.invoice.firm_id, l.fee_earner.strip().lower(), l.service_date)
            per_earner_day[k][0] += _d(l.units)
            per_earner_day[k][1].append(l)
    for (firm_id, earner, day), (hours, group) in per_earner_day.items():
        if hours > MAX_FEE_EARNER_HOURS_PER_DAY:
            excess_h = hours - MAX_FEE_EARNER_HOURS_PER_DAY
            rate = _d(group[0].rate) or Decimal('0')
            out.append(_finding(
                'IMPOSSIBLE_DAY', 'high',
                f'{group[0].fee_earner} billed {float(hours):.1f} hours in one day',
                f'{len(group)} lines on {day} totalling {float(hours):.1f} hours.',
                excess_h * rate,
                f'{group[0].fee_earner} is billed for {float(hours):.1f} hours of work on {day}. '
                f'Please provide the time records supporting a day of that length.',
                invoice=group[0].invoice, firm=group[0].invoice.firm))

    # ── 7. work dated on a Sunday or public holiday ─────────────────────────────
    for l in lines:
        # On a ledger-derived row the only date we have is when the bill was POSTED, not
        # when the work was done — flagging that as "worked on a Sunday" is meaningless.
        if not _has_line_detail(l):
            continue
        if not l.service_date:
            continue
        if l.service_date.weekday() == 6 or l.service_date in holidays:
            why = 'a Sunday' if l.service_date.weekday() == 6 else 'a public holiday'
            out.append(_finding(
                'NON_WORKING_DAY', 'low',
                f'Work dated on {why} ({l.service_date})',
                f'Matter {l.matter_ref or "(none)"}, {float(l.amount):,.2f}.',
                l.amount,
                f'This line is dated {l.service_date}, which is {why}. Please confirm the work was '
                f'genuinely done then — court and registry work on that date is unlikely.',
                invoice=l.invoice, line=l, firm=l.invoice.firm))

    # ── 8. a line far above that firm's own normal for the service ──────────────
    per_service = defaultdict(list)
    for l in lines:
        if _d(l.amount) > 0:
            per_service[(l.invoice.firm_id, (l.service_code or '').strip().lower())].append(l)
    for key, group in per_service.items():
        # The yardstick is the firm's usual PER-MATTER charge, so the agreed monthly fees must
        # come out of the sample as well as out of the flagging. Left in, six SLA fees dragged
        # one firm's median from 4,700 to 22,350 and a genuine 120,000 charge stopped showing up
        # at all — the suppression would have hidden real over-billing (caught by test, 2026-08-03).
        sample = [x for x in group if not _is_retainer_fee(x)]
        if len(sample) < MIN_LINES_FOR_STATS:
            continue
        med = _d(median([float(x.amount) for x in sample]))
        if med <= 0:
            continue
        # An amount that recurs is a PATTERN, not eleven separate outliers. Jeremiah Tladi
        # billed exactly 40,000 on eleven bills; emitting one card each buried the actual
        # insight — a firm on an 85,000 monthly retainer also invoicing 40,000 a month — under
        # eleven identical cards (live, 2026-08-03). Group by amount and say how often.
        outliers = defaultdict(list)
        for l in group:
            if _is_retainer_fee(l):
                continue          # the agreed monthly fee, not an outlier
            if _d(l.amount) > med * OUTLIER_MULTIPLE:
                outliers[_d(l.amount)].append(l)
        for amount, hits in sorted(outliers.items(), key=lambda kv: -kv[0]):
            multiple = float(amount / med)
            refs = ', '.join(sorted({(h.invoice.invoice_number or h.matter_ref or '?')
                                     for h in hits})[:6])
            more = '' if len(hits) <= 6 else f' and {len(hits) - 6} more'
            times = ('once' if len(hits) == 1 else f'{len(hits)} times')
            recurring = len(hits) >= 3
            out.append(_finding(
                'OUTLIER_VS_OWN', 'high' if recurring else 'medium',
                (f'{float(amount):,.2f} charged {times} — against this firm\'s usual '
                 f'{float(med):,.2f} for {group[0].service_code or "this service"}'),
                (f'{multiple:.1f}x the firm\'s own median across {len(sample)} comparable lines '
                 f'(the agreed monthly fees are left out of that average). '
                 f'On {refs}{more}. '
                 + ('An amount this size repeating monthly looks like a standing charge rather '
                    'than a one-off matter — check it is not work already covered by a retainer.'
                    if recurring else
                    'A single charge well above this firm\'s own normal.')),
                (amount - med) * len(hits),
                (f'You charged {float(amount):,.2f} {times} against your own usual '
                 f'{float(med):,.2f} for this service'
                 + ('. Please confirm what this recurring charge covers and whether any part of '
                    'it is already included in the monthly fee.' if recurring
                    else '. What made this matter different?')),
                invoice=hits[0].invoice, line=hits[0], firm=hits[0].invoice.firm))

    # ── 8b. the SLA fee itself: charged the right number of times? ──────────────
    # The fee is expected, so the question is not "why so much" but "how many months".
    for firm_id, group in by_firm.items():
        for fee in fees_by_firm.get(str(firm_id), ()):
            hits = [l for l in group if abs(_d(l.amount) - fee) <= Decimal('0.05')]
            if not hits:
                continue
            _sla_fee_findings(out, fee, hits, group[0].invoice.firm)
    # ── 9. round-number bias ────────────────────────────────────────────────────
    for firm_id, group in by_firm.items():
        amounts = [_d(x.amount) for x in group if _d(x.amount) > 0]
        if len(amounts) < MIN_LINES_FOR_STATS:
            continue
        rounds = sum(1 for a in amounts if a % 500 == 0)
        share = rounds / len(amounts)
        if share >= 0.5:
            out.append(_finding(
                'ROUND_NUMBERS', 'medium',
                f'{share*100:.0f}% of this firm\'s charges are exact multiples of 500',
                f'{rounds} of {len(amounts)} lines. Genuine time-based billing rarely lands on '
                f'round numbers this often — it suggests estimated rather than recorded time.',
                sum(a for a in amounts if a % 500 == 0) * Decimal('0.1'),
                'Most of your charges are round multiples of 500. Please supply the underlying '
                'time records rather than rounded estimates.',
                firm=group[0].invoice.firm))

    # ── 10. Benford's law on the leading digit ──────────────────────────────────
    for firm_id, group in by_firm.items():
        amounts = [float(x.amount) for x in group if _d(x.amount) >= 10]
        if len(amounts) < 60:            # Benford is meaningless on small samples
            continue
        lead = Counter(int(str(int(a))[0]) for a in amounts)
        n = sum(lead.values())
        worst_d, worst_gap = None, 0.0
        for dgt, exp in BENFORD.items():
            gap = (lead.get(dgt, 0) / n) - exp
            if gap > worst_gap:
                worst_d, worst_gap = dgt, gap
        if worst_gap > 0.12:
            out.append(_finding(
                'BENFORD', 'low',
                f'Leading digit {worst_d} appears {worst_gap*100:.0f} points more often than expected',
                f'Across {n} charges. On its own this proves nothing — it is a reason to sample '
                f'this firm\'s files, not an accusation.',
                0,
                '',
                firm=group[0].invoice.firm))

    # ── 11. no member reference ─────────────────────────────────────────────────
    # Only where a reference could have been captured. A ledger-derived row never had one.
    for l in lines:
        if not _has_line_detail(l):
            continue
        if not (l.member_ref or '').strip():
            out.append(_finding(
                'NO_MEMBER_REF', 'high',
                'Billed with no BONU member reference',
                f'Matter {l.matter_ref or "(none)"}, {float(l.amount):,.2f}. Without a member '
                f'reference we cannot prove this work was for a covered member at all.',
                l.amount,
                f'This line carries no BONU member reference. Which member was this work for?',
                invoice=l.invoice, line=l, firm=l.invoice.firm))

    # ── 12. service date outside the invoice period ─────────────────────────────
    for l in lines:
        inv = l.invoice
        if not (l.service_date and inv.period_start and inv.period_end):
            continue
        if not (inv.period_start <= l.service_date <= inv.period_end):
            out.append(_finding(
                'OUT_OF_PERIOD', 'medium',
                f'Work dated {l.service_date} on an invoice for '
                f'{inv.period_start} to {inv.period_end}',
                f'Matter {l.matter_ref or "(none)"}, {float(l.amount):,.2f}. Either the date is '
                f'wrong or this was already billed in another period.',
                l.amount,
                f'This line is dated {l.service_date} but the invoice covers '
                f'{inv.period_start} to {inv.period_end}. Was it billed in an earlier invoice?',
                invoice=inv, line=l, firm=inv.firm))

    out.sort(key=lambda f: (-float(f['amount_at_risk']),
                            {'high': 0, 'medium': 1, 'low': 2}[f['severity']]))
    return out


def summarise(findings):
    """Headline counts for the dashboard tiles."""
    by_code = Counter(f['code'] for f in findings)
    by_sev = Counter(f['severity'] for f in findings)
    return {
        'total': len(findings),
        'high': by_sev.get('high', 0),
        'medium': by_sev.get('medium', 0),
        'low': by_sev.get('low', 0),
        'amount_at_risk': float(sum(f['amount_at_risk'] for f in findings)),
        'by_code': dict(by_code),
    }
