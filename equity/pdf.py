"""equity/pdf.py — a one-page "My Equity" statement for a holder.

House style (Alpha Navy #0D1B2A / Orange #F4A623), reportlab like payroll/pdf.py.
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from django.utils import timezone

NAVY = colors.HexColor('#0D1B2A')
ORANGE = colors.HexColor('#F4A623')
GREY = colors.HexColor('#6b7280')


def _usd(n) -> str:
    return '$' + f'{round(float(n or 0)):,}'


def _usd_price(n) -> str:
    """Per-share price — keep cents so a $1.82 price never shows as '$2'."""
    return '$' + f'{float(n or 0):,.2f}'


def generate_my_equity_pdf(data: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm)
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=ss['Title'], textColor=NAVY, fontSize=18, spaceAfter=2)
    sub = ParagraphStyle('sub', parent=ss['Normal'], textColor=GREY, fontSize=9, spaceAfter=10)
    h2 = ParagraphStyle('h2', parent=ss['Heading2'], textColor=ORANGE, fontSize=11, spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle('body', parent=ss['Normal'], fontSize=9.5, leading=13)
    fine = ParagraphStyle('fine', parent=ss['Normal'], textColor=GREY, fontSize=7.5, leading=10)

    issuer = data.get('issuer', {})
    story = [
        Paragraph('My Equity Statement', h1),
        Paragraph(f"{issuer.get('legal_name','')} · Holder: <b>{data.get('holder','')}</b> · "
                  f"As at {timezone.localdate():%d %b %Y}", sub),
    ]

    story.append(Paragraph(
        'This statement shows the shares and share options recorded against you under the '
        'Alpha Direct Insurtech Stock Ownership and Option Plan 2021. Values shown are '
        'indicative only.', body))

    t = data.get('totals', {})
    price = data.get('current_share_price_usd', 0)
    summary = [
        ['', 'Units / Shares', 'Indicative value'],
        ['Share options', f"{t.get('option_units',0):,}", _usd(t.get('option_worth_usd'))],
        ['Shares held', f"{t.get('shares',0):,}", _usd(t.get('share_worth_usd'))],
    ]
    st = Table(summary, colWidths=[55 * mm, 55 * mm, 55 * mm])
    st.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, colors.HexColor('#e5e7eb')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story += [Spacer(1, 8), st, Spacer(1, 4),
              Paragraph(f'Indicative price per share: {_usd_price(price)} ({data.get("price_basis","")})', fine)]

    grants = data.get('grants', [])
    if grants:
        story.append(Paragraph('Grants & vesting', h2))
        rows = [['Units', 'Grant date', 'Vested', 'Next vesting', 'Indicative value']]
        for g in grants:
            v = g.get('vesting', {})
            if v.get('has_schedule'):
                vested = f"{v.get('pct_vested',0)}%"
                nxt = (f"{v.get('next_vest_units',0):,} on {v.get('next_vest_date')}"
                       if v.get('next_vest_date') else 'Fully vested')
            else:
                vested = 'Per letter'
                nxt = 'Not yet loaded'
            rows.append([f"{g.get('units',0):,}", g.get('grant_date') or '—',
                         vested, nxt, _usd(g.get('worth_usd'))])
        gt = Table(rows, colWidths=[25 * mm, 28 * mm, 22 * mm, 50 * mm, 40 * mm])
        gt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f3f4f6')),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, colors.HexColor('#e5e7eb')),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(gt)

    story += [
        Spacer(1, 12),
        Paragraph(
            'Indicative values use a valuation-model share price, not a market price, and are '
            'not an offer to buy or sell, or a promise of payment. Options vest and become '
            'exercisable strictly under your Letter of Grant and the Plan rules (continued '
            'employment required; malus/clawback may apply). This statement is for your '
            'information only. Questions: Finance.', fine),
    ]
    doc.build(story)
    return buf.getvalue()
