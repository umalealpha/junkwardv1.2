"""
ifrs17/exceptions_export.py — the IFRS 17 Exception Report as a workbook.

Three sheets, in the order a reader needs them:
  1. Materiality  — the threshold and how it was derived, so the filtering is
                    auditable before anyone reads a single exception.
  2. Exceptions   — the material ones, with Finance's explanation and the review.
  3. Below threshold — the small variances, listed and explicitly accepted with
                    no response required. Present so nobody can say they were hidden.

Brand: Book Antiqua, dark navy #0D1B2A, orange #F4A623 (finance house style).
"""
from __future__ import annotations

from . import materiality as M
from .exceptions_service import register_summary
from .models import IFRS17Exception

NAVY = '0D1B2A'
ORANGE = 'F4A623'


def register_workbook_bytes(financial_year: str = 'FY2026') -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    summary = register_summary(financial_year)
    basis = summary['materiality']

    wb = Workbook()
    wb.remove(wb.active)

    head_fill = PatternFill('solid', fgColor=NAVY)
    head_font = Font(name='Book Antiqua', color='FFFFFF', bold=True)
    title_font = Font(name='Book Antiqua', color=NAVY, bold=True, size=14)
    sub_font = Font(name='Book Antiqua', color=ORANGE, bold=True, size=11)
    body = Font(name='Book Antiqua', size=10)
    wrap = Alignment(wrap_text=True, vertical='top')

    # ---------------- 1. Materiality -------------------------------------- #
    ws = wb.create_sheet('Materiality')
    ws['A1'] = 'Alpha Direct Insurance Company (Pty) Ltd'
    ws['A1'].font = title_font
    ws['A2'] = f'IFRS 17 Exception Report — {financial_year}'
    ws['A2'].font = sub_font
    ws['A3'] = 'Premium Allocation Approach · Empirica Actuaries'
    ws['A3'].font = body

    rows = [
        ('', ''),
        ('How the threshold was set', ''),
        (f'Benchmark — {basis["benchmark_label"]}', basis['benchmark_amount']),
        (f'Overall materiality ({basis["overall_materiality_pct"]:.1%} of benchmark)',
         basis['overall_materiality']),
        (f'Performance materiality ({basis["performance_materiality_pct"]:.0%} of overall)',
         basis['performance_materiality']),
        ('Register threshold (rounded down)', basis['register_threshold']),
        ('Watch band floor (half the threshold)', basis['watch_floor']),
        ('', ''),
        (f'Cross-check — {basis["pbt_cross_check_pct"]:.0%} of profit before tax',
         basis['pbt_cross_check']),
        ('', ''),
        ('What this means', ''),
        (basis['note'], ''),
        ('', ''),
        ('Register position', ''),
        ('Exceptions disclosed by the actuary', summary['total']),
        ('Material — explanation required', summary['material']),
        ('   still outstanding with Finance', summary['outstanding']),
        ('   answered, awaiting review', summary['awaiting_review']),
        ('   accepted', summary['accepted']),
        ('Watch — monitored only', summary['watch']),
        ('Immaterial — no response required', summary['immaterial']),
        ('Total value of material exceptions', summary['material_exposure']),
    ]
    r = 5
    for label, value in rows:
        ws.cell(row=r, column=1, value=label).font = body
        if value != '':
            c = ws.cell(row=r, column=2, value=value)
            c.font = body
            if isinstance(value, int) is False:
                c.number_format = '#,##0.00'
        r += 1
    ws.column_dimensions['A'].width = 62
    ws.column_dimensions['B'].width = 20

    # ---------------- 2 + 3. the exceptions ------------------------------- #
    qs = IFRS17Exception.objects.filter(financial_year=financial_year)
    order = {'material': 0, 'watch': 1, 'immaterial': 2}
    allrows = sorted(qs, key=lambda e: (order.get(e.band, 9), -e.amount))

    def sheet(name: str, items: list, with_answers: bool) -> None:
        s = wb.create_sheet(name)
        cols = ['Ref', 'Exception', 'Amount (BWP)', 'Source', 'Why this band']
        if with_answers:
            cols += ['Status', "Finance's explanation", 'Action being taken',
                     'Answered by', 'Reviewed by', 'Review note']
        for i, h in enumerate(cols, start=1):
            c = s.cell(row=1, column=i, value=h)
            c.fill, c.font, c.alignment = head_fill, head_font, wrap
        for ri, e in enumerate(items, start=2):
            vals = [e.ref, e.title, e.amount, e.source, e.materiality_reason]
            if with_answers:
                vals += [e.get_status_display(), e.explanation, e.action,
                         (e.answered_by.get_full_name() if e.answered_by_id else ''),
                         (e.reviewed_by.get_full_name() if e.reviewed_by_id else ''),
                         e.review_note]
            for ci, v in enumerate(vals, start=1):
                c = s.cell(row=ri, column=ci, value=v)
                c.font, c.alignment = body, wrap
                if ci == 3:
                    c.number_format = '#,##0.00'
        widths = [9, 46, 16, 20, 60] + ([16, 70, 50, 20, 20, 40] if with_answers else [])
        for i, w in enumerate(widths, start=1):
            s.column_dimensions[chr(64 + i)].width = w

    sheet('Exceptions', [e for e in allrows if e.band == 'material'], True)
    sheet('Below threshold', [e for e in allrows if e.band != 'material'], False)

    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
