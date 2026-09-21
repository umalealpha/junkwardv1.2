"""hris/jobdesc_pdf.py — render a pasted job description onto the Alpha Direct
letterhead (CFO directive 2026-08-05).

The HR Document Vault already stores files. A job description, though, usually
arrives as plain text pasted from an email (e.g. Motlatsi Molefe's duties list),
not a ready-made file. This turns that pasted text into a print-ready, branded
A4 PDF so it can live in the vault against the employee like any other document —
letting HR capture a JD in under two minutes without first making a Word file.

Reuses the same letterhead furniture as the HR letters (hris/letter_pdf.py):
the logo/swoosh banner, the faint Setswana watermark and the address footer.

Public entry point: render_job_description_pdf(...) -> bytes.
"""
from __future__ import annotations

import io

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

# Reuse the exact brand + letterhead decorator from the letter renderer so a JD
# looks like every other Alpha Direct HR document.
from hris.letter_pdf import NAVY, INK, MUTE, _decorator, _xml, _long_date


def _lines_to_flowables(text: str, body, bullet, heading):
    """Turn the pasted job-description text into styled paragraphs.

    Kept deliberately simple (Karpathy: no speculative parsing): each non-blank
    line becomes a paragraph. A line that opens with a number, a bullet glyph or
    a lettered sub-point is rendered as an indented bullet; a short ALL-CAPS or
    'Xxx:' line with no trailing sentence reads as a section heading.
    """
    import re

    flow = []
    for raw in (text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = raw.strip()
        if not line:
            flow.append(Spacer(1, 3 * mm))
            continue
        # Numbered / bulleted / lettered points → indented bullet.
        m = re.match(r'^(\d+[.)]|[-*•·o]|[a-zA-Z][.)])\s+(.*)$', line)
        if m:
            flow.append(Paragraph('• ' + _xml(m.group(2)), bullet))
            continue
        # A short heading-like line ("Responsibilities:", "KEY DUTIES").
        if len(line) <= 60 and (line.isupper() or line.endswith(':')):
            flow.append(Paragraph(_xml(line.rstrip(':')), heading))
            continue
        flow.append(Paragraph(_xml(line), body))
    return flow


def render_job_description_pdf(*, employee_name: str, job_title: str,
                               body_text: str, issued_date=None,
                               reference: str = '') -> bytes:
    """Render an A4 job-description PDF on the Alpha Direct letterhead.

    employee_name — who the JD is for (may be blank for a role-only JD).
    job_title     — the role/position title.
    body_text     — the pasted duties/responsibilities text.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=25 * mm, rightMargin=25 * mm,
        topMargin=72 * mm,       # clear the full-bleed banner
        bottomMargin=26 * mm,    # clear the footer
        title=f"Alpha Direct — Job Description — {job_title or employee_name or 'Role'}",
        author='Alpha Direct Insurance Company (Pty) Ltd',
    )

    h1 = ParagraphStyle('jdtitle', fontName='Helvetica-Bold', fontSize=14,
                        leading=18, textColor=NAVY, spaceBefore=2, spaceAfter=2)
    sub = ParagraphStyle('jdsub', fontName='Helvetica', fontSize=10.5,
                         leading=14, textColor=MUTE, spaceAfter=2)
    heading = ParagraphStyle('jdhead', fontName='Helvetica-Bold', fontSize=11,
                             leading=15, textColor=NAVY, spaceBefore=8, spaceAfter=4)
    body = ParagraphStyle('jdbody', fontName='Helvetica', fontSize=10.5,
                          leading=15, alignment=TA_LEFT, textColor=INK, spaceAfter=6)
    bullet = ParagraphStyle('jdbullet', fontName='Helvetica', fontSize=10.5,
                            leading=15, textColor=INK, leftIndent=10 * mm,
                            spaceAfter=4)
    meta = ParagraphStyle('jdmeta', fontName='Helvetica', fontSize=9,
                          leading=13, textColor=MUTE)

    flow = []
    flow.append(Paragraph('JOB DESCRIPTION', h1))
    if job_title:
        flow.append(Paragraph('Position: <b>' + _xml(job_title) + '</b>', sub))
    if employee_name:
        flow.append(Paragraph('Employee: <b>' + _xml(employee_name) + '</b>', sub))
    flow.append(Paragraph('Issued: ' + _xml(_long_date(issued_date) or ''), sub))
    flow.append(Spacer(1, 4 * mm))
    flow.append(Paragraph('Duties &amp; Responsibilities', heading))
    flow.extend(_lines_to_flowables(body_text, body, bullet, heading))
    if reference:
        flow.append(Spacer(1, 8 * mm))
        flow.append(Paragraph('Reference: ' + _xml(reference)
                              + ' — captured in omni HR Document Vault.', meta))

    doc.build(flow, onFirstPage=_decorator, onLaterPages=_decorator)
    return buf.getvalue()
