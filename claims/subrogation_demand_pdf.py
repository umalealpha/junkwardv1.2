"""
claims/subrogation_demand_pdf.py — the letter of demand sent to a third party.

House PDF pattern, copied from healthcare/quote_pdf.py and
healthcare/agreement_pdf.py: reportlab (already a dependency), Alpha Direct navy
band with the white wordmark, orange used only as an accent, generous
whitespace.

    build_demand_letter_pdf(subrogation) -> bytes

This document LEAVES THE COMPANY. Two consequences, both deliberate:

  * Plain, humble English. It asks; it does not threaten. If the recovery ends
    up with lawyers, the lawyers write that letter, not Omni.
  * No internal chatter on the page — no Omni links, no task references, no
    internal email addresses beyond the one we want them to answer.

The figure printed is `total_recoverable`, which is COMPUTED from the cost
build-up on the case (assessor + repairs + excess + towing + legal, less
salvage). It is never a typed-in number, so the letter cannot disagree with the
register it came from.
"""
from __future__ import annotations

import io
import os
from decimal import Decimal

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, Image, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

NAVY = HexColor('#0D1B2A')
ORANGE = HexColor('#F4A623')
GREY = HexColor('#6B7280')
LIGHT = HexColor('#F3F4F6')

# Shared brand asset — the same white wordmark the health quote uses.
_LOGO_WHITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'healthcare', 'ad-logo-white.png')

COMPANY_BLOCK = (
    'Alpha Direct Insurance Company (Pty) Ltd<br/>'
    'Floor 2, Botswana Innovation Hub Icon Building<br/>'
    'Plot 69184, Block 8 Industrial, Gaborone<br/>'
    'P.O. Box 26ADC, Gaborone, Botswana<br/>'
    'Tel: +267 392 8264 &nbsp;·&nbsp; Toll free: 0800 601 029'
)


def _esc(s) -> str:
    return (str(s or '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _money(v) -> str:
    return f'P {Decimal(v or 0):,.2f}'


def build_demand_letter_pdf(sub) -> bytes:
    """Render the letter of demand for one subrogation case."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title=f'Letter of demand {sub.claim_reference}',
        author='Alpha Direct Insurance Company (Pty) Ltd')

    ss = getSampleStyleSheet()
    head = ParagraphStyle('hk', parent=ss['Normal'], textColor=colors.white,
                          fontSize=10, alignment=2)
    lbl = ParagraphStyle('l', parent=ss['Normal'], textColor=GREY, fontSize=8)
    val = ParagraphStyle('v', parent=ss['Normal'], textColor=NAVY, fontSize=9)
    body = ParagraphStyle('b', parent=ss['Normal'], fontSize=10, leading=15,
                          spaceAfter=8)
    foot = ParagraphStyle('f', parent=ss['Normal'], textColor=GREY, fontSize=7,
                          leading=10)
    el = []

    # -- Header band -------------------------------------------------------
    if os.path.exists(_LOGO_WHITE):
        logo = Image(_LOGO_WHITE, width=46 * mm, height=22.7 * mm)
        logo.hAlign = 'LEFT'
        left_cell = logo
    else:
        left_cell = Paragraph(
            'Alpha Direct', ParagraphStyle('t', parent=ss['Title'],
                                           textColor=ORANGE, fontSize=18))
    band = Table([[left_cell, Paragraph('LETTER OF DEMAND', head)]],
                 colWidths=[95 * mm, 79 * mm])
    band.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    el += [band, Spacer(1, 10)]

    today = timezone.localdate()
    el += [Paragraph(today.strftime('%d %B %Y'), val), Spacer(1, 8)]

    # -- Addressee ---------------------------------------------------------
    addressee = _esc(sub.third_party_name) or 'The Third Party'
    el.append(Paragraph(f'<b>{addressee}</b>', body))
    if sub.third_party_insurer:
        el.append(Paragraph(f'c/o {_esc(sub.third_party_insurer)}', body))
    el.append(Spacer(1, 6))

    # -- Case facts --------------------------------------------------------
    incident = (sub.incident_date.strftime('%d %B %Y')
                if sub.incident_date else 'not recorded')
    meta = [
        [Paragraph('Our claim reference', lbl),
         Paragraph(_esc(sub.claim_reference), val),
         Paragraph('Date of incident', lbl),
         Paragraph(incident, val)],
        [Paragraph('Amount we paid', lbl),
         Paragraph(_money(sub.claim_paid_amount), val),
         Paragraph('Amount we are claiming', lbl),
         Paragraph(f'<b>{_money(sub.total_recoverable)}</b>', val)],
    ]
    mt = Table(meta, colWidths=[32 * mm, 55 * mm, 35 * mm, 52 * mm])
    mt.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT),
        ('BOX', (0, 0), (-1, -1), 0.5, ORANGE),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    el += [mt, Spacer(1, 12)]

    # -- The letter itself -------------------------------------------------
    el.append(Paragraph('Dear Sir or Madam', body))
    el.append(Paragraph(
        f'<b>Recovery of amounts paid under claim {_esc(sub.claim_reference)}'
        f'</b>', body))
    el.append(Paragraph(
        f'We insure the other party involved in the incident of {incident}. '
        f'We have settled our insured’s claim and paid '
        f'{_money(sub.claim_paid_amount)}.', body))
    el.append(Paragraph(
        f'Our records show that the loss was caused by you or by someone you '
        f'are responsible for. We are therefore asking you to reimburse us '
        f'<b>{_money(sub.total_recoverable)}</b>.', body))

    # -- The build-up, where it exists -------------------------------------
    rows = [('Assessor fees', sub.assessor_fees),
            ('Repair costs', sub.repair_costs),
            ('Client excess', sub.client_excess),
            ('Towing fees', sub.towing_fees),
            ('Legal fees', sub.legal_fees),
            ('Less: salvage recovered', sub.salvage_amount)]
    rows = [(label, amt) for label, amt in rows if amt is not None]
    if rows:
        data = [['How the amount is made up', 'Pula']]
        for label, amt in rows:
            shown = -Decimal(amt) if label.startswith('Less') else Decimal(amt)
            data.append([label, f'{shown:,.2f}'])
        data.append(['Total claimed', f'{Decimal(sub.total_recoverable):,.2f}'])
        t = Table(data, colWidths=[124 * mm, 50 * mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (0, -1), (-1, -1), ORANGE),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, LIGHT]),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        el += [Spacer(1, 4), t, Spacer(1, 12)]

    el.append(Paragraph(
        'Please let us have your payment, or your insurer’s details so we '
        'can take it up with them, within 14 days of this letter.', body))
    el.append(Paragraph(
        'If you believe any part of this is wrong, please tell us and send us '
        'whatever you have. We would much rather settle this between us than '
        'take it any further.', body))
    el.append(Paragraph('Thank you.', body))
    el.append(Spacer(1, 10))
    el.append(Paragraph('Yours faithfully', body))
    el.append(Paragraph('<b>Recoveries</b><br/>'
                        'Alpha Direct Insurance Company (Pty) Ltd', body))

    el += [Spacer(1, 14),
           HRFlowable(width='100%', thickness=0.5, color=ORANGE),
           Spacer(1, 4),
           Paragraph(COMPANY_BLOCK, foot)]

    doc.build(el)
    return buf.getvalue()
