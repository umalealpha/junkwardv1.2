"""
payroll/pdf.py — the Alpha Direct payslip PDF.

Rebuilt 2026-07-25 on CFO instruction after a full audit of the live July-2026
run found 68 of 102 payslips did not add up when printed. What changed:

  • Earnings, Deductions and Employer contributions are now split by the ONE
    authoritative classifier in `payroll/payslip_breakdown.py` (see that module
    for the three bugs the old inline test had). The earnings column now sums
    to Gross, and Gross − Total Deductions equals Net, on every slip.
  • Employer medical / pension / provident are kept OUT of Earnings — they are
    company cost, not the employee's pay. They no longer print at all: the
    "Employer Contributions" block and the Cost to Company total were removed
    from the employee-facing slip on 2026-07-29 (CFO / Pako Kago). The tie-out
    still runs — see `payslip_breakdown.internal_variances`.
  • A salary-sacrifice reduction (negative earning, e.g. housing) stays in
    Earnings with its sign instead of appearing as a phantom deduction.
  • Added the Total Deductions row that was missing, plus a plain-English
    reconciliation line under Net Pay.
  • No "P" after every figure — the currency is stated once, in the column
    header (CFO 2026-07-25). Deductions print in brackets: (1,234.56).
  • If the itemised lines disagree with the totals payroll holds, the slip says
    so in a visible band instead of silently printing two unrelated numbers.
  • Modern light treatment: white page, real logo artwork, hairline rules,
    tinted section bars instead of heavy dark bands, brand navy #1D3270 and
    orange #F47C20.

Layout lineage: the Odoo HR "Salary Slip" the company used before omni
(Unami's May-2026 sample). Same information, rebuilt so it reconciles.
"""

from __future__ import annotations

import io
from decimal import Decimal
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    KeepTogether,
)
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF


# ─── brand palette (canonical set — exco-ca4-cfo-copilot §3) ────────────────
C_NAVY      = colors.HexColor('#1D3270')   # Alpha Navy — headings, totals
C_ORANGE    = colors.HexColor('#F47C20')   # Direct Orange — net pay only
C_INK       = colors.HexColor('#111827')   # body text
C_MUTED     = colors.HexColor('#6B7280')   # labels, footer
C_HAIRLINE  = colors.HexColor('#E5E7EB')   # row separators
C_TINT_NAVY = colors.HexColor('#F4F6FB')   # section bar + total row fill
C_TINT_WARM = colors.HexColor('#FEF3E8')   # net-pay row fill
C_WARN_BG   = colors.HexColor('#FEF2F2')   # variance band
C_WARN_TXT  = colors.HexColor('#B42318')

# Retained for any caller still importing the old names.
C_TEXT   = C_INK
C_LABEL  = C_MUTED
C_LINE   = C_HAIRLINE
C_FOOTER = C_MUTED

# ─── logo artwork ───────────────────────────────────────────────────────────
# Same candidate order as procurement/pdf.py. logo.png (the reflection/shadow
# version) is deliberately NOT a candidate — retired by CFO directive
# 2026-07-09 in favour of logo-clean.png.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOGO_CANDIDATES = [
    _REPO_ROOT / 'procurement' / 'pdf_assets' / 'logo-clean.png',
    _REPO_ROOT / 'alpha-direct-design-system' / 'assets' / 'logo-full-color.png',
    _REPO_ROOT / 'frontend' / 'public' / 'brand' / 'logo-full-color.png',
]


def _logo_path() -> str | None:
    for p in _LOGO_CANDIDATES:
        if p.exists():
            return str(p)
    return None


def _logo_flowable(width_mm: float = 44.0):
    """Real logo artwork scaled to `width_mm`, or a typeset fallback."""
    path = _logo_path()
    if path:
        try:
            iw, ih = ImageReader(path).getSize()
            w = width_mm * mm
            return Image(path, width=w, height=w * (ih / float(iw)))
        except Exception:      # noqa: BLE001 — never fail a payslip over artwork
            pass
    return Paragraph(
        f'<font color="#1D3270" size="19"><b>Alpha</b></font>'
        f'<font color="#F47C20" size="19"><b>Direct</b></font><br/>'
        f'<font color="#6B7280" size="7">Insurance Co.</font>',
        ParagraphStyle('logofallback', fontSize=19, leading=22),
    )


# ─── house copy ─────────────────────────────────────────────────────────────
ADDR_LINES = [
    'Alpha Direct Insurance Co. (Pty) Ltd',
    'Floor 2, Bar 2, Botswana Innovation Hub Icon Building',
    'Plot 69184 Block 8 Industrial, Gaborone, Botswana',
    '+267 392 8264  ·  hr@alphadirect.co.bw',
]
FOOTER_LINE_1 = 'This payslip is issued by the Human Resource Department.'
# The QR code carries the payslip's omni URL, but there is NO public
# verification route for a payslip yet (unlike the PO one at
# /api/verify/po/<uuid>/). Until that exists this footer must NOT tell a bank or
# embassy to scan-to-verify — they would hit a login wall and a 404
# (Fable review 2026-07-25, fix 2).
FOOTER_LINE_2 = ('Query anything on this slip: hr@alphadirect.co.bw '
                 'or +267 370 2700.')
CONTACT_STRIP = ('Alpha Direct Insurance Co. (Pty) Ltd  ·  www.alphadirect.co.bw  ·  '
                 'VAT BW00000123907  ·  Reg CO2012/13738')
BANK_STRIP_TITLE = 'Paid by Alpha Direct Insurance Co. (Pty) Ltd'
BANK_STRIP_BODY = ('First National Bank of Botswana  ·  Corporate Branch 282267  ·  '
                   'FIRNBWGX')


# ─── money formatting ───────────────────────────────────────────────────────
# The currency is stated ONCE, in the amount-column header, so figures stay
# clean (CFO 2026-07-25: "You dont need to put P after all figures, in the
# header itself you can say BWP").
#
# The currency code is passed down the call chain as a parameter, never held in
# module state. It used to live in a module-level dict reset by a `finally`,
# which is exception-safe but NOT thread-safe: prod runs gunicorn with
# --workers 4 --threads 4, so a concurrent BWP render's reset could flip the
# header of an in-flight INR (ADRisk) payslip to the wrong currency
# (Fable review 2026-07-25, fix 3).
DEFAULT_CCY = 'BWP'


def _money(v) -> str:
    """Plain thousands-separated amount, no currency mark: 78,900.00"""
    n = Decimal(str(v if v is not None else 0))
    sign = '-' if n < 0 else ''
    return f'{sign}{abs(n):,.2f}'


def _bracket(v) -> str:
    """Deductions print in brackets, accountant style: (1,234.56)"""
    return f'({_money(abs(Decimal(str(v if v is not None else 0))))})'


def _pula(v) -> str:
    """Deprecated — kept so any external caller keeps working. Use _money()."""
    return _money(v)


def _month_label(d: date) -> str:
    return d.strftime('%B %Y') if d else ''


# ─── public API ─────────────────────────────────────────────────────────────

def render_payslip_sample(data: dict) -> bytes:
    """Standalone renderer for QA — takes a plain dict, no DB.

    Keys: employee_name, designation, email, department, identification_no,
    registration_number, date_from, date_to, computed_on, reference,
    month_label, earnings [(label, amt)], deductions [(label, amt)],
    gross, total_deductions, net_salary, currency_note, qr_url,
    variances [str] (optional).

    No `employer` / `ctc` keys — employer contributions and cost-to-company were
    removed from the employee-facing payslip (CFO / Pako Kago 2026-07-29). Any
    such keys passed in are ignored by the renderer.
    """
    return _render(data)


def generate_payslip_pdf(payslip) -> bytes:
    """Render an A4 payslip for a payroll.models.Payslip instance."""
    from .payslip_breakdown import build_breakdown

    emp = payslip.employee
    period = payslip.period
    foreign = bool(getattr(payslip, 'is_foreign_currency', False))

    b = build_breakdown(payslip)

    gross = b.gross
    net = b.net
    total_ded = b.total_deductions
    ccy = DEFAULT_CCY
    fx_note = ''

    if foreign:
        # ADRisk (INR) and any non-BWP entity: the slip is issued in the source
        # currency. The stored *_amount fields stay BWP for group reporting.
        ccy = payslip.source_currency or DEFAULT_CCY
        if not b.earnings and payslip.source_gross is not None:
            # Headline-only import (Gross / Tax / Net, no component breakdown).
            gross = payslip.source_gross
            b.earnings = [('Gross Salary', gross)]
            paye = abs(payslip.source_paye or Decimal('0'))
            b.deductions = [('Tax', paye)] if paye else []
            net = (payslip.source_net
                   if payslip.source_net is not None else gross - paye)
            # `source_net` is taken verbatim from the source payroll's "Net
            # Salary" column (payroll/importer.py), so for an Indian payroll it
            # is already net of PF / professional tax that never came across as
            # components. Without the balancing line below the slip would print
            # "Gross 60,000 less deductions 4,200 equals net 54,000" — arithmetic
            # that is false on the face of the document, and `_compare_stored` is
            # (correctly) skipped for foreign slips so nothing would catch it.
            # Fable review 2026-07-25, fix 1.
            other = (gross - net) - paye
            if other > 0:
                total_ded = gross - net
                b.deductions.append(
                    ('Other deductions (per source payroll)', other))
            elif other < 0:
                # Net ABOVE gross-less-tax: the source figures do not reconcile
                # (a corrupt or partial import). Do NOT invent a negative
                # deduction to force the columns — a bracketed row is rendered
                # as a magnitude, so the column would silently stop summing.
                # Say so instead, the same way the BWP path does.
                total_ded = paye
                b.variances.append(
                    f'Net pay is higher than gross less tax by '
                    f'{abs(other):,.2f} {ccy} — the source payroll figures for '
                    f'this month do not reconcile'
                )
            else:
                total_ded = paye
        # No group-reporting FX line on the employee's slip: the BWP conversion
        # is internal, and staff hand this document to their own bank
        # (CFO 2026-09-18). The column header already states the currency.

    # QR REMOVED (CFO 2026-08-18): it pointed to /payroll/payslips/<id>, a
    # login-only omni page, so scanning it from a phone (e.g. Bharath's) hit the
    # sign-in wall — a dead link, never a verification. There is no PUBLIC
    # payslip-verify route yet (unlike the PO one at /api/verify/po/<uuid>/).
    # Leave it off until such a route exists; an empty qr_url draws no QR.
    qr_url = ''

    data = {
        'employee_name':       emp.full_name,
        'designation':         emp.job_title or '',
        'email':               emp.email or '',
        'department':          emp.department or '',
        'identification_no':   emp.national_id or '',
        'registration_number': emp.employee_number or '',
        'company_name':        (payslip.company.name if payslip.company_id else ''),
        'date_from':           period.start_date.strftime('%d %b %Y') if period.start_date else '',
        'date_to':             period.end_date.strftime('%d %b %Y') if period.end_date else '',
        'computed_on':         (payslip.updated_at.strftime('%d %b %Y')
                                if getattr(payslip, 'updated_at', None) else ''),
        'reference':           f'SLIP/{str(payslip.pk)[:8].upper()}',
        'month_label':         _month_label(period.end_date) if period.end_date else '',
        'earnings':            b.earnings,
        'deductions':          b.deductions,
        'gross':               gross,
        'total_deductions':    total_ded,
        'net_salary':          net,
        'currency_note':       fx_note,
        'qr_url':              qr_url,
        'variances':           b.variances,
    }
    data['currency'] = ccy
    return _render(data)


# ─── private rendering ──────────────────────────────────────────────────────

def _plain_table(rows, col_widths):
    """Borderless two-cell layout table."""
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


_LBL_W = 118 * mm
_AMT_W = 60 * mm


def _section(title, rows, *, total=None, bracketed=False, accent=False,
             note=None, ccy=DEFAULT_CCY):
    """One money block: tinted title bar, hairline rows, optional total row.

    rows      — [(label, amount)]
    total     — (label, amount) rendered as the emphasised closing row
    bracketed — print every amount in brackets (deductions)
    accent    — warm fill + orange figure on the total row (net pay)
    note      — small muted line under the block
    """
    st_head_l = ParagraphStyle('shl', fontSize=8.5, leading=11, textColor=C_NAVY,
                               fontName='Helvetica-Bold')
    st_head_r = ParagraphStyle('shr', parent=st_head_l, alignment=2,
                               textColor=C_MUTED)
    st_lbl = ParagraphStyle('sl', fontSize=9, leading=12, textColor=C_INK,
                            fontName='Helvetica')
    st_amt = ParagraphStyle('sa', parent=st_lbl, alignment=2)
    st_tot_l = ParagraphStyle('stl', fontSize=9.5, leading=13, textColor=C_NAVY,
                              fontName='Helvetica-Bold')
    st_tot_r = ParagraphStyle('str', parent=st_tot_l, alignment=2)
    st_net_r = ParagraphStyle('snr', fontSize=13, leading=16,
                              textColor=C_ORANGE, fontName='Helvetica-Bold',
                              alignment=2)
    st_net_l = ParagraphStyle('snl', fontSize=10.5, leading=15,
                              textColor=C_NAVY, fontName='Helvetica-Bold')

    body = [[Paragraph(title.upper(), st_head_l), Paragraph(ccy, st_head_r)]]
    for label, amt in rows:
        # A negative EARNING is a salary sacrifice — print it in brackets like
        # any other reduction, so the column reads consistently and matches the
        # explanatory note. Never show a bare minus sign on a payslip.
        neg = Decimal(str(amt or 0)) < 0
        txt = _bracket(amt) if (bracketed or neg) else _money(amt)
        body.append([Paragraph(str(label), st_lbl), Paragraph(txt, st_amt)])
    if not rows and total is None:
        body.append([Paragraph('<i>None this period</i>',
                               ParagraphStyle('non', parent=st_lbl,
                                              textColor=C_MUTED)),
                     Paragraph('—', st_amt)])

    tot_idx = None
    if total is not None:
        t_lbl, t_amt = total
        t_txt = _bracket(t_amt) if bracketed else _money(t_amt)
        if accent:
            body.append([Paragraph(str(t_lbl), st_net_l),
                         Paragraph(t_txt, st_net_r)])
        else:
            body.append([Paragraph(str(t_lbl), st_tot_l),
                         Paragraph(t_txt, st_tot_r)])
        tot_idx = len(body) - 1

    tbl = Table(body, colWidths=[_LBL_W, _AMT_W])
    sty = [
        ('BACKGROUND', (0, 0), (-1, 0), C_TINT_NAVY),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, C_NAVY),
        ('LINEBELOW', (0, 1), (-1, -1), 0.35, C_HAIRLINE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 7),
        ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    if tot_idx is not None:
        sty += [
            ('BACKGROUND', (0, tot_idx), (-1, tot_idx),
             C_TINT_WARM if accent else C_TINT_NAVY),
            ('LINEABOVE', (0, tot_idx), (-1, tot_idx), 0.6,
             C_ORANGE if accent else C_NAVY),
            ('LINEBELOW', (0, tot_idx), (-1, tot_idx), 0, colors.white),
            ('TOPPADDING', (0, tot_idx), (-1, tot_idx), 4.5),
            ('BOTTOMPADDING', (0, tot_idx), (-1, tot_idx), 4.5),
        ]
    tbl.setStyle(TableStyle(sty))

    if not note:
        return tbl
    return KeepTogether([
        tbl,
        Spacer(1, 2.5),
        Paragraph(note, ParagraphStyle('secnote', fontSize=7.5, leading=10,
                                       textColor=C_MUTED)),
    ])


def _employee_grid(d: dict) -> Table:
    """Light 4-column detail grid — uppercase muted labels, no heavy fills."""
    LBL = ParagraphStyle('gl', fontSize=6.5, leading=9, textColor=C_MUTED,
                         fontName='Helvetica-Bold')
    VAL = ParagraphStyle('gv', fontSize=9, leading=12, textColor=C_INK,
                         fontName='Helvetica')

    def cell(lbl, val):
        return Paragraph(
            f'<font size="6.5" color="#6B7280"><b>{lbl.upper()}</b></font><br/>'
            f'<font size="9" color="#111827">{val or "—"}</font>', VAL)

    rows = [
        [cell('Employee', d.get('employee_name', '')),
         cell('Employee number', d.get('registration_number', '')),
         cell('Designation', d.get('designation', ''))],
        [cell('Department', d.get('department', '')),
         cell('Company', d.get('company_name', '')),
         cell('Identification no', d.get('identification_no', ''))],
        [cell('Pay period', f"{d.get('date_from','')} – {d.get('date_to','')}"),
         cell('Prepared on', d.get('computed_on', '')),
         cell('Reference', d.get('reference', ''))],
    ]
    w = (_LBL_W + _AMT_W) / 3.0
    tbl = Table(rows, colWidths=[w, w, w])
    tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, -2), 0.35, C_HAIRLINE),
    ]))
    return tbl


def _qr_drawing(qr_url: str, size_mm: float = 20.0):
    w = QrCodeWidget(qr_url, barLevel='M')
    s = size_mm * mm
    b = w.getBounds()
    dw, dh = b[2] - b[0], b[3] - b[1]
    dr = Drawing(s, s, transform=[s / dw, 0, 0, s / dh, 0, 0])
    dr.add(w)
    return dr


def _render(d: dict) -> bytes:
    buf = io.BytesIO()
    ccy = d.get('currency') or DEFAULT_CCY

    if not d.get('month_label'):
        raw = str(d.get('date_to') or '')
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d %b %Y'):
            try:
                from datetime import datetime as _dt
                d['month_label'] = _dt.strptime(raw, fmt).strftime('%B %Y')
                break
            except ValueError:
                continue

    name = d.get('employee_name', '')
    month = d.get('month_label', '')
    doc_title = f"Payslip - {name}" + (f" - {month}" if month else '')

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=11 * mm, bottomMargin=9 * mm,
        title=doc_title, author='Alpha Direct Insurance Co. (Pty) Ltd',
        subject=f'Payslip {month}',
    )

    st_addr = ParagraphStyle('addr', fontSize=7.5, leading=10.5,
                             textColor=C_MUTED, alignment=2)
    st_kicker = ParagraphStyle('kick', fontSize=8, leading=11, textColor=C_ORANGE,
                               fontName='Helvetica-Bold')
    st_h1 = ParagraphStyle('h1', fontSize=19, leading=23, textColor=C_NAVY,
                           fontName='Helvetica-Bold', spaceBefore=1)
    st_sub = ParagraphStyle('sub', fontSize=9, leading=12, textColor=C_MUTED)
    st_foot = ParagraphStyle('ft', fontSize=8, leading=11, textColor=C_MUTED,
                             alignment=1)
    st_foot_b = ParagraphStyle('ftb', parent=st_foot, fontName='Helvetica-Bold',
                               textColor=C_NAVY)
    st_fine = ParagraphStyle('fine', fontSize=7, leading=10, textColor=C_MUTED,
                             alignment=1)

    flow = []
    full_w = _LBL_W + _AMT_W

    # ── masthead: real logo left, address right ────────────────────────────
    flow.append(_plain_table(
        [[_logo_flowable(44), Paragraph('<br/>'.join(ADDR_LINES), st_addr)]],
        [88 * mm, full_w - 88 * mm]))
    flow.append(Spacer(1, 5))

    # thin orange brand rule
    rule = Table([['']], colWidths=[full_w], rowHeights=[1.6])
    rule.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), C_ORANGE)]))
    flow.append(rule)
    flow.append(Spacer(1, 8))

    # ── title block + QR ───────────────────────────────────────────────────
    left = [Paragraph('PAYSLIP', st_kicker), Paragraph(name, st_h1)]
    if month:
        left.append(Paragraph(month, st_sub))
    qr_url = d.get('qr_url') or ''
    right = _qr_drawing(qr_url) if qr_url else Paragraph('', st_sub)
    flow.append(_plain_table([[left, right]], [full_w - 22 * mm, 22 * mm]))
    flow.append(Spacer(1, 7))

    # ── employee detail ────────────────────────────────────────────────────
    flow.append(_employee_grid(d))
    flow.append(Spacer(1, 9))

    # ── variance band — never print two unrelated numbers silently ─────────
    variances = d.get('variances') or []
    if variances:
        msg = ('<b>This payslip is under review.</b> ' +
               ' '.join(f'{v}.' for v in variances) +
               ' Please contact hr@alphadirect.co.bw before relying on it.')
        band = Table([[Paragraph(msg, ParagraphStyle(
            'warn', fontSize=8, leading=11, textColor=C_WARN_TXT))]],
            colWidths=[full_w])
        band.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), C_WARN_BG),
            ('LINEBEFORE', (0, 0), (0, -1), 2.2, C_WARN_TXT),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        flow.append(band)
        flow.append(Spacer(1, 9))

    # ── earnings ───────────────────────────────────────────────────────────
    earn_rows = list(d.get('earnings') or [])
    gross = d.get('gross')
    if gross is None:
        gross = sum((Decimal(str(a or 0)) for _, a in earn_rows), Decimal('0'))
    has_sacrifice = any(Decimal(str(a or 0)) < 0 for _, a in earn_rows)
    flow.append(_section(
        'Earnings', earn_rows, total=('Gross Pay', gross), ccy=ccy,
        note=('A figure in brackets above is a salary sacrifice you chose — it '
              'reduces your gross pay once, and is NOT deducted again below.')
        if has_sacrifice else None))
    flow.append(Spacer(1, 6))

    # ── deductions ─────────────────────────────────────────────────────────
    ded_rows = [(lbl, abs(Decimal(str(a or 0))))
                for lbl, a in (d.get('deductions') or [])]
    total_ded = d.get('total_deductions')
    if total_ded is None:
        total_ded = sum((a for _, a in ded_rows), Decimal('0'))
    flow.append(_section('Deductions', ded_rows,
                         total=('Total Deductions', total_ded),
                         bracketed=True, ccy=ccy))
    flow.append(Spacer(1, 6))

    # ── net pay + the reconciliation in words ──────────────────────────────
    net = d.get('net_salary', 0)
    flow.append(_section('Net Pay', [], total=('Net Pay — amount paid to you', net),
                         accent=True, ccy=ccy))
    flow.append(Spacer(1, 3))
    flow.append(Paragraph(
        f'Gross pay {_money(gross)} less total deductions {_money(total_ded)} '
        f'equals net pay {_money(net)} {ccy}.',
        ParagraphStyle('recon', fontSize=8, leading=11, textColor=C_MUTED)))
    flow.append(Spacer(1, 9))

    # ── employer contributions → cost to company ───────────────────────────
    # REMOVED (CFO / Pako Kago directive 2026-07-29): the employer-contributions
    # block and the Cost-to-Company total no longer print on the employee-facing
    # payslip. CTC is company cost, not the employee's pay, and the CFO does not
    # want it shown. The gross → deductions → net reconciliation above is kept;
    # only the CTC display is gone. `build_breakdown` still computes b.ctc /
    # b.employer for any internal caller — this only stops rendering them.

    if d.get('currency_note'):
        flow.append(Spacer(1, 6))
        flow.append(Paragraph(d['currency_note'],
                              ParagraphStyle('ccy', fontSize=7.5, leading=10,
                                             textColor=C_MUTED)))

    # ── footer ─────────────────────────────────────────────────────────────
    flow.append(Spacer(1, 10))
    foot_rule = Table([['']], colWidths=[full_w], rowHeights=[0.5])
    foot_rule.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), C_HAIRLINE)]))
    flow.append(KeepTogether([
        foot_rule,
        Spacer(1, 6),
        Paragraph(FOOTER_LINE_1, st_foot_b),
        Paragraph(FOOTER_LINE_2, st_foot),
        Spacer(1, 5),
        Paragraph(CONTACT_STRIP, st_fine),
        # The FNB Botswana strip is only true of a BWP slip; a foreign slip is
        # not paid from that account (CFO 2026-09-18).
        *([Paragraph(f'{BANK_STRIP_TITLE}  ·  {BANK_STRIP_BODY}', st_fine)]
          if ccy == DEFAULT_CCY else []),
        Paragraph('Confidential — this document contains personal salary '
                  'information.', st_fine),
    ]))

    doc.build(flow)
    return buf.getvalue()
