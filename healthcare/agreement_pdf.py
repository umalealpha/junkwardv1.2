"""healthcare/agreement_pdf.py — filled AFA Service Provider Network Agreement.

Renders the FULL verbatim agreement (single source: healthcare/agreement_text.txt)
with the vendor's completed blanks filled in, AFA brand colours + logo, a proper
letterhead on every page, a boxed signature block with the practitioner's
electronic signature, page numbers, and a "SIGNED COPY" mark. This is the
legally binding, print-ready artefact emailed to AFA — the complete agreement,
not a summary.

Design: editorial minimalism (per prat-skill design-guides) — restrained
maroon headings, orange accent used only on rules/badge, generous whitespace,
justified body. Built with reportlab (already a project dependency).
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
from datetime import datetime

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, Image, KeepTogether, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

log = logging.getLogger(__name__)

AFA_MAROON = HexColor("#7A2E22")
AFA_ORANGE = HexColor("#E2611F")
INK = HexColor("#1a1410")
MUTE = HexColor("#8a7a75")

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEXT_PATH = os.path.join(_HERE, "agreement_text.txt")
_LOGO_PATH = os.path.join(_HERE, "afa-logo.png")

_HEADING_RX = re.compile(
    r"^(SERVICE PROVIDER NETWORK AGREEMENT|MEMORANDUM OF AGREEMENT|PREAMBLE|"
    r"WHEREAS|NOW THEREFORE|CLAUSE \d+|SCHEDULE [A-Z]|WITNESSES)\b"
)
_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]


def _xml(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fill_tokens(text: str, rec) -> str:
    signed = getattr(rec, "signed_at", None) or datetime.utcnow()
    g = lambda a, d="": (getattr(rec, a, None) or d)
    mapping = {
        "PRACTITIONER_NAME": g("practitioner_name") or g("trading_name"),
        "TRADING_NAME": g("trading_name") or g("company_name"),
        "DISCIPLINE": g("discipline") or g("service_category"),
        "REPRESENTATIVE": g("representative_name"),
        "CAPACITY": g("representative_capacity"),
        "PRINCIPAL_PLACE": g("principal_place_of_business") or g("practice_address"),
        "EFFECTIVE_DATE": g("effective_date"),
        "TEL": g("contact_tel"),
        "EMAIL": g("contact_email"),
        "SIGNATORY": g("signatory_full_name"),
        "SIGN_DAY": str(signed.day),
        "SIGN_MONTH": _MONTHS[signed.month - 1],
        "SIGN_YEAR": str(signed.year),
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(mapping.get(m.group(1), m.group(0))), text)


def _decorator(ref: str):
    """Return an onPage callback that draws the letterhead + footer on every page."""
    def _draw(c, doc):
        w, h = A4
        # Letterhead band.
        c.setFillColor(AFA_MAROON)
        c.rect(0, h - 24 * mm, w, 24 * mm, fill=1, stroke=0)
        if os.path.exists(_LOGO_PATH):
            try:
                c.drawImage(_LOGO_PATH, 18 * mm, h - 20 * mm, width=15 * mm, height=10 * mm,
                            mask="auto", preserveAspectRatio=True)
            except Exception:  # noqa: BLE001
                pass
        c.setFillColor(white)
        c.setFont("Times-Bold", 12)
        c.drawString(37 * mm, h - 12 * mm, "Associated Fund Administrators Botswana")
        c.setFillColor(HexColor("#f0d9d0"))
        c.setFont("Times-Roman", 8)
        c.drawString(37 * mm, h - 16.5 * mm, "Service Provider Network Agreement")
        # SIGNED COPY badge (orange accent — used sparingly).
        c.setFillColor(AFA_ORANGE)
        c.roundRect(w - 48 * mm, h - 17 * mm, 30 * mm, 7 * mm, 2, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Times-Bold", 8)
        c.drawCentredString(w - 33 * mm, h - 14.4 * mm, "SIGNED COPY")
        # Footer: thin orange rule + ref + page number.
        c.setStrokeColor(AFA_ORANGE)
        c.setLineWidth(0.5)
        c.line(18 * mm, 15 * mm, w - 18 * mm, 15 * mm)
        c.setFillColor(MUTE)
        c.setFont("Times-Roman", 7.5)
        c.drawCentredString(
            w / 2, 10.5 * mm,
            f"Ref {ref}  ·  Funder: Alpha Direct Insurance Company  ·  Page {c.getPageNumber()}",
        )
    return _draw


def _signature_block(rec):
    """Boxed signature block (practitioner side) with the affixed signature."""
    sig_flow = Paragraph("<i>(no signature on file)</i>",
                         ParagraphStyle("nos", fontName="Times-Italic", fontSize=8, textColor=MUTE))
    sig = getattr(rec, "signature_data_url", "") or ""
    if sig.startswith("data:image"):
        try:
            from reportlab.lib.utils import ImageReader
            data = base64.b64decode(sig.split(",", 1)[1])
            bio = io.BytesIO(data)
            iw, ih = ImageReader(bio).getSize()
            bio.seek(0)
            # Fit within the box (46x16mm) preserving aspect — the canvas is wider
            # (~4:1) than the box, so it would otherwise render vertically stretched.
            max_w, max_h = 46 * mm, 16 * mm
            scale = min(max_w / iw, max_h / ih) if iw and ih else 1.0
            sig_flow = Image(bio, width=iw * scale, height=ih * scale)
        except Exception:  # noqa: BLE001
            pass
    lbl = ParagraphStyle("lbl", fontName="Times-Roman", fontSize=8, textColor=MUTE)
    val = ParagraphStyle("val", fontName="Times-Bold", fontSize=10, textColor=INK, leading=13)
    signed = getattr(rec, "signed_at", None) or datetime.utcnow()
    data = [
        [Paragraph("THE PRACTITIONER", ParagraphStyle("h", fontName="Times-Bold", fontSize=9, textColor=AFA_MAROON)), ""],
        [sig_flow, Paragraph(
            f"{_xml(getattr(rec, 'signatory_full_name', '') or '')}<br/>"
            f"<font size=8 color='#8a7a75'>Signed electronically · {signed:%d %B %Y %H:%M} UTC</font>", val)],
        [Paragraph("Signature", lbl), Paragraph("Name &amp; date", lbl)],
    ]
    t = Table(data, colWidths=[60 * mm, 100 * mm])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (1, 0)),
        ("LINEBELOW", (0, 0), (1, 0), 0.5, AFA_ORANGE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#d9c9c2")),
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#fbf6f3")),
    ]))
    return KeepTogether([Spacer(1, 3 * mm), t])


def build_agreement_pdf(rec) -> bytes:
    """rec: VendorOnboarding instance. Returns print-ready PDF bytes."""
    with open(_TEXT_PATH, "r", encoding="utf-8") as fh:
        filled = _fill_tokens(fh.read(), rec)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=30 * mm, bottomMargin=20 * mm,
        title="AFA Service Provider Network Agreement", author="Associated Fund Administrators Botswana",
    )
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=9.5, leading=13.5,
                          alignment=TA_JUSTIFY, textColor=INK, spaceAfter=2)
    h_main = ParagraphStyle("hmain", fontName="Times-Bold", fontSize=15, leading=19,
                            textColor=AFA_MAROON, alignment=TA_CENTER, spaceBefore=2, spaceAfter=6)
    h_sec = ParagraphStyle("hsec", fontName="Times-Bold", fontSize=11.5, leading=15,
                           textColor=AFA_MAROON, spaceBefore=9, spaceAfter=2)

    flow = []
    sig_emitted = False
    lines = filled.split("\n")
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flow.append(Spacer(1, 3))
            continue
        head = _HEADING_RX.match(line.strip())
        if head:
            txt = line.strip()
            if txt.startswith("SERVICE PROVIDER"):
                flow.append(Paragraph(_xml(txt), h_main))
                flow.append(HRFlowable(width="40%", thickness=1.2, color=AFA_ORANGE,
                                       spaceBefore=1, spaceAfter=6, hAlign="CENTER"))
            else:
                flow.append(Paragraph(_xml(txt), h_sec))
            continue
        flow.append(Paragraph(_xml(line), body))
        if "signature affixed below" in line and not sig_emitted:
            flow.append(_signature_block(rec))
            sig_emitted = True

    doc.build(flow, onFirstPage=_decorator(rec.reference_number),
              onLaterPages=_decorator(rec.reference_number))
    return buf.getvalue()
