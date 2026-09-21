"""
payments/remittance.py — Vendor Remittance Advice PDF.

CFO directive 2026-05-24 (Track-B audit, AP gap #2): when a Payment to
a vendor is confirmed, the vendor needs a one-page PDF + a CSV listing
the bills covered. Without this, every vendor calls Finance asking
"which invoices did this lump-sum cover?".

Public API:
    render_remittance_pdf(payment) -> bytes
    render_remittance_csv(payment) -> str

Both are read-only: they pull `payment.allocations` and the linked
Invoice rows. They DO NOT post any JE, DO NOT mutate the payment, and
DO NOT touch any other table.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import TYPE_CHECKING

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

if TYPE_CHECKING:
    from payments.models import Payment


NAVY   = colors.HexColor('#0D1B2A')
ORANGE = colors.HexColor('#F07F00')
LIGHT  = colors.HexColor('#F1F5F9')
MID    = colors.HexColor('#64748B')


def _fmt(amount) -> str:
    try:
        return f'P {Decimal(str(amount)):,.2f}'
    except Exception:    # noqa: BLE001
        return str(amount)


def _payment_lines(payment) -> list[dict]:
    """Build per-allocation row dicts. Each row = one bill covered."""
    rows = []
    for alloc in payment.allocations.select_related('invoice').all():
        inv = alloc.invoice
        rows.append({
            'invoice_number': getattr(inv, 'invoice_number', '') or '',
            'issue_date':     getattr(inv, 'issue_date', None),
            'due_date':       getattr(inv, 'due_date', None),
            'reference':      getattr(inv, 'reference', '') or '',
            'invoice_total':  Decimal(getattr(inv, 'total_amount', 0) or 0),
            'amount_applied': Decimal(alloc.amount or 0),
            'invoice_status': getattr(inv, 'status', '') or '',
        })
    return rows


def render_remittance_pdf(payment) -> bytes:
    """Return remittance advice PDF as bytes."""
    rows = _payment_lines(payment)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f'Remittance Advice — {payment.payment_number}',
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=styles['Heading1'],
                        fontName='Helvetica-Bold', fontSize=18,
                        textColor=NAVY, spaceAfter=4)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'],
                        fontName='Helvetica-Bold', fontSize=11,
                        textColor=ORANGE, spaceAfter=8)
    body = ParagraphStyle('body', parent=styles['Normal'],
                          fontName='Helvetica', fontSize=9.5,
                          textColor=NAVY, leading=12)
    small = ParagraphStyle('small', parent=body, fontSize=8,
                           textColor=MID)
    right = ParagraphStyle('right', parent=body, alignment=TA_RIGHT)

    company = payment.company
    vendor = payment.contact
    flow = []

    flow.append(Paragraph(f'<b>{company.name if company else "Alpha Direct"}</b>',
                          ParagraphStyle('hdr', parent=h1, fontSize=14)))
    flow.append(Paragraph('Remittance Advice', h1))
    flow.append(HRFlowable(width='100%', thickness=2, color=ORANGE,
                           spaceBefore=2, spaceAfter=10))

    # Vendor + payment block
    header_data = [
        [Paragraph('<b>To</b>', small),
         Paragraph('<b>Payment</b>', small)],
        [Paragraph(
            f'{vendor.name if vendor else "Vendor"}<br/>'
            f'{(getattr(vendor, "address_line_1", "") or "")}<br/>'
            f'{(getattr(vendor, "email", "") or "")}',
            body),
         Paragraph(
            f'<b>Number:</b> {payment.payment_number}<br/>'
            f'<b>Date:</b> {payment.payment_date}<br/>'
            f'<b>Bank:</b> {payment.bank_account.name if payment.bank_account else "—"}<br/>'
            f'<b>Reference:</b> {payment.reference or "—"}',
            body)],
    ]
    t = Table(header_data, colWidths=[90 * mm, 80 * mm])
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    flow.append(t)
    flow.append(Spacer(1, 14))

    flow.append(Paragraph('Invoices covered by this payment', h2))

    data = [['Invoice #', 'Date', 'Reference', 'Status',
             'Invoice Total', 'Amount Applied']]
    total_applied = Decimal('0')
    for r in rows:
        data.append([
            r['invoice_number'],
            r['issue_date'].isoformat() if r['issue_date'] else '',
            r['reference'][:30],
            r['invoice_status'].replace('_', ' ').title(),
            _fmt(r['invoice_total']),
            _fmt(r['amount_applied']),
        ])
        total_applied += r['amount_applied']

    data.append(['', '', '', 'TOTAL APPLIED', '', _fmt(total_applied)])

    tbl = Table(data, colWidths=[28 * mm, 22 * mm, 35 * mm, 25 * mm,
                                 30 * mm, 30 * mm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',  (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR',   (0, 0), (-1, 0), colors.white),
        ('FONTNAME',    (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',    (0, 0), (-1, 0), 9),
        ('FONTSIZE',    (0, 1), (-1, -1), 9),
        ('ALIGN',       (-2, 0), (-1, -1), 'RIGHT'),
        ('LINEBELOW',   (0, 0), (-1, 0), 0.5, NAVY),
        ('LINEABOVE',   (0, -1), (-1, -1), 0.5, NAVY),
        ('FONTNAME',    (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND',  (0, -1), (-1, -1), LIGHT),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, LIGHT]),
        ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',  (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    flow.append(tbl)
    flow.append(Spacer(1, 18))

    if payment.notes:
        flow.append(Paragraph('<b>Notes</b>', small))
        flow.append(Paragraph(payment.notes, body))
        flow.append(Spacer(1, 8))

    flow.append(HRFlowable(width='100%', thickness=0.5, color=MID,
                           spaceBefore=4, spaceAfter=4))
    flow.append(Paragraph(
        'This is a system-generated remittance advice — no signature required. '
        'For queries, reply to this email or contact finance@alphadirect.co.bw.',
        small,
    ))

    doc.build(flow)
    return buf.getvalue()


def render_remittance_csv(payment) -> str:
    """Return remittance advice as CSV (one row per allocated invoice)."""
    rows = _payment_lines(payment)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['payment_number', 'payment_date', 'vendor', 'bank',
                'invoice_number', 'invoice_date', 'reference',
                'invoice_total', 'amount_applied'])
    vendor_name = payment.contact.name if payment.contact else ''
    bank_name   = payment.bank_account.name if payment.bank_account else ''
    for r in rows:
        w.writerow([
            payment.payment_number, payment.payment_date,
            vendor_name, bank_name,
            r['invoice_number'],
            r['issue_date'].isoformat() if r['issue_date'] else '',
            r['reference'], str(r['invoice_total']), str(r['amount_applied']),
        ])
    return out.getvalue()
