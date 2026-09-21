"""hris/training_certificate.py — the induction certificate, as a PDF.

A landscape A4 certificate on the Alpha Direct letterhead furniture, stating in
plain words that the holder completed the induction and understood the
Conditions of Service. It carries a serial and a QR code so HR — or a regulator
asking who was inducted — can verify it later without trusting the paper.

Reuses the letterhead artwork and helpers from `hris/letter_pdf.py` (same
watermark, same footer) rather than re-creating them, so the certificate looks
like every other Alpha Direct document. Brand colours are the real pair
(navy #0B0B3B, orange #F07F00).

Public entry point: render_certificate_pdf(cert) -> bytes.
"""
from __future__ import annotations

import io
import os

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

import logging

from hris.letter_pdf import _WATERMARK_IMG, FOOTER_LINE_1, _xml

# The official full-colour logo, used AS IT IS (CFO, 20-Sep-2026). The landscape
# letterhead banner was stretched to fit and the logo came out smudged; this is
# the clean artwork drawn at its own aspect ratio and nothing else.
_LOGO_IMG    = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'assets', 'logo-full-color.png')
_LOGO_ASPECT = 1226 / 2480          # height / width of the artwork
_LOGO_WIDTH  = 62 * mm

# The real house brand (machine-talk 20-Sep-2026: #0D1B2A/#F4A623 is drift).
NAVY   = HexColor('#0B0B3B')
ORANGE = HexColor('#F07F00')
INK    = HexColor('#1F2937')
MUTE   = HexColor('#6B7280')

log = logging.getLogger(__name__)

VERIFY_URL = 'https://omni.alphadirect.co.bw/verify-certificate'


def _decorate(c, doc):
    """Watermark, top banner, orange rule and footer — landscape."""
    w, h = landscape(A4)

    if os.path.exists(_WATERMARK_IMG):
        try:
            wm = 130 * mm
            c.saveState()
            c.setFillAlpha(0.18)
            c.drawImage(_WATERMARK_IMG, (w - wm) / 2, (h - wm) / 2 - 8 * mm,
                        width=wm, height=wm, mask='auto', preserveAspectRatio=True)
            c.restoreState()
        except OSError as exc:
            # Decoration only — a missing or unreadable watermark must never
            # stop somebody getting the certificate they earned. Say so though,
            # or the artwork can quietly disappear and nobody notices.
            log.warning('certificate: watermark not drawn — %s', exc)

    if os.path.exists(_LOGO_IMG):
        try:
            lw = _LOGO_WIDTH
            lh = lw * _LOGO_ASPECT
            c.drawImage(_LOGO_IMG, (w - lw) / 2, h - lh - 14 * mm,
                        width=lw, height=lh, mask='auto',
                        preserveAspectRatio=True)
        except OSError as exc:
            log.warning('certificate: logo not drawn — %s', exc)

    # Double border — navy outer, orange inner. Reads as a certificate at a
    # glance without any clip-art. The top edge clears the logo.
    top = h - (_LOGO_WIDTH * _LOGO_ASPECT) - 20 * mm
    c.setStrokeColor(NAVY)
    c.setLineWidth(2.2)
    c.rect(12 * mm, 12 * mm, w - 24 * mm, top - 12 * mm)
    c.setStrokeColor(ORANGE)
    c.setLineWidth(0.8)
    c.rect(15 * mm, 15 * mm, w - 30 * mm, top - 18 * mm)

    c.setFillColor(MUTE)
    c.setFont('Helvetica', 7.5)
    c.drawCentredString(w / 2, 18.5 * mm, FOOTER_LINE_1)


def render_certificate_pdf(cert) -> bytes:
    """Render the certificate for an induction `TrainingCertificate` row."""
    attempt = cert.attempt
    from hris.training_service import _sitting_order
    issued = len(_sitting_order(attempt)) or attempt.course.questions_per_paper
    return render_certificate(
        holder_name=cert.holder_name,
        course_title=attempt.course.title,
        percent=attempt.percent,
        score=attempt.score,
        total=issued,
        serial=cert.serial,
        verify_code=cert.verify_code,
        issued_at=cert.issued_at,
        statement=('and has confirmed that they have read and understood the '
                   'Company&rsquo;s Conditions of Service in full'),
    )


def render_certificate(*, holder_name: str, course_title: str, percent: int,
                       score: int | None = None, total: int | None = None,
                       serial: str = '', verify_code: str = '',
                       issued_at=None, statement: str = '',
                       signatories: tuple[str, str] = ('Chief Financial Officer',
                                                       'Compliance Officer')) -> bytes:
    """The house certificate. One page, landscape, the real logo, a QR.

    Shared on purpose: the induction and the AML compliance module must hand out
    the SAME document, or "an Alpha Direct certificate" stops meaning one thing.
    """
    buf = io.BytesIO()
    w, h = landscape(A4)

    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=28 * mm, rightMargin=28 * mm,
        topMargin=(62 * mm * (1226 / 2480)) + 30 * mm, bottomMargin=26 * mm,
        title=f'Certificate of Completion — {holder_name}',
        author='Alpha Direct Insurance Company (Pty) Ltd',
    )

    eyebrow = ParagraphStyle('eyebrow', fontName='Helvetica-Bold', fontSize=10,
                             leading=13, textColor=ORANGE, alignment=TA_CENTER,
                             spaceAfter=2)
    heading = ParagraphStyle('heading', fontName='Helvetica-Bold', fontSize=26,
                             leading=30, textColor=NAVY, alignment=TA_CENTER,
                             spaceAfter=6)
    lede = ParagraphStyle('lede', fontName='Helvetica', fontSize=10.5, leading=15,
                          textColor=MUTE, alignment=TA_CENTER, spaceAfter=4)
    name = ParagraphStyle('name', fontName='Helvetica-Bold', fontSize=22,
                          leading=26, textColor=INK, alignment=TA_CENTER,
                          spaceBefore=4, spaceAfter=6)
    body = ParagraphStyle('body', fontName='Helvetica', fontSize=11, leading=16,
                          textColor=INK, alignment=TA_CENTER)
    small = ParagraphStyle('small', fontName='Helvetica', fontSize=8.5, leading=12,
                           textColor=MUTE, alignment=TA_CENTER)

    issued = issued_at.strftime('%d %B %Y') if issued_at else ''
    detail = (f' ({score} of {total} questions answered correctly)'
              if score is not None and total else '')

    story = [
        Paragraph('ALPHA DIRECT INSURANCE COMPANY', eyebrow),
        Paragraph('Certificate of Completion', heading),
        Paragraph('This is to certify that', lede),
        Paragraph(_xml(holder_name), name),
        Paragraph(
            f'has completed the <b>{_xml(course_title)}</b> and passed the '
            f'assessment with a mark of <b>{percent}%</b>{detail}'
            + (', ' + statement if statement else '') + '.',
            body),
        Spacer(1, 8 * mm),
        Paragraph(f'Issued on {_xml(issued)} &nbsp;·&nbsp; Certificate No. '
                  f'<b>{_xml(serial)}</b>', body),
        Spacer(1, 10 * mm),
        Paragraph('Issued electronically by Omni on behalf of the Human Capital '
                  'Department.', small),
    ]
    if verify_code:
        story.append(Paragraph(
            f'Verify at {VERIFY_URL} using code <b>{_xml(verify_code)}</b>.', small))

    def _page(c, d):
        _decorate(c, d)
        # Signature lines. The AML certificate carried these before the two
        # designs were merged (CFO 20-Sep) and a training record for a regulated
        # firm is expected to name who stands behind it — so both certificates
        # carry them now, not just the one that used to.
        if signatories:
            left_title, right_title = signatories
            sig_y = 34 * mm
            c.setStrokeColor(MUTE)
            c.setLineWidth(0.5)
            c.setFont('Helvetica', 9)
            for x, title in ((58 * mm, left_title), (w - 58 * mm, right_title)):
                c.line(x - 34 * mm, sig_y, x + 34 * mm, sig_y)
                c.setFillColor(MUTE)
                c.drawCentredString(x, sig_y - 5.5 * mm, title)
        # QR to the verify page — ONLY when there is a code the verify endpoint
        # can actually match. Falling back to the serial printed a QR that
        # answered "not valid" when scanned, which is worse than no QR.
        if not verify_code:
            return
        try:
            qr = QrCodeWidget(f'{VERIFY_URL}?code={verify_code}')
            b = qr.getBounds()
            size = 22 * mm
            dr = Drawing(size, size,
                         transform=[size / (b[2] - b[0]), 0, 0,
                                    size / (b[3] - b[1]), 0, 0])
            dr.add(qr)
            # Centred between the two signature lines — anchored bottom-right
            # it sat on top of the Compliance Officer line.
            renderPDF.draw(dr, c, (w - size) / 2, 24 * mm)
        except (OSError, ValueError) as exc:
            log.warning('certificate: QR code not drawn — %s', exc)

    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return buf.getvalue()
