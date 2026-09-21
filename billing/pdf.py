"""
billing/pdf.py

Generates a PDF invoice/bill/credit-note using ReportLab.
Returns the PDF as a bytes object.
"""

from __future__ import annotations

from io import BytesIO
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Spacer, HRFlowable,
)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT

# ---------------------------------------------------------------------------
# Branding colours
# ---------------------------------------------------------------------------
NAVY   = colors.HexColor('#0f172a')
ORANGE = colors.HexColor('#ea580c')
LIGHT  = colors.HexColor('#f1f5f9')
MID    = colors.HexColor('#64748b')
WHITE  = colors.white

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm


def _fmt(amount) -> str:
    """Format a Decimal or string as BWP amount."""
    try:
        v = Decimal(str(amount))
        return f"P {v:,.2f}"
    except Exception:
        return str(amount)


def generate_invoice_pdf(invoice) -> bytes:
    """
    invoice: billing.models.Invoice instance (with .lines prefetched).
    Returns PDF bytes.
    """
    buf  = BytesIO()
    doc  = SimpleDocTemplate(
        buf,
        pagesize       = A4,
        leftMargin     = MARGIN,
        rightMargin    = MARGIN,
        topMargin      = MARGIN,
        bottomMargin   = MARGIN,
        title          = invoice.invoice_number,
    )

    styles   = getSampleStyleSheet()
    story    = []

    # ── Header styles ──────────────────────────────────────────────────
    title_style = ParagraphStyle(
        'InvTitle', parent=styles['Normal'],
        fontSize=22, textColor=ORANGE, fontName='Helvetica-Bold',
    )
    sub_style = ParagraphStyle(
        'InvSub', parent=styles['Normal'],
        fontSize=10, textColor=MID,
    )
    heading_style = ParagraphStyle(
        'InvHeading', parent=styles['Normal'],
        fontSize=10, textColor=WHITE, fontName='Helvetica-Bold',
    )
    body_style = ParagraphStyle(
        'InvBody', parent=styles['Normal'],
        fontSize=9, textColor=NAVY,
    )
    right_style = ParagraphStyle(
        'InvRight', parent=styles['Normal'],
        fontSize=9, textColor=NAVY, alignment=TA_RIGHT,
    )
    total_label = ParagraphStyle(
        'TotalLabel', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica-Bold', textColor=NAVY,
    )
    total_value = ParagraphStyle(
        'TotalValue', parent=styles['Normal'],
        fontSize=10, fontName='Helvetica-Bold', textColor=NAVY, alignment=TA_RIGHT,
    )
    grand_label = ParagraphStyle(
        'GrandLabel', parent=styles['Normal'],
        fontSize=12, fontName='Helvetica-Bold', textColor=WHITE,
    )
    grand_value = ParagraphStyle(
        'GrandValue', parent=styles['Normal'],
        fontSize=12, fontName='Helvetica-Bold', textColor=WHITE, alignment=TA_RIGHT,
    )

    # ── Company header + doc type ───────────────────────────────────────
    doc_type_map = {
        'customer_invoice': 'TAX INVOICE',
        'vendor_bill':      'VENDOR BILL',
        'credit_note':      'CREDIT NOTE',
        'vendor_credit':    'VENDOR CREDIT',
    }
    doc_type = doc_type_map.get(invoice.invoice_type, 'INVOICE')

    header_data = [
        [
            Paragraph('Alpha Direct Insurance', title_style),
            Paragraph(doc_type, ParagraphStyle(
                'DocType', parent=styles['Normal'],
                fontSize=18, fontName='Helvetica-Bold',
                textColor=NAVY, alignment=TA_RIGHT,
            )),
        ],
        [
            Paragraph('Gaborone, Botswana  |  VAT Reg: P03664382112', sub_style),
            Paragraph(
                f"<b>{invoice.invoice_number}</b>",
                ParagraphStyle('InvNum', parent=styles['Normal'],
                               fontSize=11, textColor=ORANGE,
                               fontName='Helvetica-Bold', alignment=TA_RIGHT),
            ),
        ],
    ]
    header_tbl = Table(header_data, colWidths=[PAGE_W - 2*MARGIN - 70*mm, 70*mm])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(header_tbl)
    story.append(HRFlowable(width='100%', thickness=2, color=ORANGE, spaceAfter=8))

    # ── Bill To / Dates block ───────────────────────────────────────────
    contact = invoice.contact
    bill_to_lines = [
        f"<b>BILL TO</b>",
        contact.name,
    ]
    if contact.address:
        bill_to_lines.append(contact.address)
    if contact.email:
        bill_to_lines.append(contact.email)
    if contact.phone:
        bill_to_lines.append(contact.phone)

    dates_lines = [
        f"<b>Issue Date:</b>  {invoice.issue_date}",
        f"<b>Due Date:</b>    {invoice.due_date}",
        f"<b>Currency:</b>    {invoice.currency_code_id}",
        f"<b>Status:</b>      {invoice.status.upper()}",
    ]

    info_data = [
        [
            Paragraph('<br/>'.join(bill_to_lines), body_style),
            Paragraph('<br/>'.join(dates_lines), body_style),
        ]
    ]
    info_tbl = Table(info_data, colWidths=[(PAGE_W - 2*MARGIN)/2]*2)
    info_tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BACKGROUND', (0,0), (-1,-1), LIGHT),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(info_tbl)
    story.append(Spacer(1, 10))

    # ── Description ─────────────────────────────────────────────────────
    if invoice.description:
        story.append(Paragraph(invoice.description, body_style))
        story.append(Spacer(1, 6))

    # ── Line items table ────────────────────────────────────────────────
    col_w = PAGE_W - 2*MARGIN
    col_widths = [col_w*0.40, col_w*0.10, col_w*0.15, col_w*0.10, col_w*0.10, col_w*0.15]

    lines_data = [[
        Paragraph('Description', heading_style),
        Paragraph('Qty',         heading_style),
        Paragraph('Unit Price',  heading_style),
        Paragraph('Tax Code',    heading_style),
        Paragraph('Tax Amt',     heading_style),
        Paragraph('Total',       heading_style),
    ]]

    for ln in invoice.lines.all():
        lines_data.append([
            Paragraph(ln.description or '', body_style),
            Paragraph(str(ln.quantity),      right_style),
            Paragraph(_fmt(ln.unit_price),   right_style),
            Paragraph(str(ln.tax_code.tax_code if ln.tax_code else ''), body_style),
            Paragraph(_fmt(ln.tax_amount),   right_style),
            Paragraph(_fmt(ln.line_total),   right_style),
        ])

    lines_tbl = Table(lines_data, colWidths=col_widths)
    lines_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  NAVY),
        ('GRID',          (0, 0), (-1, -1), 0.3, colors.HexColor('#e2e8f0')),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, LIGHT]),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
    ]))
    story.append(lines_tbl)
    story.append(Spacer(1, 10))

    # ── Totals block ────────────────────────────────────────────────────
    right_col = col_w * 0.35
    totals_data = [
        [Paragraph('Subtotal',   total_label), Paragraph(_fmt(invoice.subtotal),     total_value)],
        [Paragraph('Tax (VAT)',  total_label), Paragraph(_fmt(invoice.tax_total),     total_value)],
        [Paragraph('Amount Paid',total_label), Paragraph(_fmt(invoice.amount_paid),   total_value)],
    ]
    grand_data = [
        [Paragraph('BALANCE DUE', grand_label), Paragraph(_fmt(invoice.balance_due), grand_value)],
    ]

    totals_tbl = Table(totals_data, colWidths=[right_col*0.6, right_col*0.4])
    totals_tbl.setStyle(TableStyle([
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('LINEBELOW', (0,-1), (-1,-1), 0.5, MID),
        ('TOPPADDING', (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
    ]))

    grand_tbl = Table(grand_data, colWidths=[right_col*0.6, right_col*0.4])
    grand_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('ALIGN', (1,0), (1,-1), 'RIGHT'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))

    wrapper = Table(
        [[totals_tbl], [Spacer(1,4)], [grand_tbl]],
        colWidths=[right_col],
        hAlign='RIGHT',
    )
    story.append(wrapper)
    story.append(Spacer(1, 16))

    # ── Footer ──────────────────────────────────────────────────────────
    story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#e2e8f0')))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        'Alpha Direct Insurance — Gaborone, Botswana  '
        '|  This document was computer generated.',
        ParagraphStyle('Footer', parent=styles['Normal'],
                       fontSize=8, textColor=MID, alignment=TA_CENTER),
    ))

    doc.build(story)
    return buf.getvalue()
