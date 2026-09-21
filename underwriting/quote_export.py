"""underwriting/quote_export.py — the quotation as Excel or Word.

The PDF (quote_render) is the document a client is SENT. These two are the
working copies people ask for:

  * Excel — a LIVE workbook. Every money cell is a FORMULA, not a pasted value:
    each row's premium is `=sum_insured * rate / 100`, the section subtotals and
    the grand total are `=SUM(...)`, VAT is `=net * rate`, and the instalment is
    built off the total. Change a sum insured or a rate and the workbook
    re-prices itself — which is the whole reason an underwriter asks for Excel
    rather than a PDF (CFO 2026-08-11: "ensure the excel has formulas").
  * Word — an editable branded copy for the rare case where a broker wants to
    mark it up.

Both are built from the SAME quote record the PDF uses, so all three agree — and
that includes the rows the PDF GUARANTEES rather than reads (the Workmen's
Compensation Common Law Liability benefit): both paths go through
quote_render.sections_with_guaranteed_rows, or the Word copy a broker marks up
would understate the cover being sold. The VAT rate and the payment-plan rate come
from quote_parse, never retyped here.
"""
from __future__ import annotations

import io
from decimal import Decimal

from .quote_parse import (VAT_RATE, PAYMENT_PLAN_PCT, _to_decimal,
                          section_premium_rows)

NAVY = '1D3270'
ORANGE = 'F47C20'
WASH = 'F5F7FB'


def _num(raw):
    """A cell value as a real number where it is one, else the text as typed
    ('Included', 'As per policy') — a spreadsheet must not hold words in a
    numeric column, and must not turn wording into 0."""
    v = _to_decimal(raw)
    return float(v) if v is not None and v > 0 else (str(raw or '').strip() or None)


def build_xlsx(quote) -> bytes:
    """The quotation as a live Excel workbook."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = 'Quotation'

    navy_fill = PatternFill('solid', fgColor=NAVY)
    wash_fill = PatternFill('solid', fgColor=WASH)
    white_bold = Font(color='FFFFFF', bold=True, size=11)
    navy_bold = Font(color=NAVY, bold=True)
    thin = Side(style='thin', color='D8DEE6')
    box = Border(top=thin, bottom=thin, left=thin, right=thin)
    money_fmt = '#,##0.00'
    pct_fmt = '0.00"%"'

    # ── header ───────────────────────────────────────────────────────────────
    ws['A1'] = 'ALPHA DIRECT INSURANCE COMPANY (PTY) LTD'
    ws['A1'].font = Font(color='FFFFFF', bold=True, size=13)
    ws['A2'] = 'Insurance Quotation'
    ws['A2'].font = Font(color='FFFFFF', size=10)
    for row in (1, 2):
        for col in range(1, 7):
            ws.cell(row=row, column=col).fill = navy_fill

    meta = [
        ('Quotation No.', quote.quote_number),
        ('Client', quote.client_name),
        ('Class of business', quote.class_of_business or '—'),
        ('Period', quote.period or '—'),
        ('Broker', quote.broker or '—'),
    ]
    r = 4
    for k, v in meta:
        ws.cell(row=r, column=1, value=k).font = Font(bold=True, size=9, color='6B7280')
        ws.cell(row=r, column=2, value=v)
        r += 1

    # ── cover table ──────────────────────────────────────────────────────────
    r += 1
    head_row = r
    headers = ['Section', 'Sum Insured (BWP)', 'Rate %', 'Basis', 'Excess (BWP)', 'Premium (BWP)']
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = navy_fill
        cell.font = white_bold
        cell.border = box
    r += 1

    first_data_row = r
    group_starts: list[tuple[str, int]] = []      # (group label, first row)
    premium_rows: list[int] = []
    current_group = None

    breakdown = section_premium_rows(quote.sections, quote.rate_incl_vat)
    nets_by_src = {p['index']: p['net'] for p in breakdown['rows']} if breakdown else {}

    # The SAME guarantee the PDF applies, so the three documents agree (this
    # module's docstring promises they do). The paired index keeps each premium on
    # its own row; a guaranteed row carries None and takes no premium.
    from .quote_render import sections_with_guaranteed_rows
    for idx, s in sections_with_guaranteed_rows(quote.sections):
        grp = (s.get('group') or '').strip()
        if grp and grp != current_group:
            ws.cell(row=r, column=1, value=grp).font = navy_bold
            for c in range(1, 7):
                ws.cell(row=r, column=c).fill = wash_fill
            current_group = grp
            group_starts.append((grp, r + 1))
            r += 1

        ws.cell(row=r, column=1, value=s.get('name') or '')
        ws.cell(row=r, column=2, value=_num(s.get('sum_insured'))).number_format = money_fmt
        rate = _to_decimal(s.get('rate'))
        if rate is not None and rate > 0:
            ws.cell(row=r, column=3, value=float(rate)).number_format = pct_fmt
        ws.cell(row=r, column=4, value=s.get('basis') or '')
        ws.cell(row=r, column=5, value=_num(s.get('excess'))).number_format = money_fmt

        # THE POINT OF THE WORKBOOK: the premium is a live formula, so changing
        # the sum insured or the rate re-prices the quote.
        # A TYPED row premium wins over the rate — the same precedence as
        # section_premium_rows; the sheet checked rate first and disagreed.
        # And when the rate INCLUDES VAT (the model default) the engine backs the
        # net out of it, so the sheet must divide by 1+VAT too or it prints the
        # gross as the net and then adds VAT again — the total came out 14% high.
        flat = _to_decimal(s.get('premium'))
        if flat is not None and flat > 0:
            net_flat = nets_by_src.get(idx)
            ws.cell(row=r, column=6,
                    value=float(net_flat if net_flat is not None else flat))
        elif rate is not None and rate > 0:
            gross = f'B{r}*C{r}/100'
            expr = (f'ROUND({gross}/{1 + float(VAT_RATE)},2)'
                    if quote.rate_incl_vat else f'ROUND({gross},2)')
            ws.cell(row=r, column=6, value=f'=IF(N(B{r})=0,0,{expr})')
        ws.cell(row=r, column=6).number_format = money_fmt
        premium_rows.append(r)
        r += 1

    last_data_row = r - 1

    # ── totals, all formulas ─────────────────────────────────────────────────
    r += 1
    net_row = r
    ws.cell(row=r, column=1, value='Total annual premium (excluding VAT)').font = navy_bold
    ws.cell(row=r, column=2, value=f'=SUM(B{first_data_row}:B{last_data_row})').number_format = money_fmt
    ws.cell(row=r, column=2).font = navy_bold
    ws.cell(row=r, column=6, value=f'=SUM(F{first_data_row}:F{last_data_row})').number_format = money_fmt
    ws.cell(row=r, column=6).font = navy_bold
    for c in range(1, 7):
        ws.cell(row=r, column=c).border = box
    r += 1

    # `or 12` swallows a ZERO (0 is falsy) and a nil-month quote would
    # then print twelve instalments. Treat only None as 'not set'.
    _pm = getattr(quote, 'period_months', None)
    months = 12 if _pm is None else int(_pm)
    charged_row = net_row
    if months < 12:
        ws.cell(row=r, column=1, value=f'Charged for {months} months ({months}/12 of the year)')
        ws.cell(row=r, column=6, value=f'=ROUND(F{net_row}*{months}/12,2)').number_format = money_fmt
        charged_row = r
        r += 1

    vat_row = r
    ws.cell(row=r, column=1, value=f'Value Added Tax at {int(VAT_RATE * 100)}%')
    ws.cell(row=r, column=6, value=f'=ROUND(F{charged_row}*{VAT_RATE},2)').number_format = money_fmt
    r += 1
    total_row = r
    ws.cell(row=r, column=1, value='Total payable').font = navy_bold
    ws.cell(row=r, column=6, value=f'=F{charged_row}+F{vat_row}').number_format = money_fmt
    ws.cell(row=r, column=6).font = navy_bold
    r += 2

    # ── payment options, also formulas ───────────────────────────────────────
    ws.cell(row=r, column=1, value='PAYMENT OPTIONS').font = navy_bold
    r += 1
    ws.cell(row=r, column=1, value='Annual — one payment')
    ws.cell(row=r, column=6, value=f'=F{total_row}').number_format = money_fmt
    r += 1
    ws.cell(row=r, column=1, value=f'Total payable ÷ {months}')
    monthly_row = r
    ws.cell(row=r, column=6, value=f'=ROUND(F{total_row}/{months},2)').number_format = money_fmt
    r += 1
    ws.cell(row=r, column=1, value=f'Payment plan charge ({PAYMENT_PLAN_PCT}% of the monthly premium)')
    charge_row = r
    ws.cell(row=r, column=6, value=f'=ROUND(F{monthly_row}*{PAYMENT_PLAN_PCT}/100,2)').number_format = money_fmt
    r += 1
    ws.cell(row=r, column=1, value='Monthly instalment').font = navy_bold
    inst_row = r
    ws.cell(row=r, column=6, value=f'=F{monthly_row}+F{charge_row}').number_format = money_fmt
    ws.cell(row=r, column=6).font = navy_bold
    r += 1
    ws.cell(row=r, column=1, value=f'Total over {months} months')
    ws.cell(row=r, column=6, value=f'=F{inst_row}*{months}').number_format = money_fmt
    r += 2

    if quote.exclusions:
        ws.cell(row=r, column=1, value='WHAT IS NOT COVERED').font = navy_bold
        r += 1
        for line in quote.exclusions:
            ws.cell(row=r, column=1, value=str(line))
            r += 1
        r += 1

    ws.cell(row=r, column=1,
            value=('This workbook re-prices itself: change a sum insured or a rate and every '
                   'premium, subtotal, VAT and instalment updates. The signed quotation is the PDF.'))
    ws.cell(row=r, column=1).font = Font(size=8, italic=True, color='6B7280')

    widths = [46, 18, 9, 18, 18, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    for row in ws.iter_rows(min_row=first_data_row, max_row=last_data_row, min_col=1, max_col=6):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=(cell.column == 1))

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_docx(quote) -> bytes:
    """The quotation as an editable Word document."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    navy = RGBColor(0x1D, 0x32, 0x70)
    doc = Document()

    h = doc.add_paragraph()
    run = h.add_run('ALPHA DIRECT INSURANCE COMPANY (PTY) LTD')
    run.bold = True
    run.font.size = Pt(13)
    run.font.color.rgb = navy
    sub = doc.add_paragraph()
    r2 = sub.add_run(f'Insurance Quotation · {quote.quote_number}')
    r2.font.size = Pt(10)
    r2.font.color.rgb = navy

    for k, v in (('Client', quote.client_name),
                 ('Class of business', quote.class_of_business or '—'),
                 ('Period', quote.period or '—'),
                 ('Broker', quote.broker or '—')):
        p = doc.add_paragraph()
        kr = p.add_run(f'{k}: ')
        kr.bold = True
        kr.font.size = Pt(10)
        vr = p.add_run(str(v))
        vr.font.size = Pt(10)

    doc.add_paragraph()
    from .quote_render import sections_with_guaranteed_rows
    rows = sections_with_guaranteed_rows(quote.sections)
    breakdown = section_premium_rows(quote.sections, quote.rate_incl_vat)
    net_by_src = {p['index']: p['net'] for p in breakdown['rows']} if breakdown else {}

    table = doc.add_table(rows=1, cols=5)
    table.style = 'Table Grid'
    for i, head in enumerate(['Section', 'Sum insured', 'Rate %', 'Excess', 'Premium']):
        cell = table.rows[0].cells[i]
        cell.text = head
        for para in cell.paragraphs:
            for run in para.runs:
                run.bold = True
                run.font.size = Pt(9)

    for i, s in rows:
        cells = table.add_row().cells
        name = s.get('name') or ''
        if (s.get('group') or '').strip():
            name = f"[{s['group'].strip()}]  {name}"
        rate = _to_decimal(s.get('rate'))
        net = net_by_src.get(i)
        values = [name, str(s.get('sum_insured') or ''),
                  f'{rate:.2f}%' if rate else '',
                  str(s.get('excess') or ''),
                  f'{net:,.2f}' if net is not None else '']
        for c, val in enumerate(values):
            cells[c].text = val
            for para in cells[c].paragraphs:
                for run in para.runs:
                    run.font.size = Pt(8.5)

    doc.add_paragraph()
    # A part-year Word copy must show the annual AND the charged figure, or a
    # broker adding the annual row premiums lands on twice the total.
    _pm = getattr(quote, 'period_months', None)
    months = 12 if _pm is None else int(_pm)
    lines = []
    if months < 12 and quote.annual_premium:
        lines.append(('Annual premium (excluding VAT)', quote.annual_premium))
        lines.append((f'Charged for {months} months ({months}/12 of the year)', quote.premium))
    else:
        lines.append(('Premium (excluding VAT)', quote.premium))
    lines.append((f'Value Added Tax at {int(VAT_RATE * 100)}%', quote.vat))
    lines.append(('Total payable', quote.total))
    for label, val in lines:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = p.add_run(f'{label}:  {val:,.2f}')
        run.font.size = Pt(10.5)
        if label == 'Total payable':
            run.bold = True
            run.font.color.rgb = navy

    if quote.exclusions:
        doc.add_paragraph()
        p = doc.add_paragraph()
        run = p.add_run('What is not covered')
        run.bold = True
        run.font.color.rgb = navy
        for line in quote.exclusions:
            doc.add_paragraph(str(line), style='List Bullet')

    doc.add_paragraph()
    note = doc.add_paragraph(
        'This is a working copy. The signed quotation is the PDF issued by Alpha Direct; '
        'the policy wording prevails over any summary here.')
    note.runs[0].font.size = Pt(8)
    note.runs[0].italic = True

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def filename_for(quote, ext: str) -> str:
    safe = ''.join(ch for ch in (quote.client_name or 'client')
                   if ch.isalnum() or ch in ' -_').strip().replace(' ', '_')[:40]
    return f'{quote.quote_number}_{safe or "quotation"}.{ext}'
