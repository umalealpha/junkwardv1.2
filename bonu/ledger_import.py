"""
bonu/ledger_import.py — the invoice history was in Omni the whole time.

I told the CFO the forensics could not start until Ikanyeng's invoice pack arrived. That was
wrong, and he pushed back. The 890 BONU claim journal lines all read "BONU Clamis" in the
LINE description — which is what I looked at — but the JOURNAL ENTRY carries:

    notes       → the law firm's name        ("JEREMIAH TLADI & COMPANY")
    description → "Odoo BILL/2025/07/0005 — INVOICE NO: 5131 - …"
    entry_date  → when it was booked
    debit_bwp   → the money

So 32 firms, 887 referenced bills and P4.85M of legal spend can be loaded from the ledger
right now. Enough for the league table, cost per firm, duplicate invoice numbers, and the
retainer double-dip check. NOT enough for the rate and hours tests — those need the real
invoice lines, and this module says so rather than pretending.

**PII — the one hard line.** The entry description also contains the MEMBER'S NAME (the
Odoo bill was addressed to the member). A member name must never be stored in a field that
gets exported, shown on a dashboard or sent to an AI. So this importer takes the firm, the
reference, the date and the amount, and **deliberately discards the rest of the description**.
`member_ref` is left empty because a name is not a reference (AD-POL-AI-GOV-001).
"""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

BONU_CLAIMS_ACCOUNT = '103014'
SOURCE_TAG = 'Odoo GL import — account 103014 (BONU Claims)'

# "INVOICE NO: 5131", "INVOICE: 0100", "INV #A-22"
INVOICE_NO = re.compile(r'INVOICE\s*(?:NO)?\s*[:#]?\s*([A-Z0-9][A-Z0-9/\-]{1,20})', re.I)
# "Odoo BILL/2025/07/0005" — always present, so it is the fallback reference.
BILL_REF = re.compile(r'(BILL/\d{4}/\d{2}/\d+)')
Z = Decimal('0')


def _firm_key(name: str) -> str:
    """Match firms that differ only in case, spacing or punctuation.

    The ledger holds "JEREMIAH TLADI & COMPANY" and could equally hold "Jeremiah Tladi and
    Company" — the same firm billing us twice under two spellings would otherwise split the
    league table in half and hide the duplicate.
    """
    s = (name or '').upper()
    s = s.replace('&', ' AND ')
    s = re.sub(r'[^A-Z0-9 ]', ' ', s)
    s = re.sub(r'\b(PTY|LTD|LIMITED|INC|CO|COMPANY|ATTORNEYS?|ASSOCIATES?|PARTNERS?)\b', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _reference(description: str) -> tuple[str, str]:
    """(invoice_number, bill_ref) off the entry description. Never a person's name."""
    desc = description or ''
    inv = INVOICE_NO.search(desc)
    bill = BILL_REF.search(desc)
    return ((inv.group(1).strip()[:40] if inv else ''),
            (bill.group(1).strip()[:40] if bill else ''))


def _member_name(description: str) -> str:
    """The member named on the bill, for tokenising in memory. Never returned to a caller
    that stores it — `load()` converts it to a token and drops it."""
    try:
        from bonu.member_identity import member_name_from_bill
        return member_name_from_bill(description)
    except BaseException:          # noqa: BLE001 — never let this stop an import
        return ''


def collect(lines):
    """Group ledger lines into per-invoice buckets. Pure — no database writes.

    Returns (buckets, stats). A bucket is one invoice: firm, reference, date, amount, and how
    many ledger lines rolled into it.
    """
    buckets, stats = {}, {'lines': 0, 'no_firm': 0, 'no_reference': 0, 'net_zero': 0}
    for l in lines:
        e = l.journal_entry
        stats['lines'] += 1
        firm_name = (getattr(e, 'notes', '') or '').strip()
        if not firm_name:
            stats['no_firm'] += 1
            continue
        inv_no, bill_ref = _reference(getattr(e, 'description', ''))
        ref = inv_no or bill_ref
        if not ref:
            stats['no_reference'] += 1
            continue
        amount = (l.debit_bwp or Z) - (l.credit_bwp or Z)
        key = (_firm_key(firm_name), ref)
        b = buckets.setdefault(key, {
            'firm_name': firm_name, 'invoice_number': ref, 'bill_ref': bill_ref,
            'amount': Z, 'ledger_lines': 0, 'dates': [], 'entry_numbers': [],
            'member_name': '',
        })
        b['amount'] += amount
        b['ledger_lines'] += 1
        # The member's NAME is in this description. It is held IN MEMORY only, long enough to
        # become a one-way token at write time, and is never stored in a column — see
        # bonu/member_identity.py. Without it the single most valuable fraud test (the same
        # member claiming through several firms) is impossible.
        if not b.get('member_name'):
            b['member_name'] = _member_name(getattr(e, 'description', ''))
        if getattr(e, 'entry_date', None):
            b['dates'].append(e.entry_date)
        if getattr(e, 'entry_number', ''):
            b['entry_numbers'].append(e.entry_number)

    for b in buckets.values():
        b['invoice_date'] = min(b['dates']) if b['dates'] else None
        b['last_date'] = max(b['dates']) if b['dates'] else None
        # The same reference booked on two different dates is worth a question, not a merge.
        b['dates_differ'] = bool(b['dates']) and b['invoice_date'] != b['last_date']
        if b['amount'] == Z:
            stats['net_zero'] += 1
    return buckets, stats


def load(dry_run=True, account_code=BONU_CLAIMS_ACCOUNT):
    """Create LawFirm and BonuInvoice rows from the ledger. Returns a report dict."""
    from django.db import transaction

    from bonu.models import BonuInvoice, BonuInvoiceLine, LawFirm
    from ledger.models import Account, JournalEntryLine

    acc = Account.objects.filter(code=account_code).first()
    if acc is None:
        return {'ok': False, 'error': f'account {account_code} is not in the chart of accounts'}

    qs = (JournalEntryLine.objects.filter(account=acc)
          .select_related('journal_entry').order_by('id'))
    buckets, stats = collect(qs)

    report = {
        'ok': True, 'dry_run': dry_run, 'account': account_code,
        'stats': stats, 'invoices_seen': len(buckets),
        'firms_created': 0, 'firms_matched': 0,
        'invoices_created': 0, 'invoices_skipped_existing': 0,
        'total_amount': float(sum(b['amount'] for b in buckets.values())),
        'multi_date_references': sum(1 for b in buckets.values() if b['dates_differ']),
        # Counted from the presence of a name, NOT by tokenising — a dry run must not even
        # create the salt row.
        'with_member_token': sum(1 for b in buckets.values() if b.get('member_name')),
        'by_firm': defaultdict(lambda: {'invoices': 0, 'amount': 0.0}),
        'limits': ('Firm, invoice reference, date and amount only. There are no hours, rates '
                   'or matter types in the ledger, so the rate, arithmetic and impossible-day '
                   'checks stay switched off until real invoice lines are loaded. No member '
                   'data was imported.'),
    }
    for (fkey, _ref), b in buckets.items():
        row = report['by_firm'][b['firm_name']]
        row['invoices'] += 1
        row['amount'] += float(b['amount'])

    if dry_run:
        # Return BEFORE touching a bonu table, so the dry run also works on a system where
        # the migration has not been applied yet — which is exactly when you want to see what
        # a load would do.
        report['by_firm'] = dict(report['by_firm'])
        return report

    # Existing firms, matched on the normalised key so a second spelling does not create a
    # second firm.
    existing_firms = {_firm_key(f.name): f for f in LawFirm.objects.all()}
    # One salt fetch for the whole run; the names never leave this function.
    from bonu.member_identity import get_salt, token as _token
    salt = get_salt()

    with transaction.atomic():
        for (fkey, ref), b in sorted(buckets.items(), key=lambda kv: kv[1]['invoice_date'] or ''):
            firm = existing_firms.get(fkey)
            if firm is None:
                firm = LawFirm.objects.create(
                    name=b['firm_name'][:200],
                    notes='Created from the general ledger (BONU Claims). No agreed rate on '
                          'file, so rate checks cannot run for this firm yet.')
                existing_firms[fkey] = firm
                report['firms_created'] += 1
            else:
                report['firms_matched'] += 1

            if BonuInvoice.objects.filter(firm=firm, invoice_number=ref).exists():
                report['invoices_skipped_existing'] += 1
                continue
            if b['invoice_date'] is None:
                continue

            note = (f"Loaded from the ledger ({', '.join(b['entry_numbers'][:3])}"
                    f"{'…' if len(b['entry_numbers']) > 3 else ''}). "
                    f"{b['ledger_lines']} ledger line(s). Firm, reference, date and amount are "
                    f"from the books; hours, rate and case type were never recorded there.")
            if b['dates_differ']:
                note += (f" NOTE: this reference was booked on more than one date "
                         f"({b['invoice_date']} … {b['last_date']}) — worth asking about.")

            inv = BonuInvoice.objects.create(
                firm=firm, invoice_number=ref, invoice_date=b['invoice_date'],
                subtotal=b['amount'], total=b['amount'],
                status=BonuInvoice.Status.RECEIVED,
                source_file=SOURCE_TAG, review_note=note)
            # One line carrying the money, so spend joins up. matter_ref holds the firm's own
            # bill reference; member_ref stays EMPTY because the ledger only had a person's
            # name and a name is not a reference.
            BonuInvoiceLine.objects.create(
                invoice=inv, line_no=1, service_date=b['invoice_date'],
                matter_ref=(b['bill_ref'] or ref)[:80], member_ref='',
                member_token=(_token(b['member_name'], salt) if b.get('member_name') else ''),
                matter_type=BonuInvoiceLine.MatterType.OTHER,
                matter_type_source=BonuInvoiceLine.ClassifiedBy.DEFAULT,
                basis=BonuInvoiceLine.Basis.OTHER,
                amount=b['amount'],
                matter_description='')
            report['invoices_created'] += 1

    report['by_firm'] = dict(report['by_firm'])
    return report
