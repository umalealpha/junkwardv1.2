"""hris/letter_pdf.py — render an HR letter on the Alpha Direct letterhead.

Reproduces Oprah Mogomotsi's branded letterhead (the same file she asked to be
put in the omni document bank, 2026-07-15) as a print-ready PDF: the logo/swoosh
banner across the top, the faint Setswana watermark behind the text, and the
address footer — using her ORIGINAL artwork (hris/assets/letterhead_*.jpg), not
a re-creation. The letter body is auto-filled from the frozen sign-off snapshot
on a hris.LetterRequest, closes with the manager's sign-off block, and carries a
QR code + reference so a third party (bank, embassy) can verify it — the same
anti-forgery discipline as the payslip PDF (payroll/pdf.py).

Public entry point: render_letter_pdf(snap: dict) -> bytes.
"""
from __future__ import annotations

import io
import os
from datetime import date, datetime

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

# House brand (matches payroll/pdf.py + CFO branding directive).
NAVY   = HexColor('#0D1B2A')
ORANGE = HexColor('#F4A623')
INK    = HexColor('#1F2937')
MUTE   = HexColor('#6B7280')

_HERE          = os.path.dirname(os.path.abspath(__file__))
_HEADER_IMG    = os.path.join(_HERE, 'assets', 'letterhead_header.jpg')     # logo + swoosh banner
_WATERMARK_IMG = os.path.join(_HERE, 'assets', 'letterhead_watermark.jpg')  # faint Setswana word-cloud
_SEAL_IMG      = os.path.join(_HERE, 'assets', 'company_seal.png')          # round company stamp (from the UW tool)

# Header banner native aspect (1241 x 388 px) → full-bleed width, ~65.6mm tall.
_BANNER_ASPECT = 388 / 1241

FOOTER_LINE_1 = ('Botswana Innovation Hub, Plot 69184, Floor 2, Bar 2, '
                 'Block 8 Industrial, Gaborone')
FOOTER_LINE_2 = ('P.O. Box 26 ADC, Gaborone  •  (+267) 370 27 00 / 392 82 64  '
                 '•  www.alphadirect.co.bw')


# ── date helpers ────────────────────────────────────────────────────────────

def _as_date(v):
    if isinstance(v, (date, datetime)):
        return v
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d %B %Y'):
        try:
            return datetime.strptime(str(v), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _long_date(v) -> str:
    d = _as_date(v)
    return d.strftime('%d %B %Y') if d else str(v or '')


def _xml(s: str) -> str:
    return (str(s) or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ── the actual letter wording (per type) ────────────────────────────────────

def _employment_confirmation(snap: dict) -> tuple[str, list[str]]:
    """Return (RE line, [body paragraphs]) for an employment-confirmation letter.

    Written in the plain, precise register of an insurance lawyer and kept free
    of gendered pronouns (omni does not assume gender) — 'the above-named'.
    """
    name     = snap.get('full_name', '')
    num      = (snap.get('employee_number') or '').strip()
    title    = snap.get('job_title') or 'a member of staff'
    dept     = (snap.get('department') or '').strip()
    company  = snap.get('company_name') or 'Alpha Direct Insurance Company (Pty) Ltd'
    hired    = _long_date(snap.get('hire_date'))
    issued   = _long_date(snap.get('issued_date'))
    purpose  = (snap.get('purpose') or '').strip()

    id_clause   = f' (Employee No. {_xml(num)})' if num else ''
    dept_clause = f' within the {_xml(dept)} department' if dept else ''
    purpose_clause = (f'at the employee’s request for the purpose of {_xml(purpose)}, '
                      if purpose else 'at the employee’s request ')

    re_line = f'RE: CONFIRMATION OF EMPLOYMENT — {_xml(name).upper()}'
    paras = [
        (f'This letter confirms that {_xml(name)}{id_clause} is employed by '
         f'{_xml(company)} in the position of {_xml(title)}{dept_clause}.'),
        (f'The above-named has been in the continuous employ of the Company '
         f'since {_xml(hired)} and remains an active employee in good standing '
         f'as at {_xml(issued)}.'),
        (f'This confirmation is issued {purpose_clause}and reflects the records '
         f'of the Company as at the date of issue.'),
        ('Should you require any further information, please contact the Human '
         'Resources Department on hr@alphadirect.co.bw or +267 370 2700.'),
    ]
    return re_line, paras


_BODY_BUILDERS = {
    'employment_confirmation': _employment_confirmation,
}


def body_for(letter_type: str, snap: dict) -> tuple[str, list[str]]:
    builder = _BODY_BUILDERS.get(letter_type, _employment_confirmation)
    return builder(snap)


# ── page furniture (letterhead drawn on every page) ─────────────────────────

def _decorator(c, doc):
    w, h = A4
    # Faint Setswana watermark — centred, behind the text, drawn very light so
    # it never competes with the letter body / signature.
    if os.path.exists(_WATERMARK_IMG):
        try:
            wm = 145 * mm
            c.saveState()
            c.setFillAlpha(0.30)
            c.drawImage(_WATERMARK_IMG, (w - wm) / 2, (h - wm) / 2,
                        width=wm, height=wm, mask='auto', preserveAspectRatio=True)
            c.restoreState()
        except Exception:
            pass
    # Logo + swoosh banner — full-bleed across the top.
    if os.path.exists(_HEADER_IMG):
        try:
            bh = w * _BANNER_ASPECT
            c.drawImage(_HEADER_IMG, 0, h - bh, width=w, height=bh, mask='auto')
        except Exception:
            pass
    # Footer — thin navy rule + two centred address lines.
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.6)
    c.line(20 * mm, 20 * mm, w - 20 * mm, 20 * mm)
    c.setFillColor(MUTE)
    c.setFont('Helvetica', 7.5)
    c.drawCentredString(w / 2, 15.5 * mm, FOOTER_LINE_1)
    c.drawCentredString(w / 2, 12 * mm, FOOTER_LINE_2)


def _signature_block(snap: dict):
    name  = snap.get('signatory_name') or ''
    title = snap.get('signatory_title') or 'Manager'
    when  = _long_date(snap.get('issued_date'))
    lbl   = ParagraphStyle('siglbl', fontName='Helvetica', fontSize=8.5, textColor=MUTE, leading=12)
    strong = ParagraphStyle('signame', fontName='Helvetica-Bold', fontSize=10.5, textColor=NAVY, leading=14)
    inner = Table([
        [Paragraph('Yours faithfully,', lbl)],
        [Paragraph('for and on behalf of '
                   + _xml(snap.get('company_name') or 'Alpha Direct Insurance Company (Pty) Ltd'), lbl)],
        [Spacer(1, 10 * mm)],
        [Paragraph(_xml(name), strong)],
        [Paragraph(_xml(title), lbl)],
        [Paragraph(f'Signed off electronically via omni on {_xml(when)}.', lbl)],
    ], colWidths=[105 * mm])
    inner.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LINEABOVE', (0, 3), (0, 3), 0.6, NAVY),
    ]))
    # Company stamp (from the UW tool) applied beside the manager's signature.
    seal_cell = ''
    if os.path.exists(_SEAL_IMG):
        try:
            seal_cell = Image(_SEAL_IMG, width=34 * mm, height=34 * mm)
        except Exception:
            seal_cell = ''
    row = Table([[inner, seal_cell]], colWidths=[110 * mm, 40 * mm])
    row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return KeepTogether([Spacer(1, 6 * mm), row])


def _ref_qr_row(snap: dict, verify_url: str):
    """Reference + QR verify strip, sat at the foot of the letter body."""
    ref = snap.get('reference') or ''
    lbl = ParagraphStyle('reflbl', fontName='Helvetica', fontSize=7.5, textColor=MUTE, leading=11)
    left = Paragraph(
        f'<b>Reference:</b> {_xml(ref)}<br/>'
        'Scan the code to verify this letter with Alpha Direct.<br/>'
        f'{_xml(verify_url)}', lbl)
    qr_widget = QrCodeWidget(verify_url or ref or 'omni', barLevel='M')
    size = 20 * mm
    b = qr_widget.getBounds()
    dw, dh = b[2] - b[0], b[3] - b[1]
    draw = Drawing(size, size, transform=[size / dw, 0, 0, size / dh, 0, 0])
    draw.add(qr_widget)
    row = Table([[left, draw]], colWidths=[120 * mm, size])
    row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('LINEABOVE', (0, 0), (-1, 0), 0.4, HexColor('#D1D5DB')),
    ]))
    return KeepTogether([Spacer(1, 8 * mm), row])


# ── public entry point ──────────────────────────────────────────────────────

def render_letter_pdf(snap: dict, *, verify_url: str = '') -> bytes:
    """Render an A4 letter PDF from a LetterRequest.issued_snapshot dict.

    snap keys: full_name, employee_number, job_title, department,
    employment_status, company_name, hire_date, issued_date, addressee,
    purpose, reference, signatory_name, signatory_title, letter_type.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=25 * mm, rightMargin=25 * mm,
        topMargin=72 * mm,       # clear the full-bleed banner
        bottomMargin=26 * mm,    # clear the footer
        title=f"Alpha Direct — {snap.get('reference') or 'Letter'}",
        author='Alpha Direct Insurance Company (Pty) Ltd',
    )

    body = ParagraphStyle('body', fontName='Helvetica', fontSize=10.5,
                          leading=15.5, alignment=TA_JUSTIFY, textColor=INK,
                          spaceAfter=8)
    meta = ParagraphStyle('meta', fontName='Helvetica', fontSize=10.5,
                          leading=14, textColor=INK, alignment=TA_LEFT)
    addressee = ParagraphStyle('addr', fontName='Helvetica-Bold', fontSize=10.5,
                               leading=14, textColor=INK, spaceBefore=10, spaceAfter=8)
    re_style = ParagraphStyle('re', fontName='Helvetica-Bold', fontSize=11,
                              leading=15, textColor=NAVY, spaceBefore=4, spaceAfter=10)

    re_line, paras = body_for(snap.get('letter_type') or 'employment_confirmation', snap)

    flow = []
    flow.append(Paragraph(_long_date(snap.get('issued_date')), meta))
    flow.append(Paragraph(_xml(snap.get('addressee') or 'To Whom It May Concern'), addressee))
    flow.append(Paragraph(re_line, re_style))
    for p in paras:
        flow.append(Paragraph(p, body))
    # Optional extra paragraph HR added at sign-off (polished by the omni AI
    # helper). Inserted before the closing courtesy line already in `paras`…
    # so we append it here, before the signature.
    extra = (snap.get('extra_paragraph') or '').strip()
    if extra:
        flow.append(Paragraph(_xml(extra), body))
    flow.append(_signature_block(snap))
    flow.append(_ref_qr_row(snap, verify_url))

    doc.build(flow, onFirstPage=_decorator, onLaterPages=_decorator)
    return buf.getvalue()
