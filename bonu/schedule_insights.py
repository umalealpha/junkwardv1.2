"""bonu/schedule_insights.py — the money-protecting reads over the captured BONU
schedule (CFO 2026-08-12).

Everything here is derived from the schedule rows already in Omni (and, for the
gap, the ledger invoices), so it stays true as staff edit the schedule. No writes.

  gap_to_ledger()   — claims in the schedule that never posted to the ledger
  member_limits()   — members near / over the P90k-a-year legal-cover cap
  duplicate_claims()— the same invoice / amount billed twice (pay-twice risk)
  premium_gaps()    — months with no premium booked
  cost_by_firm()    — spend + count per law firm
  cost_by_case()    — spend + count per case type
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from decimal import Decimal

from .models import BonuScheduleRow, BonuScheduleSheet
from .schedule import _money, is_total_row

CLAIMS_KEYS = ('claims',)
OWED_KEYS = ('owed-per-fee-note',)
MEMBER_ANNUAL_LIMIT = Decimal('90000')      # BONU rule, calendar year (see bonu memory)

# Rows whose "client" is not a real member — retainers / block cover / the scheme
# itself — must not be counted against a member's personal limit. Matched on WHOLE
# words, never substrings: a substring 'bonu' would silently drop a real member
# whose surname contains it (e.g. "Mabonu"), switching the P90k control off for them.
_NON_MEMBER_WORDS = frozenset({'retainer', 'cover', 'bonu'})


def _is_non_member(cli: str) -> bool:
    import re
    words = set(re.findall(r'[a-z]+', (cli or '').lower()))
    return bool(words & _NON_MEMBER_WORDS)


def _col(columns, *needles):
    """First column whose label contains ALL of a needle-group (case-insensitive)."""
    for group in needles:
        wants = group if isinstance(group, tuple) else (group,)
        for c in columns:
            cl = c.lower()
            if all(w in cl for w in wants):
                return c
    return ''


def _rows(key):
    """The sheet and its DATA rows.

    The workbook's own 'Totals' line is a row like any other in the file, so it MUST
    be dropped here — every insight is built on top of this one helper, so leaving it
    in double-counted all of them at once. On Kutlo's file it made the schedule total
    read P17,027,141.52 against a real P8,513,570.76, put 'Totals' in the league
    table as the biggest law firm we use, dropped 8.5m of it into '(blank)' case
    type, and booked the premium Totals row as a single 13.46m month of June."""
    sheet = BonuScheduleSheet.objects.filter(key=key).first()
    if not sheet:
        return None, []
    return sheet, [r for r in sheet.rows.all()
                   if not is_total_row(r.cells, sheet.columns)]


def _year(datestr):
    s = (datestr or '').strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d.%m.%Y', '%m/%d/%Y'):
        try:
            return _dt.datetime.strptime(s[:10], fmt).year
        except (ValueError, TypeError):
            continue
    # a bare 4-digit year anywhere
    for tok in s.replace('/', ' ').replace('-', ' ').split():
        if len(tok) == 4 and tok.isdigit():
            return int(tok)
    return None


# --------------------------------------------------------------------------- 1
def gap_to_ledger():
    """Schedule claim invoices whose reference is NOT found on any ledger invoice —
    i.e. detail captured here that the GL does not yet carry. The to-do list."""
    from .models import BonuInvoice
    sheet, rows = _rows('claims')
    if not sheet:
        return {'available': False}
    cols = sheet.columns
    c_ref = _col(cols, ('invoice', 'ref'), 'reference', 'invoice')
    c_firm = _col(cols, ('law', 'firm'), 'firm')
    c_amt = sheet.amount_column or _col(cols, ('inv', 'amount'), 'amount')
    c_cli = _col(cols, ('client',))
    c_month = _col(cols, ('invoice', 'month'), 'month')

    def norm(r):
        return (r or '').strip().upper().replace(' ', '')
    from django.db.models import Sum
    from .models import BonuInvoiceLine
    if not BonuInvoice.objects.exists():
        # CFO 13-Aug-2026: the union's schedule and the ledger are different
        # populations, and BONU now runs off the schedule alone. With no ledger bills,
        # schedule − 0 reported the ENTIRE schedule as a gap — P17,027,141.52 in red
        # on the Insights tab. A zero ledger means "nothing to compare with", never
        # "everything is missing".
        return {'available': False, 'reason': 'no-ledger-invoices'}
    ledger_refs = {norm(n) for n in BonuInvoice.objects.values_list('invoice_number', flat=True) if n}
    ledger_total = BonuInvoiceLine.objects.aggregate(t=Sum('amount'))['t'] or Decimal('0')

    unmatched, unmatched_total, sched_total = [], Decimal('0'), Decimal('0')
    for r in rows:
        amt = _money(r.cells.get(c_amt))
        sched_total += amt
        ref = norm(r.cells.get(c_ref))
        if ref and ref in ledger_refs:
            continue
        unmatched_total += amt
        unmatched.append({'id': str(r.id), 'firm': r.cells.get(c_firm, ''),
                          'ref': r.cells.get(c_ref, ''), 'client': r.cells.get(c_cli, ''),
                          'month': r.cells.get(c_month, ''), 'amount': str(amt)})
    unmatched.sort(key=lambda x: _money(x['amount']), reverse=True)
    # The TRUE headline gap is total schedule claims minus what the ledger holds.
    # The unmatched LIST is best-effort by invoice reference — the ledger's numbers
    # and the schedule's references are in different formats, so it is a worklist to
    # verify, not proof each one is missing.
    total_gap = (sched_total - ledger_total).quantize(Decimal('0.01'))
    return {'available': True, 'schedule_total': str(sched_total.quantize(Decimal('0.01'))),
            'ledger_total': str(ledger_total.quantize(Decimal('0.01'))),
            'total_gap': str(total_gap),
            'ledger_invoices': len(ledger_refs),
            'unmatched_count': len(unmatched),
            'unmatched_total': str(unmatched_total.quantize(Decimal('0.01'))),
            'unmatched': unmatched}


# --------------------------------------------------------------------------- 2
def member_limits(limit: Decimal = MEMBER_ANNUAL_LIMIT, near_pct: Decimal = Decimal('0.8')):
    """Spend per member per calendar year vs the P90k cap. Retainer/cover rows are
    excluded — they are not a member's personal legal cost."""
    sheet, rows = _rows('claims')
    if not sheet:
        return {'available': False}
    cols = sheet.columns
    c_cli = _col(cols, ('client',))
    c_amt = sheet.amount_column or _col(cols, ('inv', 'amount'), 'amount')
    c_date = _col(cols, ('inv', 'date'), 'date')
    totals = defaultdict(lambda: Decimal('0'))          # (client, year) -> amount
    for r in rows:
        cli = (r.cells.get(c_cli) or '').strip()
        if not cli or _is_non_member(cli):
            continue
        yr = _year(r.cells.get(c_date))
        totals[(cli, yr)] += _money(r.cells.get(c_amt))
    over, near = [], []
    for (cli, yr), amt in totals.items():
        row = {'member': cli, 'year': yr, 'total': str(amt.quantize(Decimal('0.01'))),
               'over_by': str((amt - limit).quantize(Decimal('0.01')))}
        if amt > limit:
            over.append(row)
        elif amt >= limit * near_pct:
            near.append(row)
    over.sort(key=lambda x: _money(x['total']), reverse=True)
    near.sort(key=lambda x: _money(x['total']), reverse=True)
    return {'available': True, 'limit': str(limit), 'over': over, 'near': near,
            'over_count': len(over), 'near_count': len(near)}


# --------------------------------------------------------------------------- 3
def duplicate_claims():
    """Same invoice reference, OR same firm+amount+month, appearing more than once
    WITHIN the claims sheet — a pay-twice risk to check before payment.

    Only the claims sheet is scanned: OWED-PER-FEE-NOTE mirrors the same invoices,
    so comparing across the two would flag every single invoice as a 'duplicate'."""
    groups_by_ref = defaultdict(list)
    groups_by_fam = defaultdict(list)
    for key in ('claims',):
        sheet, rows = _rows(key)
        if not sheet:
            continue
        cols = sheet.columns
        c_ref = _col(cols, ('invoice', 'ref'), 'reference', 'invoice')
        c_firm = _col(cols, ('law', 'firm'), 'firm')
        c_amt = sheet.amount_column or _col(cols, ('inv', 'amount'), 'amount')
        c_month = _col(cols, ('invoice', 'month'), 'month')
        for r in rows:
            ref = (r.cells.get(c_ref) or '').strip().upper().replace(' ', '')
            firm = (r.cells.get(c_firm) or '').strip()
            amt = _money(r.cells.get(c_amt))
            month = (r.cells.get(c_month) or '').strip()
            item = {'sheet': key, 'id': str(r.id), 'firm': firm,
                    'ref': r.cells.get(c_ref, ''), 'amount': str(amt), 'month': month}
            if ref:
                # Keyed on (FIRM, reference), not the reference alone. Three separate
                # firms each numbering a fee note '003' is three firms using a simple
                # sequence, not one bill paid three times — and cross-firm groups were
                # inflating 'amount at risk' with money nobody could ever claim back.
                groups_by_ref[(firm.lower(), ref)].append(item)
            if firm and amt and month:
                groups_by_fam[(firm.lower(), str(amt), month.lower())].append(item)

    def dupes(d):
        out = []
        for k, items in d.items():
            if len(items) > 1:
                out.append({'items': items, 'count': len(items),
                            'amount_each': items[0]['amount']})
        out.sort(key=lambda g: _money(g['amount_each']) * g['count'], reverse=True)
        return out
    by_ref = dupes(groups_by_ref)
    by_fam = dupes(groups_by_fam)
    at_risk = sum((_money(g['amount_each']) * (g['count'] - 1) for g in by_ref), Decimal('0'))
    return {'by_reference': by_ref, 'by_firm_amount_month': by_fam,
            'ref_dupe_groups': len(by_ref), 'fam_dupe_groups': len(by_fam),
            'amount_at_risk_ref': str(at_risk.quantize(Decimal('0.01')))}


# --------------------------------------------------------------------------- 4
_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']


def premium_gaps():
    """Which months carry a booked premium and which are missing, off the PREMIUMS
    sheet. A missing month is money not yet recognised."""
    sheet, rows = _rows('premiums')
    if not sheet:
        return {'available': False}
    cols = sheet.columns
    c_month = _col(cols, ('invoice', 'month'), 'month')
    c_amt = sheet.amount_column or _col(cols, ('total', 'premium'), 'premium')
    booked = defaultdict(lambda: Decimal('0'))
    # The month sits on a merged header row and is blank on the deposit rows below
    # it, so carry the last-seen month DOWN — otherwise the amount rows attribute
    # to no month and every month reads "missing".
    last_month = ''
    for r in rows:
        m = (r.cells.get(c_month) or '').strip().lower()[:3]
        if m in _MONTHS:
            last_month = m
        if last_month:
            booked[last_month] += _money(r.cells.get(c_amt))
    present = [m for m in _MONTHS if booked.get(m, Decimal('0')) != 0]
    missing = [m for m in _MONTHS if booked.get(m, Decimal('0')) == 0]
    return {'available': True,
            'months': [{'month': m.title(), 'amount': str(booked.get(m, Decimal('0')).quantize(Decimal('0.01'))),
                        'booked': booked.get(m, Decimal('0')) != 0} for m in _MONTHS],
            'present_count': len(present), 'missing': [m.title() for m in missing]}


# ------------------------------------------------------------------------- 6
def _cost_by(colfinder):
    sheet, rows = _rows('claims')
    if not sheet:
        return {'available': False}
    cols = sheet.columns
    c_key = colfinder(cols)
    c_amt = sheet.amount_column or _col(cols, ('inv', 'amount'), 'amount')
    agg = defaultdict(lambda: [Decimal('0'), 0])
    for r in rows:
        k = (r.cells.get(c_key) or '').strip() or '(blank)'
        agg[k][0] += _money(r.cells.get(c_amt))
        agg[k][1] += 1
    out = [{'name': k, 'total': str(v[0].quantize(Decimal('0.01'))), 'count': v[1]}
           for k, v in agg.items()]
    out.sort(key=lambda x: _money(x['total']), reverse=True)
    return {'available': True, 'rows': out}


def cost_by_firm():
    return _cost_by(lambda cols: _col(cols, ('law', 'firm'), 'firm'))


def cost_by_case():
    return _cost_by(lambda cols: _col(cols, ('case', 'matter'), 'case', 'matter'))


def insights_summary():
    """Headline numbers for the Insights tab — cheap enough to load on open."""
    gap = gap_to_ledger()
    lim = member_limits()
    dup = duplicate_claims()
    prem = premium_gaps()
    return {
        'gap': {k: gap.get(k) for k in ('available', 'unmatched_count', 'unmatched_total', 'schedule_total')},
        'members': {k: lim.get(k) for k in ('available', 'over_count', 'near_count', 'limit')},
        'duplicates': {'ref_dupe_groups': dup['ref_dupe_groups'],
                       'fam_dupe_groups': dup['fam_dupe_groups'],
                       'amount_at_risk_ref': dup['amount_at_risk_ref']},
        'premium': {k: prem.get(k) for k in ('available', 'present_count', 'missing')},
    }


# ------------------------------------------------------------------------- 8
def bill_matches():
    """Reconcile the emailed/confirmed bills (ledger invoices) against the schedule
    by invoice reference: matched with the same amount, matched but AMOUNT DIFFERS
    (query the firm), and refs on one side only."""
    from .models import BonuInvoice
    sheet, rows = _rows('claims')
    if not sheet:
        return {'available': False}
    if not BonuInvoice.objects.exists():
        # No emailed bills to reconcile against. Reporting "matched: 0, only in the
        # schedule: 1,006" reads as 1,006 unmatched bills when in fact there is
        # nothing on the other side of the comparison at all.
        return {'available': False, 'reason': 'no-bills-loaded'}
    cols = sheet.columns
    c_ref = _col(cols, ('invoice', 'ref'), 'reference', 'invoice')
    c_amt = sheet.amount_column or _col(cols, ('inv', 'amount'), 'amount')
    c_firm = _col(cols, ('law', 'firm'), 'firm')

    def norm(x):
        return (x or '').strip().upper().replace(' ', '')
    sched = {}
    for r in rows:
        ref = norm(r.cells.get(c_ref))
        if ref:
            sched[ref] = {'amount': _money(r.cells.get(c_amt)), 'firm': r.cells.get(c_firm, ''),
                          'ref': r.cells.get(c_ref, '')}
    bills = {}
    for inv in BonuInvoice.objects.all():
        ref = norm(inv.invoice_number)
        if ref:
            bills[ref] = inv.total or Decimal('0')

    mismatches, matched = [], 0
    for ref, s in sched.items():
        if ref in bills:
            if abs(s['amount'] - bills[ref]) > Decimal('0.01'):
                mismatches.append({'ref': s['ref'], 'firm': s['firm'],
                                   'schedule_amount': str(s['amount']),
                                   'bill_amount': str(bills[ref].quantize(Decimal('0.01')))})
            else:
                matched += 1
    only_schedule = len([r for r in sched if r not in bills])
    only_bills = len([r for r in bills if r not in sched])
    mismatches.sort(key=lambda m: abs(_money(m['schedule_amount']) - _money(m['bill_amount'])), reverse=True)
    return {'available': True, 'matched': matched, 'mismatch_count': len(mismatches),
            'only_in_schedule': only_schedule, 'only_in_bills': only_bills,
            'mismatches': mismatches[:100]}
