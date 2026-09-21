"""
procurement/pdf.py

Alpha Direct Purchase Order PDF — company letterhead edition.

CFO directive 2026-07-08: rebuilt on the REAL Alpha Direct letterhead the
CFO supplied —

  * the AlphaDirect logo (procurement/pdf_assets/logo.png),
  * the signature orange + navy sweep in the top-right corner (bezier
    wedges, mirroring the printed letterhead / cover-note stock),
  * the ruled two-line footer (Innovation Hub address + contacts) exactly
    like the cover-note letterhead, with banking + VAT above it,
  * the circular company stamp (procurement/pdf_assets/stamp.png, white
    knocked out) printed on APPROVED purchase orders as authentication,
    beside the approval block.

Document identity (CFO/Kao 2026-07-08): before approval the document is a
REQUEST FOR QUOTATION; once approved it is a PURCHASE ORDER and carries the
stamp. Serif display type (Times) echoes the Book Antiqua house style;
Helvetica carries labels and figures.

Layout (A4 portrait — two pages, CFO directive 2026-07-08):
  every page   letterhead sweep + logo (canvas), ruled footer (canvas)
  page 1       title block -> meta grid -> FROM | SUPPLIER cards ->
               lines table (navy header, zebra) -> totals (navy TOTAL bar) ->
               authorisation block + stamp (approved only). Order ONLY —
               no terms.
  page 2       TERMS AND CONDITIONS in legal fine print, two columns
               (repair terms on claims POs only; invoice + dispute
               conditions on every PO), with the QR verification block
               anchored bottom-centre (painter-drawn).

Stays dependency-light (reportlab only).
"""
from __future__ import annotations

import io
import os
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate,
    PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)


# ---------------------------------------------------------------------------
# Constants — Alpha Direct branding + default contact details
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
_ASSETS = _HERE / 'pdf_assets'
_LOGO_CANDIDATES = [
    # CFO 2026-07-09: the old reflection/"shadow" logo (logo.png) is retired on
    # purchase orders. Use the clean no-shadow mark (tight-cropped from the
    # design-system full-colour master, transparent background).
    _ASSETS / 'logo-clean.png',
    _REPO_ROOT / 'alpha-direct-design-system' / 'assets' / 'logo-full-color.png',
    _REPO_ROOT / 'frontend' / 'public' / 'brand' / 'logo-full-color.png',
]
# Compact round mark for the centre of the QR code — the wide header logo
# turns to mush at ~7mm; the round "AD" mark stays legible.
_LOGO_ROUND_CANDIDATES = [
    _REPO_ROOT / 'alpha-direct-design-system' / 'assets' / 'logo-round-variant.png',
    _REPO_ROOT / 'alpha-direct-design-system' / 'assets' / 'logo-compact-mark.png',
    _REPO_ROOT / 'frontend' / 'public' / 'brand' / 'logo-full-color.png',
]
_STAMP_PATH = _ASSETS / 'stamp.png'             # circular company stamp

NAVY    = colors.HexColor('#0D1B2A')
NAVY_2  = colors.HexColor('#1B3A5C')            # sweep ribbon (letterhead blue)
ORANGE  = colors.HexColor('#F47C20')            # letterhead sweep orange
AMBER   = colors.HexColor('#F4A623')            # accent (finance brand)
GREY    = colors.HexColor('#6B7280')
LINE    = colors.HexColor('#D7DBE0')
LIGHT   = colors.HexColor('#F5F7FB')

AD_HEADER_PHONE   = '+267 392 8264'
AD_HQ_ADDRESS     = [
    'Floor 2, Icon Building, Botswana Innovation Hub',
    'Plot 69184, Block 8 Industrial, Gaborone, Botswana',
]
AD_FOOTER_PHONE   = '+267 370 2700'
# Purchase orders direct suppliers to the invoices mailbox (per the T&Cs).
AD_FOOTER_EMAIL   = 'invoices@alphadirect.co.bw'
AD_FOOTER_WEB     = 'www.alphadirect.co.bw'
AD_FOOTER_VAT     = 'BW00000123907'
AD_BANK_LINE      = ('First National Bank of Botswana  ·  Corporate Branch 282267  ·  '
                     'A/c No 62403392335  ·  FIRNBWGX')
AD_BANK_OWNER     = 'Alpha Direct Insurance (Pty) Ltd'
# Footer lines mirror the printed letterhead (cover-note stock).
AD_FOOTER_LINE1   = ('Botswana Innovation Hub, Plot 69184, Floor 2, Bar 2, '
                     'Block 8 Industrial, Gaborone')
AD_FOOTER_LINE2   = ('P.O. Box 26 ADC, Gaborone  •  (+267) 370 27 00 / 392 82 64  •  '
                     'www.alphadirect.co.bw')

DEFAULT_VAT_RATE  = Decimal('0.14')   # Botswana VAT — used when lines carry no tax


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _money(v, ccy: str = 'BWP') -> str:
    n = Decimal(str(v or 0))
    sign = '-' if n < 0 else ''
    # Botswana convention: currency BEFORE the amount — "P2,000.00", never
    # "2,000.00 P" (CFO print review 2026-07-05; the old suffix style copied
    # the Odoo sample and read backwards). Foreign: "USD 1,000.00".
    prefix = 'P' if ccy == 'BWP' else f'{ccy} '
    return f'{sign}{prefix}{abs(n):,.2f}'


def _esc(s: str) -> str:
    """
    Escape &, <, > for reportlab's Paragraph mini-markup. Unescaped text is
    silently CORRUPTED, not rejected — '<T/A>' disappears from the output and
    '&' can render as a stray entity — so EVERY dynamic string that enters a
    Paragraph (descriptions, supplier names, claim refs, justification) must
    pass through here. Canvas drawString text does not need escaping.
    """
    return (s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _logo_path() -> str | None:
    for p in _LOGO_CANDIDATES:
        if p.exists():
            return str(p)
    return None


def _stamp_path() -> str | None:
    return str(_STAMP_PATH) if _STAMP_PATH.exists() else None


def _logo_round_path() -> str | None:
    for p in _LOGO_ROUND_CANDIDATES:
        if p.exists():
            return str(p)
    return None


def _po_verify_url(po) -> str:
    """Absolute link the QR code carries → the public verification page."""
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw')
    return f'{base}/api/verify/po/{po.pk}/'


def _draw_qr(canv, url: str, x: float, y: float,
             size: float = 30 * mm, logo_frac: float = 0.18):
    """
    Draw the verification QR code as PDF vector (navy modules) with the round
    AD mark centred on a white pad, bottom-left corner at (x, y).

    Emitted via renderPDF (vector) — no raster backend (rlPyCairo / renderPM)
    is required on the server. Error-correction level H (~30% of the symbol
    recoverable) keeps it scannable with the centre covered by the logo; the
    logo is kept small (~18% of the symbol) so plenty of margin remains.
    Verified end-to-end: the rendered PDF's QR decodes to _po_verify_url(po).
    """
    widget = QrCodeWidget(url, barLevel='H')
    widget.barFillColor = NAVY
    b = widget.getBounds()
    w, h = b[2] - b[0], b[3] - b[1]
    d = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    d.add(widget)
    renderPDF.draw(d, canv, x, y)

    logo = _logo_round_path()
    if logo:
        pad = size * (logo_frac + 0.06)
        pxy = (size - pad) / 2
        canv.setFillColor(colors.white)
        canv.roundRect(x + pxy, y + pxy, pad, pad, pad * 0.16, stroke=0, fill=1)
        ls = size * logo_frac
        lxy = (size - ls) / 2
        try:
            canv.drawImage(logo, x + lxy, y + lxy, width=ls, height=ls,
                           preserveAspectRatio=True, mask='auto')
        except Exception:        # noqa: BLE001
            pass


def _user_display_name(user) -> str:
    if user is None:
        return '—'
    full = (getattr(user, 'get_full_name', lambda: '')() or '').strip()
    return full or getattr(user, 'username', '—')


def _supplier_address_lines(supplier) -> list[str]:
    lines = [supplier.name]
    if getattr(supplier, 'address', None):
        for ln in str(supplier.address).splitlines():
            ln = ln.strip()
            if ln:
                lines.append(ln)
    if getattr(supplier, 'phone', None):
        lines.append(supplier.phone)
    if getattr(supplier, 'tax_id', None):
        lines.append(f'VAT: {supplier.tax_id}')
    return lines


def _company_address_lines(company) -> list[str]:
    if company is None:
        return ['Alpha Direct Insurance'] + AD_HQ_ADDRESS
    lines = [company.name]
    if getattr(company, 'address', None):
        for ln in str(company.address).splitlines():
            ln = ln.strip()
            if ln:
                lines.append(ln)
    # A shipping block with no street reads unfinished — suppliers must know
    # where to deliver. Fall back to the HQ address when the Company row
    # carries none.
    if len(lines) == 1:
        lines += AD_HQ_ADDRESS
    return lines


# ---------------------------------------------------------------------------
# Terms & conditions — condensed fine-print edition (CFO directive
# 2026-07-08): page 1 carries the order only; ALL terms print on page 2 in
# small type, like the fine print of a legal document. Wording tightened
# from the original sample (grammar fixed, redundancy cut, meaning kept);
# payment within 30 days of the STATEMENT date is stated explicitly.
# ---------------------------------------------------------------------------

_REPAIR_TERMS = [
    ('1. Acceptance:',
     "Acceptance of this order binds the repairer to Alpha Direct's code of "
     "conduct and policies."),
    ('2. Authorisation of Repairs:',
     "No repairs may commence without written authorisation from the insurer "
     "or its appointed assessor, and all work must follow that authorisation "
     "exactly. The repairer may not accept instructions from the policyholder "
     "that deviate from it without the insurer's written consent."),
    ('3. Use of Parts:',
     "Only approved parts may be used, in line with the insurer's instructions "
     "on new, used or alternative parts. Additional costs or changes require "
     "the prior approval of the insurer or its assessor."),
    ('4. Timeliness:',
     "Repairs must begin within 24 working hours of written authorisation, "
     "provided the necessary parts and materials are available, and be "
     "completed as soon as practically possible. An estimated completion date "
     "must be given to the insurer."),
    ('5. Workmanship Guarantee:',
     "Repair work is guaranteed for one year and paintwork for three years. "
     "Where the repairer's own standard guarantees are more favourable, those "
     "terms apply."),
    ('6. Rectification of Poor Workmanship:',
     "The repairer must rectify, at no additional cost, any repairs the "
     "insurer, its assessor or the policyholder finds unsatisfactory. Failing "
     "this, the insurer may have the repairs completed elsewhere and recover "
     "the cost from the repairer."),
    ('7. Subcontracting:',
     "No repair work may be subcontracted without the insurer's approval. The "
     "repairer must notify the insurer immediately if it cannot meet its "
     "obligations."),
    ('8. Cession and Factoring:',
     "The right to receive payment may not be transferred without the "
     "insurer's prior written consent."),
    ('9. Invoicing:',
     "Invoices must comply with Botswana Unified Revenue Service (BURS) "
     "regulations and the insurer's format: clearly marked as VAT invoices, "
     "with itemised descriptions, accurate VAT registration details, and "
     "details of work performed, hours worked and reimbursable expenses. "
     "Electronic invoices are accepted if marked \"copy\" and non-editable. "
     "Supplier invoices for parts authorised by the insurer must be attached. "
     "Send all invoices and supporting documents to invoices@alphadirect.co.bw."),
    ('10. Payments:',
     "Payment will be made 30 days from the statement date unless otherwise "
     "agreed in writing. Disputed items may delay payment until resolved."),
    ('11. Insurance Requirements:',
     "The repairer must maintain comprehensive cover, including Motor Traders, "
     "Liability, Product and Theft insurance, with the insurer named as a "
     "beneficiary with insurable interest."),
    ('12. Dispute Resolution:',
     "Disputes regarding repairs will be addressed through the agreed "
     "dispute-resolution mechanism, which may include mediation or "
     "arbitration depending on the value of the dispute."),
]

_INVOICE_CONDITIONS = [
    ('13. Payment Terms:',
     "Payments will be made 30 days from the <i>statement date</i>, not the "
     "invoice date."),
    ('14. KYC Compliance:',
     "Payment is made only to KYC-compliant suppliers. If KYC documents are "
     "outstanding, send them to claimsdept@alphadirect.co.bw as soon as the "
     "order is dispatched."),
    ('15. Repair Notes:',
     "Satisfactory repair notes must accompany any work involving labour or "
     "repairs for payment to be processed on time."),
    ('16. Document Submission:',
     "Send all statements and invoices to invoices@alphadirect.co.bw, with "
     "each invoice attached to its corresponding order."),
    ('17. Discrepancies:',
     "Any discrepancies or issues regarding the job or an invoice must be "
     "clearly noted, with reasons given."),
]

_DISPUTE_RESOLUTION = [
    ('18. Pre-Repair and Repair Disputes:',
     "Alpha Direct will investigate the dispute and the repairer's supporting "
     "evidence and respond promptly, and may inspect the vehicle and discuss "
     "the issue with the repairer. Misunderstandings between the repairer and "
     "the insured will be mediated by an Alpha Direct representative. If a "
     "repair cost cannot be agreed, Alpha Direct may engage another repairer."),
    ('19. Mediation and Arbitration:',
     "Disputes under this agreement should first be addressed through "
     "negotiation, aiming for resolution within 14 days. If unresolved, "
     "disputes up to P100,000 may be referred to binding arbitration before a "
     "mutually agreed arbitrator with at least 10 years' experience in "
     "insurance claims; disputes above P100,000 may be pursued through the "
     "courts under the laws of Botswana."),
]

# Protective boilerplate for the insurer (CFO 2026-07-08: "the PO is a legal
# document — tighten it so I don't lose any cases"). Printed on EVERY PO.
_GENERAL_CONDITIONS = [
    ('20. Without Prejudice — No Admission of Liability:',
     "Where this order is issued in connection with an insurance claim, it is "
     "issued on a without-prejudice basis. It is not an admission of "
     "liability by the insurer under any policy or at law, and all of the "
     "insurer's rights and defences are expressly reserved."),
    ('21. Scope and Variation:',
     "This order covers only the goods and services stated in it. Variations, "
     "extras or price increases are not payable unless authorised by the "
     "insurer in writing before the work is performed."),
    ('22. Cancellation:',
     "The insurer may cancel this order in writing at any time before work "
     "commences, and may cancel any unperformed part where the supplier is in "
     "breach of these terms. The insurer's liability on cancellation is "
     "limited to authorised work already completed."),
    ('23. Set-Off:',
     "The insurer may set off against any payment due under this order any "
     "amount the supplier owes the insurer."),
    ('24. Entire Agreement — No Waiver:',
     "This order, the repair authorisation and these terms constitute the "
     "entire agreement and prevail over any terms of the supplier. No "
     "indulgence or delay by the insurer in enforcing these terms operates as "
     "a waiver of its rights."),
    ('25. Governing Law:',
     "This order is governed by and construed under the laws of the Republic "
     "of Botswana."),
]


# ---------------------------------------------------------------------------
# Page letterhead painter — sweep + logo (header), ruled address (footer)
# ---------------------------------------------------------------------------

def _draw_sweep(canv, w, h):
    """The letterhead's signature top-right corner: a broad orange curve with
    a navy ribbon along its inner edge. Navy wedge first (slightly larger),
    orange wedge on top — the navy shows as the trailing edge, exactly like
    the printed stock."""
    p = canv.beginPath()
    p.moveTo(w - 88 * mm, h)
    p.curveTo(w - 36 * mm, h - 10 * mm, w - 11 * mm, h - 34 * mm, w, h - 66 * mm)
    p.lineTo(w, h)
    p.close()
    canv.setFillColor(NAVY_2)
    canv.drawPath(p, stroke=0, fill=1)
    p = canv.beginPath()
    p.moveTo(w - 80 * mm, h)
    p.curveTo(w - 31 * mm, h - 9 * mm, w - 9 * mm, h - 30 * mm, w, h - 57 * mm)
    p.lineTo(w, h)
    p.close()
    canv.setFillColor(ORANGE)
    canv.drawPath(p, stroke=0, fill=1)


def _make_painter():
    """Sticky letterhead on every page: corner sweep + logo up top, the
    ruled two-line address footer (with banking + VAT + page number) below."""
    logo = _logo_path()

    def paint(canv, doc):
        canv.saveState()
        w, h = A4

        _draw_sweep(canv, w, h)
        if logo:
            try:
                canv.drawImage(
                    logo, 18 * mm, h - 30 * mm,
                    width=44 * mm, height=20 * mm,
                    preserveAspectRatio=True, anchor='nw', mask='auto',
                )
            except Exception:        # noqa: BLE001
                pass

        canv.setFont('Helvetica', 7.5)
        canv.setFillColor(GREY)
        canv.drawCentredString(
            w / 2, 21.5 * mm,
            f'Banking: {AD_BANK_OWNER}  ·  {AD_BANK_LINE}  ·  VAT No. {AD_FOOTER_VAT}',
        )
        canv.setStrokeColor(NAVY)
        canv.setLineWidth(0.6)
        canv.line(24 * mm, 18.5 * mm, w - 24 * mm, 18.5 * mm)
        canv.setFillColor(NAVY)
        canv.setFont('Helvetica', 8)
        canv.drawCentredString(w / 2, 14.5 * mm, AD_FOOTER_LINE1)
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.4)
        canv.line(24 * mm, 12 * mm, w - 24 * mm, 12 * mm)
        canv.setFillColor(GREY)
        canv.drawCentredString(w / 2, 8 * mm, AD_FOOTER_LINE2)
        canv.setFont('Helvetica', 7)
        canv.drawRightString(w - 18 * mm, 8 * mm, f'Page {doc.page}')
        canv.restoreState()

    return paint


def _make_terms_painter(qr_url: str):
    """
    Painter for the terms page (page 2): the standard letterhead, a centred
    TERMS AND CONDITIONS heading, and the verification QR block anchored
    bottom-centre. Drawing the QR on the canvas (not in the flow) pins it to
    the page regardless of how much fine print the columns carry.
    """
    base = _make_painter()

    def paint(canv, doc):
        base(canv, doc)
        canv.saveState()
        w, h = A4

        canv.setFillColor(NAVY)
        canv.setFont('Times-Bold', 14)
        canv.drawCentredString(w / 2, h - 41 * mm, 'TERMS AND CONDITIONS')
        canv.setFillColor(ORANGE)
        canv.rect(w / 2 - 20 * mm, h - 43.4 * mm, 40 * mm, 0.8 * mm,
                  stroke=0, fill=1)

        size = 30 * mm
        _draw_qr(canv, qr_url, (w - size) / 2, 34 * mm, size)
        canv.setFillColor(NAVY)
        canv.setFont('Helvetica-Bold', 8.5)
        canv.drawCentredString(
            w / 2, 29.5 * mm,
            'Scan the QR code for acceptance or authentication.')
        canv.setFillColor(GREY)
        canv.setFont('Helvetica', 6.3)
        canv.drawCentredString(w / 2, 26.3 * mm, qr_url)
        canv.restoreState()

    return paint


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def generate_po_pdf(po) -> bytes:
    """
    po: procurement.models.PurchaseOrder
    Returns: raw PDF bytes on the Alpha Direct letterhead.
    """
    buf = io.BytesIO()

    # Document identity — RFQ before approval, Purchase Order after
    # (CFO/Kao directive 2026-07-08).
    _approved_states = {
        po.Status.APPROVED, po.Status.PARTIALLY_RECEIVED,
        po.Status.FULLY_RECEIVED, po.Status.CLOSED,
    }
    is_approved = po.status in _approved_states
    doc_label = 'PURCHASE ORDER' if is_approved else 'REQUEST FOR QUOTATION'

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=36 * mm, bottomMargin=28 * mm,
        title=f'{doc_label.title()} {po.po_number}',
        author='Alpha Direct Insurance',
    )

    company = po.company if po.company_id else None

    frame = Frame(
        18 * mm, 28 * mm,
        A4[0] - 36 * mm, A4[1] - 64 * mm,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        showBoundary=0,
    )
    # Terms page (page 2): fine print in two columns between the heading
    # (painter-drawn, ~h-43mm) and the QR block (painter-drawn, bottom).
    qr_url = _po_verify_url(po)
    _col_gap = 8 * mm
    _col_w = (A4[0] - 36 * mm - _col_gap) / 2
    _col_h = A4[1] - 48 * mm - 70 * mm
    terms_frames = [
        Frame(18 * mm, 70 * mm, _col_w, _col_h,
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
              showBoundary=0),
        Frame(18 * mm + _col_w + _col_gap, 70 * mm, _col_w, _col_h,
              leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
              showBoundary=0),
    ]
    doc.addPageTemplates([
        PageTemplate(id='ad_po', frames=[frame], onPage=_make_painter()),
        PageTemplate(id='ad_terms', frames=terms_frames,
                     onPage=_make_terms_painter(qr_url)),
    ])

    # ── Styles — serif display (Book Antiqua house style), Helvetica detail ─
    styles = getSampleStyleSheet()
    body = ParagraphStyle('body', parent=styles['Normal'],
                          fontName='Helvetica', fontSize=9.5, leading=13,
                          textColor=NAVY)
    title = ParagraphStyle('title', parent=body, fontName='Times-Bold',
                           fontSize=27, leading=30, textColor=NAVY)
    title_no = ParagraphStyle('title_no', parent=body, fontName='Helvetica-Bold',
                              fontSize=12, leading=15, textColor=ORANGE,
                              spaceBefore=2)
    meta_label = ParagraphStyle('meta_label', parent=body,
                                fontName='Helvetica-Bold', fontSize=7,
                                leading=9, textColor=GREY)
    meta_value = ParagraphStyle('meta_value', parent=body,
                                fontName='Helvetica-Bold', fontSize=10,
                                leading=13, textColor=NAVY)
    card_label = ParagraphStyle('card_label', parent=body,
                                fontName='Helvetica-Bold', fontSize=7.5,
                                leading=10, textColor=ORANGE)
    card_body = ParagraphStyle('card_body', parent=body, fontSize=9, leading=12)
    italic_grey = ParagraphStyle('italic_grey', parent=body,
                                 fontName='Helvetica-Oblique', fontSize=8.5,
                                 textColor=GREY, leading=11.5)
    # Wrapping cell styles — plain strings do NOT wrap in a reportlab Table.
    cell = ParagraphStyle('cell', parent=body, fontSize=9, leading=11.5)
    cell_r = ParagraphStyle('cell_r', parent=cell, alignment=2)
    head_cell = ParagraphStyle('head_cell', parent=body,
                               fontName='Helvetica-Bold', fontSize=8,
                               leading=10, textColor=colors.white)
    head_cell_r = ParagraphStyle('head_cell_r', parent=head_cell, alignment=2)

    flow: list = []
    usable = A4[0] - 36 * mm   # 174mm

    # ── Title block ───────────────────────────────────────────────────────
    flow.append(Paragraph(doc_label, title))
    # NB: keep to WinAnsi glyphs — '№' is outside reportlab's built-in
    # encoding and prints as a solid box.
    flow.append(Paragraph(f'No. {_esc(po.po_number or "(draft)")}', title_no))
    flow.append(Spacer(1, 4))
    keyline = Table([['']], colWidths=[usable], rowHeights=[2.2])
    keyline.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), ORANGE),
                                 ('TOPPADDING', (0, 0), (-1, -1), 0),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    flow.append(keyline)
    flow.append(Spacer(1, 10))

    # ── Meta grid ─────────────────────────────────────────────────────────
    rep_name = _user_display_name(getattr(po, 'created_by', None))
    issue_str = po.issue_date.strftime('%d %B %Y') if po.issue_date else '—'
    expected_str = (po.expected_delivery_date.strftime('%d %B %Y')
                    if getattr(po, 'expected_delivery_date', None) else '—')
    your_ref = (po.related_claim_reference or '—').strip()

    meta = Table([
        [Paragraph('ORDER DATE', meta_label),
         Paragraph('EXPECTED DELIVERY', meta_label),
         Paragraph('PURCHASE REPRESENTATIVE', meta_label),
         Paragraph('REFERENCE / CLAIM', meta_label)],
        [Paragraph(issue_str, meta_value),
         Paragraph(expected_str, meta_value),
         Paragraph(_esc(rep_name), meta_value),
         Paragraph(_esc(your_ref), meta_value)],
    ], colWidths=[usable * 0.21, usable * 0.24, usable * 0.30, usable * 0.25])
    meta.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 1),
        ('LINEBELOW', (0, 1), (-1, 1), 0.4, LINE),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 6),
    ]))
    flow.append(meta)
    flow.append(Spacer(1, 10))

    # ── FROM | SUPPLIER cards ────────────────────────────────────────────
    ship_lines = _company_address_lines(company) + [AD_HEADER_PHONE]
    sup_lines = _supplier_address_lines(po.supplier)
    from_para = Paragraph('<br/>'.join(_esc(ln) for ln in ship_lines), card_body)
    sup_para = Paragraph('<br/>'.join(_esc(ln) for ln in sup_lines), card_body)
    gap = 6 * mm
    card_w = (usable - gap) / 2
    cards = Table(
        [[Paragraph('FROM', card_label), '', Paragraph('SUPPLIER', card_label)],
         [from_para, '', sup_para]],
        colWidths=[card_w, gap, card_w],
    )
    cards.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (0, 1), LIGHT),
        ('BACKGROUND', (2, 0), (2, 1), LIGHT),
        ('LINEBEFORE', (0, 0), (0, 1), 2.2, NAVY),
        ('LINEBEFORE', (2, 0), (2, 1), 2.2, ORANGE),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 1),
        ('TOPPADDING', (0, 1), (-1, 1), 1),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
    ]))
    flow.append(cards)
    flow.append(Spacer(1, 12))

    # ── Lines table ──────────────────────────────────────────────────────
    # Columns: # | Description | Qty | Unit Price | VAT | Amount.
    # Per-line Delivery dropped — it repeated one date on every row (noise);
    # delivery lives in the meta grid (CFO redesign 2026-07-08).
    ccy = po.currency_code_id or 'BWP'
    rows = [[
        Paragraph('#', head_cell),
        Paragraph('DESCRIPTION', head_cell),
        Paragraph('QTY', head_cell_r),
        Paragraph('UNIT PRICE', head_cell_r),
        Paragraph('VAT', head_cell_r),
        Paragraph('AMOUNT', head_cell_r),
    ]]
    # Order by the user-set sequence (drag-to-reorder, Kao 2026-07-08), then
    # creation time. NEVER by id — it is a UUID and randomised the rows.
    line_qs = list(
        po.lines.select_related('account', 'tax_code')
        .order_by('sequence', 'created_at', 'id')
    )

    untaxed_total = ZERO
    for i, ln in enumerate(line_qs, start=1):
        qty = Decimal(str(ln.quantity or 0))
        unit = Decimal(str(ln.unit_price or 0))
        amount = qty * unit
        untaxed_total += amount
        # Show the line's ACTUAL VAT — a no-tax line (e.g. the excess
        # deduction) must NOT read "VAT 14%".
        if getattr(ln, 'tax_code_id', None):
            rate = getattr(ln.tax_code, 'rate', None)
            tax_label = (f'{float(rate):g}%' if rate is not None else 'VAT')
        else:
            tax_label = '—'
        qty_str = f'{qty:,.0f}' if qty == qty.to_integral_value() else f'{qty:,.3f}'
        rows.append([
            Paragraph(str(i), cell),
            Paragraph(_esc(ln.description), cell),
            Paragraph(qty_str, cell_r),
            Paragraph(f'{unit:,.2f}', cell_r),
            Paragraph(tax_label, cell_r),
            Paragraph(_money(amount, ccy), cell_r),
        ])
    if len(rows) == 1:
        rows.append([Paragraph('—', cell),
                     Paragraph('(no lines on this order)', cell),
                     '', '', '', ''])

    col_w = [8 * mm, 76 * mm, 14 * mm, 24 * mm, 16 * mm, 36 * mm]
    lines_tbl = Table(rows, colWidths=col_w, repeatRows=1)
    lines_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT]),
        ('LINEBELOW', (0, -1), (-1, -1), 0.6, NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 1), (-1, -1), 5.5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    flow.append(lines_tbl)
    flow.append(Spacer(1, 10))

    # ── Totals (right-aligned; navy TOTAL bar) ───────────────────────────
    # Trust the PO's stored header totals whenever the PO carries any —
    # deriving 14% on no-VAT lines printed a Total that contradicted
    # po.total_amount on a supplier-facing document.
    if (po.total_amount or ZERO) != ZERO or (po.tax_total or ZERO) != ZERO:
        vat_amt = Decimal(str(po.tax_total or ZERO))
        untaxed = Decimal(str(po.subtotal or untaxed_total))
        total_amt = Decimal(str(po.total_amount or (untaxed + vat_amt)))
    else:
        untaxed = untaxed_total
        vat_amt = (untaxed * DEFAULT_VAT_RATE).quantize(Decimal('0.01'))
        total_amt = untaxed + vat_amt

    t_lbl = ParagraphStyle('t_lbl', parent=body, fontSize=9.5, alignment=2,
                           textColor=NAVY)
    t_val = ParagraphStyle('t_val', parent=t_lbl, fontName='Helvetica-Bold')
    t_total_lbl = ParagraphStyle('t_total_lbl', parent=body,
                                 fontName='Times-Bold', fontSize=12,
                                 alignment=2, textColor=colors.white)
    t_total_val = ParagraphStyle('t_total_val', parent=t_total_lbl,
                                 fontName='Helvetica-Bold', fontSize=12)
    # When the PO carries a discount (PR #336, restored 2026-07-08): show the
    # gross subtotal and the money taken off, then the net figures VAT is
    # actually struck on. subtotal is stored NET of the discount.
    disc_total = Decimal(str(getattr(po, 'discount_total', None) or ZERO))
    totals_data = []
    if disc_total > ZERO:
        dpct = Decimal(str(po.discount_percent or ZERO))
        pct_str = f'{dpct:.2f}'.rstrip('0').rstrip('.')
        totals_data += [
            [Paragraph('Subtotal (gross)', t_lbl),
             Paragraph(_money(untaxed + disc_total, ccy), t_val)],
            [Paragraph(f'Discount ({pct_str}%)', t_lbl),
             Paragraph('-' + _money(disc_total, ccy), t_val)],
        ]
    totals_data += [
        [Paragraph('Subtotal (excl. VAT)', t_lbl),
         Paragraph(_money(untaxed, ccy), t_val)],
        [Paragraph('VAT', t_lbl), Paragraph(_money(vat_amt, ccy), t_val)],
        [Paragraph('TOTAL', t_total_lbl),
         Paragraph(_money(total_amt, ccy), t_total_val)],
    ]
    _last = len(totals_data) - 1
    totals_tbl = Table(totals_data, colWidths=[52 * mm, 40 * mm], hAlign='RIGHT')
    totals_tbl.setStyle(TableStyle([
        ('LINEBELOW', (0, _last - 1), (-1, _last - 1), 0.4, LINE),
        ('BACKGROUND', (0, _last), (-1, _last), NAVY),
        ('LINEABOVE', (0, _last), (-1, _last), 1.6, ORANGE),
        ('TOPPADDING', (0, 0), (-1, _last - 1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, _last - 1), 4),
        ('TOPPADDING', (0, _last), (-1, _last), 7),
        ('BOTTOMPADDING', (0, _last), (-1, _last), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    flow.append(KeepTogether(totals_tbl))
    flow.append(Spacer(1, 8))

    # ── Reference / justification note ───────────────────────────────────
    free_text_bits: list[str] = []
    if po.related_claim_reference:
        free_text_bits.append(f'Claim Ref: {_esc(po.related_claim_reference)}')
    if po.justification:
        free_text_bits.append(_esc(po.justification.strip()))
    if free_text_bits:
        flow.append(Paragraph('<br/>'.join(free_text_bits), italic_grey))
        flow.append(Spacer(1, 8))

    # ── Authorisation block + company stamp (APPROVED only) ─────────────
    # The circular stamp is the authentication the CFO mandated (2026-07-08):
    # it prints ONLY once the order is approved in omni — an RFQ carries a
    # plain-italic status note instead.
    if is_approved:
        fm_by = _user_display_name(getattr(po, 'fm_approved_by', None))
        fm_at = (po.fm_approved_at.strftime('%d %B %Y')
                 if getattr(po, 'fm_approved_at', None) else '')
        cfo_by = _user_display_name(getattr(po, 'cfo_approved_by', None))
        cfo_at = (po.cfo_approved_at.strftime('%d %B %Y')
                  if getattr(po, 'cfo_approved_at', None) else '')
        auth_lines = ['<b>AUTHORISATION</b>',
                      'This Purchase Order was raised and approved in the '
                      'Alpha Direct omni system.']
        if fm_by != '—':
            auth_lines.append(f'Approved: {_esc(fm_by)}'
                              + (f' — {fm_at}' if fm_at else ''))
        if cfo_by != '—' and cfo_by != fm_by:
            auth_lines.append(f'Final approval: {_esc(cfo_by)}'
                              + (f' — {cfo_at}' if cfo_at else ''))
        auth_lines.append(f'For and on behalf of {AD_BANK_OWNER}.')
        auth_para = Paragraph('<br/>'.join(auth_lines),
                              ParagraphStyle('auth', parent=body, fontSize=9,
                                             leading=13))
        stamp = _stamp_path()
        if stamp:
            stamp_img = Image(stamp, width=34 * mm, height=35.4 * mm)
            auth_tbl = Table([[auth_para, stamp_img]],
                             colWidths=[usable - 44 * mm, 44 * mm])
        else:
            auth_tbl = Table([[auth_para]], colWidths=[usable])
        auth_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT),
            ('LINEBEFORE', (0, 0), (0, -1), 2.2, ORANGE),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        flow.append(KeepTogether(auth_tbl))
    else:
        flow.append(Paragraph(
            'This document is a Request for Quotation — it becomes an '
            'authorised Purchase Order (with the company stamp) once approved '
            'in the Alpha Direct omni system.', italic_grey))

    # ── Terms & Conditions — page 2, legal fine print ────────────────────
    # CFO directive 2026-07-08: page 1 is the clean order; ALL terms move to
    # page 2 in small type (two columns), with the QR verification block
    # anchored at the bottom of that page (drawn by _make_terms_painter).
    # Repair-order T&Cs only belong on CLAIMS POs (CFO print review
    # 2026-07-05). Every PO carries invoice-payment + dispute conditions.
    fp_section = ParagraphStyle('fp_section', parent=body,
                                fontName='Helvetica-Bold', fontSize=8,
                                leading=10, textColor=NAVY,
                                spaceBefore=7, spaceAfter=2)
    fp_head = ParagraphStyle('fp_head', parent=body,
                             fontName='Helvetica-Bold', fontSize=6.8,
                             leading=8.4, textColor=NAVY,
                             spaceBefore=3.5, spaceAfter=0.5)
    fp_body = ParagraphStyle('fp_body', parent=body, fontSize=6.3,
                             leading=8.2, alignment=4,   # justified
                             textColor=colors.HexColor('#333F4E'),
                             spaceAfter=1.5)

    flow.append(NextPageTemplate('ad_terms'))
    flow.append(PageBreak())

    if po.department == po.Department.CLAIMS:
        flow.append(Paragraph('Terms and Conditions for Repair Orders',
                              fp_section))
        for head, txt in _REPAIR_TERMS:
            flow.append(Paragraph(head, fp_head))
            flow.append(Paragraph(txt, fp_body))

    flow.append(Paragraph('Invoice Payment Conditions', fp_section))
    for head, txt in _INVOICE_CONDITIONS:
        flow.append(Paragraph(head, fp_head))
        flow.append(Paragraph(txt, fp_body))

    flow.append(Paragraph('Dispute Resolution', fp_section))
    for head, txt in _DISPUTE_RESOLUTION:
        flow.append(Paragraph(head, fp_head))
        flow.append(Paragraph(txt, fp_body))

    flow.append(Paragraph('General Conditions', fp_section))
    for head, txt in _GENERAL_CONDITIONS:
        flow.append(Paragraph(head, fp_head))
        flow.append(Paragraph(txt, fp_body))

    doc.build(flow)
    return buf.getvalue()
