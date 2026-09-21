"""
assets/control_pdf.py

Render an Asset Handover Note as a print-ready, storable PDF (spec §6.3).
Self-contained reportlab/platypus — no external image files — so it never
fails on a missing asset path (spec §10: nothing on the control/handover path
may silently fail). House brand: navy #0D1B2A + orange #F4A623.

Public entry point: render_handover_pdf(handover) -> bytes.
"""
from __future__ import annotations

import io

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

NAVY   = HexColor('#0D1B2A')
ORANGE = HexColor('#F4A623')
INK    = HexColor('#1F2937')
MUTE   = HexColor('#6B7280')
LINE   = HexColor('#D1D5DB')


def _fmt_dt(v):
    return v.strftime('%d %b %Y %H:%M') if v else '—'


def render_handover_pdf(ho) -> bytes:
    """Build the handover-note PDF for an assets.AssetHandover instance."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=18 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm,
        title=f'Asset Handover Note {ho.handover_number}',
    )
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle('h1', parent=ss['Title'], fontName='Helvetica-Bold',
                        fontSize=18, textColor=NAVY, spaceAfter=2)
    sub = ParagraphStyle('sub', parent=ss['Normal'], fontName='Helvetica',
                         fontSize=9, textColor=MUTE)
    label = ParagraphStyle('label', parent=ss['Normal'], fontName='Helvetica-Bold',
                           fontSize=8, textColor=MUTE)
    val = ParagraphStyle('val', parent=ss['Normal'], fontName='Helvetica',
                         fontSize=10, textColor=INK)
    sec = ParagraphStyle('sec', parent=ss['Normal'], fontName='Helvetica-Bold',
                         fontSize=11, textColor=NAVY, spaceBefore=8, spaceAfter=4)

    story = []
    story.append(Paragraph('Alpha Direct Insurance', h1))
    story.append(Paragraph('Asset Handover Note', sub))
    story.append(Spacer(1, 8))

    # Header band: number + status
    hdr = Table([[
        Paragraph(f'<font color="#FFFFFF"><b>{ho.handover_number}</b></font>', val),
        Paragraph(f'<font color="#FFFFFF">{ho.get_status_display()}</font>', val),
    ]], colWidths=[95 * mm, 79 * mm])
    hdr.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('LINEBELOW', (0, 0), (-1, -1), 2, ORANGE),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    story.append(hdr)
    story.append(Spacer(1, 10))

    def kv(rows):
        data = []
        for lab, v in rows:
            data.append([Paragraph(lab.upper(), label), Paragraph(str(v or '—'), val)])
        t = Table(data, colWidths=[45 * mm, 129 * mm])
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE),
        ]))
        return t

    a = ho.asset
    story.append(Paragraph('Asset', sec))
    story.append(kv([
        ('Tag number', a.tag_number),
        ('Description', a.name),
        ('Serial number', a.serial_number or '—'),
        ('Category', a.category.name if a.category_id else '—'),
        ('Condition on issue', ho.condition_on_issue or '—'),
        ('Accessories', ho.accessories or '—'),
    ]))

    story.append(Paragraph('Recipient', sec))
    story.append(kv([
        ('Name', ho.recipient_name),
        ('Email', ho.recipient_email or '—'),
        ('Requisition', ho.requisition.requisition_number if ho.requisition_id else '—'),
    ]))

    story.append(Paragraph('Signatures', sec))
    sig_rows = [[
        Paragraph('IT RELEASES', label),
        Paragraph('FINANCE RECORDS', label),
        Paragraph('EMPLOYEE ACCEPTS', label),
    ], [
        Paragraph((ho.it_released_by.get_full_name() if ho.it_released_by_id else '—'), val),
        Paragraph((ho.finance_recorded_by.get_full_name() if ho.finance_recorded_by_id else '—'), val),
        Paragraph((ho.employee_accepted_by.get_full_name() if ho.employee_accepted_by_id else '—'), val),
    ], [
        Paragraph(_fmt_dt(ho.it_released_at), sub),
        Paragraph(_fmt_dt(ho.finance_recorded_at), sub),
        Paragraph(_fmt_dt(ho.employee_accepted_at), sub),
    ]]
    sig = Table(sig_rows, colWidths=[58 * mm, 58 * mm, 58 * mm])
    sig.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, LINE),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, LINE),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(sig)
    story.append(Spacer(1, 12))

    complete = 'COMPLETE — asset in use' if ho.is_complete else 'INCOMPLETE — not all signatures present'
    story.append(Paragraph(
        f'<font color="#6B7280" size="8">This note is the signed record required before the '
        f'asset leaves Finance control. Status: {complete}. '
        f'Generated by Omni · Asset Control &amp; Handover.</font>', sub))

    doc.build(story)
    return buf.getvalue()
