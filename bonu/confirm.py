"""
bonu/confirm.py — the waiting room between a document and a payable.

Two jobs, both aimed at the same thing: the accountant should be CHECKING, never typing.

**1. Warn before, not after.** The moment a bill is read we already know enough to say
"this firm already sent us this invoice number", "this matter was billed in May", "these
lines do not add up to the total on the front page". Saying that while she still has the
document open costs nothing. Saying it after we have paid costs money.

**2. Confirm creates the invoice — nothing else does.** The machine's parse is stored as a
draft and never becomes an invoice on its own. `confirm()` takes what the human agreed
(which may differ from what was read), records WHO classified each case type, and writes
the invoice in one transaction. If it fails, nothing is half-written.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.utils import timezone

Z = Decimal('0')
RECENT_DAYS = 120        # how far back to look for the same matter being billed again


def _dec(v, default=Z):
    try:
        return Decimal(str(v).replace(',', '').strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        return default


def _date(v):
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def warnings_for_draft(draft, firm=None, today=None):
    """Everything worth seeing BEFORE the invoice exists. Cheap checks, no AI.

    Deliberately conservative: each warning names the money and the reason, so the
    accountant can clear it in one look instead of investigating a vague flag.
    """
    from bonu.models import BonuInvoice, BonuInvoiceLine
    today = today or timezone.localdate()
    out = []
    hdr = draft.get('header') or {}
    lines = draft.get('lines') or []
    inv_no = (hdr.get('invoice_number') or '').strip()

    if firm and inv_no:
        if BonuInvoice.objects.filter(firm=firm, invoice_number=inv_no).exists():
            out.append({
                'code': 'INVOICE_SEEN_BEFORE', 'severity': 'high',
                'message': (f'{firm.name} has already sent us invoice {inv_no}. Check this is not '
                            f'the same bill arriving twice.'),
            })

    if not inv_no:
        out.append({'code': 'NO_INVOICE_NUMBER', 'severity': 'medium',
                    'message': 'No invoice number was found on the document. Type it in before '
                               'confirming — it is what stops the same bill being paid twice.'})

    inv_date = _date(hdr.get('invoice_date'))
    if inv_date and inv_date > today:
        out.append({'code': 'FUTURE_DATED', 'severity': 'high',
                    'message': f'The invoice is dated {inv_date:%d %B %Y}, which is in the future.'})

    lines_total = sum(_dec(l.get('amount')) for l in lines)
    hdr_total = _dec(hdr.get('total'), None) if hdr.get('total') is not None else None
    if hdr_total is not None and lines_total and abs(lines_total - hdr_total) > Decimal('0.05'):
        out.append({
            'code': 'DOES_NOT_FOOT', 'severity': 'high',
            'message': (f'The lines add up to P{lines_total:,.2f} but the document shows '
                        f'P{hdr_total:,.2f}. Difference P{abs(lines_total - hdr_total):,.2f}.'),
        })

    if not lines:
        out.append({'code': 'NO_LINES', 'severity': 'high',
                    'message': 'No billed lines could be read. Capture them by hand, or ask the '
                               'firm for the bill as a spreadsheet.'})

    # Same matter billed again recently — the single most common way we pay twice.
    refs = {(l.get('matter_ref') or '').strip() for l in lines}
    refs.discard('')
    if refs and firm:
        since = today - dt.timedelta(days=RECENT_DAYS)
        seen = (BonuInvoiceLine.objects
                .filter(invoice__firm=firm, matter_ref__in=refs, invoice__invoice_date__gte=since)
                .values_list('matter_ref', 'invoice__invoice_number', 'invoice__invoice_date'))
        for ref, prev_no, prev_date in seen[:20]:
            out.append({
                'code': 'MATTER_BILLED_RECENTLY', 'severity': 'medium',
                'message': (f'Matter {ref} was already billed on invoice {prev_no} '
                            f'({prev_date:%d %b %Y}). Confirm this is new work.'),
            })

    unclassified = sum(1 for l in lines
                       if (l.get('matter_type') or 'other') == 'other')
    if unclassified:
        out.append({'code': 'CASE_TYPE_NOT_STATED', 'severity': 'low',
                    'message': (f'{unclassified} line(s) have no case type. Set them before '
                                f'confirming, or spend by case type will be wrong.')})
    return out


def confirm(document, payload, user=None, today=None):
    """Create the invoice from what the HUMAN agreed. The only path that writes a payable.

    `payload` is the corrected draft off the confirm screen. Anything the accountant
    changed wins over what the machine read — that is the whole point of the screen. The
    original parse stays on the document for audit.
    """
    from django.db import transaction

    from bonu.models import BonuInvoice, BonuInvoiceLine, IngestedDocument, LawFirm
    today = today or timezone.localdate()

    if document.status == IngestedDocument.Status.CONFIRMED:
        raise ValueError('This document has already been confirmed into an invoice.')

    firm_id = payload.get('firm_id') or (document.firm_id if document.firm_id else None)
    if not firm_id:
        raise ValueError('Choose which firm this bill is from before confirming.')
    firm = LawFirm.objects.get(pk=firm_id)

    hdr = payload.get('header') or {}
    inv_no = (hdr.get('invoice_number') or '').strip()
    inv_date = _date(hdr.get('invoice_date'))
    if not inv_no:
        raise ValueError('An invoice number is required.')
    if not inv_date:
        raise ValueError('An invoice date is required.')
    if inv_date > today:
        raise ValueError('The invoice date cannot be in the future.')

    rows = payload.get('lines') or []
    if not rows:
        raise ValueError('An invoice needs at least one line.')

    lines_total = sum(_dec(r.get('amount')) for r in rows)
    subtotal = _dec(hdr.get('subtotal'), lines_total) or lines_total
    vat = _dec(hdr.get('vat'))
    total = _dec(hdr.get('total'), subtotal + vat) or (subtotal + vat)

    with transaction.atomic():
        if BonuInvoice.objects.filter(firm=firm, invoice_number=inv_no).exists():
            raise ValueError(f'{firm.name} already has an invoice numbered {inv_no} on file.')

        invoice = BonuInvoice.objects.create(
            firm=firm, invoice_number=inv_no, invoice_date=inv_date,
            period_start=_date(hdr.get('period_start')), period_end=_date(hdr.get('period_end')),
            subtotal=subtotal, vat=vat, total=total,
            source_file=document.filename,
            review_note=(payload.get('review_note') or '')[:4000],
        )

        made = []
        for i, r in enumerate(rows, start=1):
            mtype = (r.get('matter_type') or 'other')
            # Who said so, in priority order: the accountant touched it > the firm stated it
            # > the AI guessed it. Never silently promote a guess.
            src = r.get('matter_type_source')
            if src not in dict(BonuInvoiceLine.ClassifiedBy.choices):
                src = ('manual' if r.get('_edited')
                       else ('firm' if r.get('_from_firm') else 'ai'))
            if mtype == 'other' and not r.get('_edited'):
                src = 'default'
            made.append(BonuInvoiceLine(
                invoice=invoice, line_no=i,
                service_date=_date(r.get('service_date')),
                matter_ref=(r.get('matter_ref') or '')[:80],
                member_ref=(r.get('member_ref') or '')[:80],
                service_code=(r.get('service_code') or '')[:40],
                matter_type=mtype, matter_type_source=src,
                matter_description=(r.get('matter_description') or '')[:4000],
                basis=(r.get('basis') or 'hourly'),
                fee_earner=(r.get('fee_earner') or '')[:120],
                units=_dec(r.get('units'), None) if r.get('units') not in (None, '') else None,
                rate=_dec(r.get('rate'), None) if r.get('rate') not in (None, '') else None,
                amount=_dec(r.get('amount')),
            ))
        BonuInvoiceLine.objects.bulk_create(made)

        document.invoice = invoice
        document.firm = firm
        document.status = IngestedDocument.Status.CONFIRMED
        document.save(update_fields=['invoice', 'firm', 'status', 'updated_at'])

    return invoice
