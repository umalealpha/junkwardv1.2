"""
agent_portal/payslip_pdf.py — the UniCoin *Instant Insurance* commission payslip.

A one-page A4 statement handed / emailed to an independent agent who has NO omni
login (CFO 2026-07-27). It mirrors the look of the Alpha Direct salary payslip
(earnings block, deductions in brackets, currency stated once in the header, QR
to a verify page) but is UniCoin-branded and commission-shaped:

    Earnings          = the agent's paid commission, itemised by stream
    Gross commission  = Σ earnings  (ties to the uploaded pay-run)
    VAT memo          = gross / 1.14  (shown, NOT deducted — mirrors Bharath's sheet)
    Tax               = income tax, Bharath's agent scale (agent_portal.tax)
    Net payable       = gross − tax   (what the agent is paid)

Self-contained: the UniCoin mark is drawn as vector shapes here, so there is no
external image dependency and it prints sharp at any size. If an official logo
file is later dropped at agent_portal/brand/unicoin-logo.png it is used instead.
"""
from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, KeepTogether,
    HRFlowable,
)
from reportlab.graphics.shapes import Drawing, Circle, Polygon, Line
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget

# ── UniCoin brand palette (recreated from the logo the CFO supplied) ──────────
UNI_NAVY   = colors.HexColor('#1B2A6B')   # UniCoin deep blue
UNI_ORANGE = colors.HexColor('#F26A21')   # UniCoin orange
C_INK      = colors.HexColor('#111827')
C_MUTED    = colors.HexColor('#6B7280')
C_HAIRLINE = colors.HexColor('#E5E7EB')
C_TINT     = colors.HexColor('#F3F5FC')   # navy tint — section bars / totals
C_TINT_WARM= colors.HexColor('#FEF1E8')   # orange tint — net-pay row
C_WARN_BG  = colors.HexColor('#FEF2F2')
C_WARN_TXT = colors.HexColor('#B42318')

_LOGO_OVERRIDE = Path(__file__).resolve().parent / 'brand' / 'unicoin-logo.png'


def _unicoin_mark(size_mm: float = 16.0):
    """The UniCoin circular 'expand-arrows' mark as vector art (or the official
    PNG if one has been dropped in agent_portal/brand/unicoin-logo.png)."""
    if _LOGO_OVERRIDE.exists():
        try:
            iw, ih = ImageReader(str(_LOGO_OVERRIDE)).getSize()
            w = size_mm * mm
            return Image(str(_LOGO_OVERRIDE), width=w, height=w * (ih / float(iw)))
        except Exception:      # noqa: BLE001 — never fail a payslip over artwork
            pass
    s = size_mm * mm
    d = Drawing(s, s)
    r = s / 2.0
    d.add(Circle(r, r, r, fillColor=UNI_NAVY, strokeColor=None))
    # Four outward arrows (expand icon): NE + SW white, NW + SE orange.
    def arrow(cx, cy, dx, dy, colour):
        """A short shaft + chevron head pointing (dx,dy) from a point near the
        circle edge back toward centre."""
        L = s * 0.16          # shaft half-length
        h = s * 0.11          # head size
        tipx, tipy = cx + dx * L, cy + dy * L
        tailx, taily = cx - dx * L, cy - dy * L
        d.add(Line(tailx, taily, tipx, tipy, strokeColor=colour, strokeWidth=s * 0.055))
        # chevron head (two short barbs at the tip, perpendicular-ish)
        px, py = -dy, dx      # perpendicular
        d.add(Polygon([
            tipx, tipy,
            tipx - dx * h + px * h, tipy - dy * h + py * h,
            tipx - dx * h - px * h, tipy - dy * h - py * h,
        ], fillColor=colour, strokeColor=None))
    off = s * 0.30
    k = 0.70711  # 1/sqrt2
    arrow(r + off * k, r + off * k,  k,  k, colors.white)     # NE
    arrow(r - off * k, r - off * k, -k, -k, colors.white)     # SW
    arrow(r - off * k, r + off * k, -k,  k, UNI_ORANGE)       # NW
    arrow(r + off * k, r - off * k,  k, -k, UNI_ORANGE)       # SE
    return d


def _wordmark(size: int = 20):
    return Paragraph(
        f'<font color="#1B2A6B"><b>Uni</b></font>'
        f'<font color="#F26A21"><b>Coin</b></font>',
        ParagraphStyle('wm', fontName='Helvetica-Bold', fontSize=size, leading=size + 2),
    )


# ── money formatting (currency stated once, in the column header) ─────────────
def _money(v) -> str:
    n = Decimal(str(v if v is not None else 0))
    sign = '-' if n < 0 else ''
    return f'{sign}{abs(n):,.2f}'


def _bracket(v) -> str:
    return f'({_money(abs(Decimal(str(v if v is not None else 0))))})'


_LBL = ParagraphStyle('lbl', fontName='Helvetica', fontSize=8, textColor=C_MUTED, leading=10)
_VAL = ParagraphStyle('val', fontName='Helvetica', fontSize=9, textColor=C_INK, leading=11)
_H = ParagraphStyle('h', fontName='Helvetica-Bold', fontSize=9, textColor=UNI_NAVY, leading=11)


def render_agent_payslip(data: dict) -> bytes:
    """Standalone renderer — takes a plain dict, no DB, so QA can exercise it.

    Keys: agent_name, agent_ref, agency, phone, email, cycle_label, period,
    number, computed_on, earnings [(label, amount)], gross, vat_memo, tax, net,
    annual, qr_url, note (optional), variances [str] (optional).
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=14 * mm,
        title=f"UniCoin Commission Payslip {data.get('number', '')}",
    )
    ccy = data.get('currency', 'BWP')
    story = []

    # ── header: mark + wordmark (left), issuer block (right) ──────────────────
    head_left = Table(
        [[_unicoin_mark(16), _wordmark(20)]],
        colWidths=[18 * mm, None],
    )
    head_left.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    issuer = Paragraph(
        '<font color="#6B7280" size="7.5">Instant Insurance · Agent Commissions<br/>'
        'UniCoin · a member of the Alpha Direct group<br/>'
        'Gaborone, Botswana</font>',
        ParagraphStyle('iss', alignment=2, leading=11),
    )
    header = Table([[head_left, issuer]], colWidths=[95 * mm, None])
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 4 * mm))
    story.append(_rule())
    story.append(Spacer(1, 3 * mm))

    # ── title ────────────────────────────────────────────────────────────────
    story.append(Paragraph(
        f'<font color="#1B2A6B" size="15"><b>Commission Payslip</b></font>'
        f'&nbsp;&nbsp;<font color="#6B7280" size="9">{data.get("cycle_label","")}</font>',
        ParagraphStyle('title', leading=18)))
    story.append(Spacer(1, 3 * mm))

    # ── agent + payslip meta ──────────────────────────────────────────────────
    def field(lbl, val):
        return [Paragraph(lbl, _LBL), Paragraph(str(val or '—'), _VAL)]
    meta = Table([
        field('Agent', data.get('agent_name')) + field('Payslip no.', data.get('number')),
        field('Agent ID', data.get('agent_ref')) + field('Pay cycle', data.get('period')),
        field('Agency', data.get('agency')) + field('Issued', data.get('computed_on')),
        field('Contact', data.get('phone') or data.get('email')) + field('Currency', ccy),
    ], colWidths=[24 * mm, 62 * mm, 24 * mm, None])
    meta.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(meta)
    story.append(Spacer(1, 4 * mm))

    # ── earnings (per stream) ─────────────────────────────────────────────────
    story.append(_section_bar('Commission earned', f'Amount ({ccy})'))
    rows = []
    for label, amt in data.get('earnings', []):
        rows.append([Paragraph(str(label), _VAL), Paragraph(_money(amt), _VAL_R())])
    if not rows:
        rows.append([Paragraph('No commission this cycle', _VAL), Paragraph(_money(0), _VAL_R())])
    rows.append([Paragraph('<b>Gross commission</b>', _H),
                 Paragraph(f'<b>{_money(data.get("gross", 0))}</b>', _VAL_R(bold=True))])
    t = _amount_table(rows, total_row=True)
    story.append(t)
    story.append(Spacer(1, 3 * mm))

    # ── deductions ────────────────────────────────────────────────────────────
    story.append(_section_bar('Deductions', f'Amount ({ccy})'))
    ded_rows = [[Paragraph('Income tax (UniCoin agent scale)', _VAL),
                 Paragraph(_bracket(data.get('tax', 0)), _VAL_R())]]
    ded_rows.append([Paragraph('<b>Total deductions</b>', _H),
                     Paragraph(f'<b>{_bracket(data.get("tax", 0))}</b>', _VAL_R(bold=True))])
    story.append(_amount_table(ded_rows, total_row=True))
    story.append(Spacer(1, 3 * mm))

    # ── net payable (highlighted) ─────────────────────────────────────────────
    net_t = Table([[Paragraph('<font color="#1B2A6B"><b>NET PAYABLE</b></font>',
                              ParagraphStyle('n', fontSize=11, leading=13)),
                    Paragraph(f'<font color="#1B2A6B"><b>{ccy} {_money(data.get("net", 0))}</b></font>',
                              ParagraphStyle('nr', fontSize=12, leading=14, alignment=2))]],
                   colWidths=[None, 55 * mm])
    net_t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_TINT_WARM),
        ('LINEABOVE', (0, 0), (-1, 0), 1, UNI_ORANGE),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(net_t)
    story.append(Spacer(1, 2 * mm))

    # reconciliation + VAT memo line
    story.append(Paragraph(
        f'Net payable = gross commission {_money(data.get("gross",0))} '
        f'less income tax {_money(data.get("tax",0))}. '
        f'VAT-exclusive equivalent (memo only, not deducted): '
        f'{ccy} {_money(data.get("vat_memo",0))}. '
        f'Tax is computed on annualised commission ({ccy} {_money(data.get("annual",0))}).',
        ParagraphStyle('recon', fontSize=7.5, textColor=C_MUTED, leading=10)))

    # ── not paid this cycle (transparency) ────────────────────────────────────
    np_rows = data.get('not_paid') or []
    if np_rows:
        story.append(Spacer(1, 4 * mm))
        story.append(_section_bar('Not paid this cycle', 'Reason'))
        body = []
        for row in np_rows[:12]:
            label = row[0] if len(row) > 0 else ''
            pol = row[1] if len(row) > 1 else ''
            reason = row[2] if len(row) > 2 else ''
            left = f'{label} · {pol}' if pol else label
            body.append([Paragraph(str(left), ParagraphStyle('npl', fontSize=7.5, textColor=C_MUTED, leading=9)),
                         Paragraph(str(reason), ParagraphStyle('npr', fontSize=7.5, textColor=C_MUTED, leading=9))])
        nt = Table(body, colWidths=[62 * mm, None])
        nt.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, C_HAIRLINE),
        ]))
        story.append(nt)

    # ── variance band (only if figures disagree) ──────────────────────────────
    for v in data.get('variances', []) or []:
        story.append(Spacer(1, 2 * mm))
        vb = Table([[Paragraph(f'<font color="#B42318"><b>⚠ {v}</b></font>',
                              ParagraphStyle('v', fontSize=8, leading=10))]], colWidths=[None])
        vb.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), C_WARN_BG),
                                ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
                                ('LEFTPADDING', (0, 0), (-1, -1), 6)]))
        story.append(vb)

    story.append(Spacer(1, 6 * mm))
    story.append(_rule())
    story.append(Spacer(1, 3 * mm))

    # ── footer: note + QR ──────────────────────────────────────────────────────
    note = data.get('note') or (
        'This is a commission statement for an independent UniCoin Instant '
        'Insurance agent. It is not a salary and does not create employment. '
        'Queries: commissions@alphadirect.co.bw.')
    foot_left = Paragraph(f'<font color="#6B7280" size="7.5">{note}</font>',
                          ParagraphStyle('f', leading=10))
    qr_flow = _qr(data.get('qr_url', ''))
    footer = Table([[foot_left, qr_flow]], colWidths=[None, 22 * mm])
    footer.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    story.append(footer)

    doc.build(story)
    return buf.getvalue()


# ── small helpers ─────────────────────────────────────────────────────────────
def _rule():
    return HRFlowable(width='100%', thickness=0.8, color=C_HAIRLINE,
                      spaceBefore=0, spaceAfter=0)


def _VAL_R(bold=False):
    return ParagraphStyle('vr', fontName='Helvetica-Bold' if bold else 'Helvetica',
                          fontSize=9, textColor=C_INK, leading=11, alignment=2)


def _section_bar(title, right):
    t = Table([[Paragraph(f'<font color="#1B2A6B"><b>{title}</b></font>',
                          ParagraphStyle('sb', fontSize=9, leading=11)),
                Paragraph(f'<font color="#6B7280">{right}</font>',
                          ParagraphStyle('sbr', fontSize=8, leading=11, alignment=2))]],
               colWidths=[None, 45 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_TINT),
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t


def _amount_table(rows, total_row=False):
    t = Table(rows, colWidths=[None, 45 * mm])
    style = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.4, C_HAIRLINE),
    ]
    if total_row:
        style += [('BACKGROUND', (0, -1), (-1, -1), C_TINT),
                  ('LINEABOVE', (0, -1), (-1, -1), 0.8, UNI_NAVY)]
    t.setStyle(TableStyle(style))
    return t


def _qr(url):
    if not url:
        return Spacer(1, 1)
    q = QrCodeWidget(url)
    b = q.getBounds()
    w = 20 * mm
    d = Drawing(w, w, transform=[w / (b[2] - b[0]), 0, 0, w / (b[3] - b[1]), 0, 0])
    d.add(q)
    return d


# ── from a stored AgentPayslip instance ───────────────────────────────────────
def generate_agent_payslip_pdf(payslip) -> bytes:
    """Render an AgentPayslip model instance to PDF bytes."""
    agent = payslip.agent
    cycle = payslip.cycle
    period = ''
    if cycle.start_date and cycle.end_date:
        period = f"{cycle.start_date:%d %b %Y} – {cycle.end_date:%d %b %Y}"
    data = {
        'agent_name': agent.name,
        'agent_ref': agent.ref_id,
        'agency': agent.agency or 'UniCoin',
        'phone': agent.phone,
        'email': agent.email,
        'cycle_label': cycle.label,
        'period': period,
        'number': payslip.number,
        'computed_on': (payslip.updated_at or payslip.created_at).strftime('%d %b %Y')
                       if (payslip.updated_at or payslip.created_at) else '',
        'earnings': [(lbl, amt) for lbl, amt in payslip.breakdown],
        'gross': payslip.gross,
        'vat_memo': payslip.ex_vat,
        'tax': payslip.tax,
        'net': payslip.net,
        'annual': payslip.annual,
        'not_paid': payslip.not_paid or [],
        # No QR yet: there is no PUBLIC payslip-verify route, and agents have no
        # omni login, so a QR would print a dead / login-walled link on every
        # slip (Fable review 2026-07-27). The _qr helper degrades to nothing on
        # an empty URL. Add a real /unicoin/verify page first, then wire it here.
        'qr_url': '',
        'currency': 'BWP',
    }
    return render_agent_payslip(data)
