"""
ifrs17/data_request.py — generate Empirica's IFRS 17 data-request workbook.

`FY26/Alpha Direct - IFRS17 Data Request FY2026.xlsx` is the input contract
Empirica sends every year and Finance fills BY HAND. Its tabs are: Instructions ·
1. Premium & UPR · 2. Claims & OCR · 3. IBNR CHER & Recon · 3b. Claims Extract
(IBNR) · 4. Reinsurance · 5. Expenses · 6. Checklist.

Omni holds the source for nearly all of it. This produces the workbook pre-filled
from the signed valuation, so next year the starting point is a completed draft
tied to the trial balance rather than a blank template. The actuary still reviews
and signs; this removes the copy-typing, not the judgement.

Nothing here posts to the ledger.
"""
from __future__ import annotations

import io
from decimal import Decimal as D

from . import constants as K
from .engine import Computed


def _fill(ws, rows, start=2):
    from openpyxl.styles import Font, PatternFill
    hdr = PatternFill('solid', fgColor='0D1B2A')
    hf = Font(color='FFFFFF', bold=True)
    for ci, h in enumerate(rows[0], start=1):
        cell = ws.cell(row=start, column=ci, value=h)
        cell.fill = hdr
        cell.font = hf
    for ri, row in enumerate(rows[1:], start=start + 1):
        for ci, val in enumerate(row, start=1):
            v = float(val) if isinstance(val, (int, float, D)) else val
            cell = ws.cell(row=ri, column=ci, value=v)
            if isinstance(val, (int, float, D)) and ci > 1:
                cell.number_format = '#,##0'
    for ci in range(1, len(rows[0]) + 1):
        ws.column_dimensions[chr(64 + ci)].width = 26 if ci == 1 else 18


def workbook_bytes(c: Computed) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)

    # Instructions
    ins = wb.create_sheet('Instructions')
    ins['A1'] = 'ALPHA DIRECT — IFRS 17 DATA REQUEST'
    ins['A1'].font = Font(bold=True, size=13, color='0D1B2A')
    ins['A2'] = f'Valuation as at 30 June 2026 · pre-filled from Omni'
    ins['A4'] = ('Every figure below is drawn from the Omni general ledger and the '
                 'signed valuation. Empirica reviews, adjusts and signs — this is a '
                 'completed starting draft, not a final submission.')
    ins['A6'] = 'Tie all totals back to the general ledger and the finalised financials.'
    ins.column_dimensions['A'].width = 100

    # 1. Premium & UPR — per segment
    prem = [['Segment', 'Gross written premium', 'Unearned premium', 'Commission']]
    for seg, (p, cm, cl, ue) in K.SEGMENT_FY26.items():
        prem.append([seg.title(), p, ue, cm])
    prem.append(['TOTAL', sum(v[0] for v in K.SEGMENT_FY26.values()),
                 sum(v[3] for v in K.SEGMENT_FY26.values()),
                 sum(v[1] for v in K.SEGMENT_FY26.values())])
    _fill(wb.create_sheet('1. Premium & UPR'), prem)

    # 2. Claims & OCR
    claims = [['Segment', 'Claims incurred', 'Case reserves (OCR)']]
    for seg, (p, cm, cl, ue) in K.SEGMENT_FY26.items():
        claims.append([seg.title(), cl, ''])
    claims.append(['Gross case reserves (total)', '', float(c.gross_case_reserves)])
    _fill(wb.create_sheet('2. Claims & OCR'), claims)

    # 3. IBNR CHER & Recon — by underwriting year
    ibnr = [['Underwriting year', 'Observed cumulative', 'Selected ultimate',
             'Outstanding', 'IBNR']]
    for uwy, (obs, ult, out, ib) in sorted(K.IBNR_BY_UWY_FY26.items()):
        ibnr.append([uwy, obs, ult, out, ib])
    ibnr.append(['Signed selection', '', '', '', float(c.gross_ibnr)])
    ibnr.append(['Claims-handling expense reserve', '', '', '', float(c.che_reserve)])
    _fill(wb.create_sheet('3. IBNR CHER & Recon'), ibnr)

    # 4. Reinsurance — by treaty
    ri = [['Treaty', 'Ceded premium', 'Commission', 'Direct recovery']]
    for t, (ceded, comm, rec) in K.TREATIES_FY26.items():
        ri.append([t.replace('_', ' ').title(), ceded, comm, rec])
    _fill(wb.create_sheet('4. Reinsurance'), ri)

    # 5. Expenses
    exp = [['Item', 'Amount'],
           ['Total operating expenses', float(K.REPORTED['FY2026']['total_operating_expenses'])],
           ['Attributable portion', float(c.attributable_expenses)],
           ['Non-attributable, excluded', float(K.REPORTED['FY2026']['non_attributable_expenses'])],
           ['Salvage & subrogation deduction', float(c.salvage_subrogation)]]
    _fill(wb.create_sheet('5. Expenses'), exp)

    # 6. Checklist — including the disclosed differences the actuary must see
    chk = [['Ref', 'Item to confirm', 'Amount', 'Severity']]
    for v in K.KNOWN_VARIANCES:
        chk.append([v['ref'], v['title'], float(v['amount']), v['severity']])
    _fill(wb.create_sheet('6. Checklist'), chk)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
