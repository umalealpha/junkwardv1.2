"""Build the Authority to Recruit as a Word document (CFO 2026-08-03).

House style: Book Antiqua, dark navy #0D1B2A headings, orange #F4A623 accent.
The document shows the grade structure line by line so a signatory can
reconcile it to HR's workbook, states BOTH the package HR quoted and the real
cost of employment, and ends in five signature blocks.

python-docx only — no external service sees a named person's pay.
"""
from __future__ import annotations

from decimal import Decimal

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

NAVY = RGBColor(0x0D, 0x1B, 0x2A)
ORANGE = RGBColor(0xF4, 0xA6, 0x23)
GREY = RGBColor(0x6B, 0x72, 0x80)
RED = RGBColor(0xB4, 0x23, 0x18)
SERIF = 'Book Antiqua'


def _money(cur: str, v) -> str:
    d = Decimal(str(v or 0))
    return f'{cur} {d:,.2f}'


def _set_font(run, *, size=10.5, bold=False, colour=None, name=SERIF):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    if colour is not None:
        run.font.color.rgb = colour
    # python-docx sets the latin font only; east-asian must be set on rPr or
    # Word silently substitutes a different face for the whole run.
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = rpr.makeelement(qn('w:rFonts'), {})
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), name)


def _para(doc, text='', *, size=10.5, bold=False, colour=None, space_after=6,
          align=None):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    if text:
        _set_font(p.add_run(text), size=size, bold=bold, colour=colour)
    return p


def _kv_table(doc, rows):
    t = doc.add_table(rows=0, cols=2)
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for k, v in rows:
        cells = t.add_row().cells
        _set_font(cells[0].paragraphs[0].add_run(k), size=10, bold=True, colour=NAVY)
        _set_font(cells[1].paragraphs[0].add_run(str(v)), size=10)
    return t


def build(authority) -> bytes:
    """Return the .docx bytes for one AuthorityToRecruit."""
    a = authority
    cur = a.currency or 'BWP'
    is_regrade = a.kind == a.Kind.REGRADE
    title = 'AUTHORITY TO REGRADE' if is_regrade else 'AUTHORITY TO RECRUIT'

    doc = Document()
    style = doc.styles['Normal']
    style.font.name = SERIF
    style.font.size = Pt(10.5)

    _para(doc, a.entity, size=13, bold=True, colour=NAVY, space_after=0)
    _para(doc, title, size=17, bold=True, colour=ORANGE, space_after=2)
    _para(doc, f'Reference {a.reference}', size=9.5, colour=GREY, space_after=14)

    if is_regrade:
        # Never let a promotion pass as a hire — say so on the face of the paper.
        _para(doc,
              'This is a regrade of an existing employee, not an external appointment. '
              'It is presented on the same instrument because it needs the same five '
              'signatures.',
              size=9.5, bold=True, colour=RED, space_after=12)

    _para(doc, '1. The appointment', size=11.5, bold=True, colour=NAVY, space_after=4)
    if a.tier_id:
        band = '—'
        if a.tier.basic_salary_min is not None or a.tier.basic_salary_max is not None:
            lo = _money(cur, a.tier.basic_salary_min) if a.tier.basic_salary_min is not None else '—'
            hi = _money(cur, a.tier.basic_salary_max) if a.tier.basic_salary_max is not None else '—'
            band = f'{lo} — {hi} / mo (basic)'
        tier_rows = [
            ('Position tier', f'Tier {a.tier.tier} — {a.tier.name}'),
            ('Basic-salary band', band),
            ('Proposed basic (monthly)', _money(cur, a.proposed_basic_salary)),
        ]
    else:
        tier_rows = [('Grade / level', a.level or '—')]
    _kv_table(doc, [
        ('Name', a.person_name),
        ('Position', a.position),
        ('Department', a.department or '—'),
        *tier_rows,
        ('Employment type', a.get_employment_type_display()),
        ('Headcount authorised', a.headcount),
        ('Effective date', a.effective_date.strftime('%d %B %Y') if a.effective_date else 'On acceptance'),
    ])
    if a.is_salary_exception():
        who = ', '.join(l for _s, l, _e in a.exception_signers()) or 'the exception approvers'
        _para(doc,
              f'Salary-band exception: the proposed basic salary is ABOVE the Tier {a.tier.tier} '
              f'ceiling ({_money(cur, a.tier_ceiling())} / mo). It proceeds on the justification '
              f'below and must be approved by {who}.',
              size=9.5, bold=True, colour=RED, space_after=4)
    _para(doc, space_after=10)

    _para(doc, '2. Why this role is needed', size=11.5, bold=True, colour=NAVY, space_after=4)
    _para(doc, a.justification or '—', space_after=12)

    _para(doc, '3. Salary and benefits structure', size=11.5, bold=True, colour=NAVY, space_after=4)
    lines = [ln for ln in (a.salary_lines or [])
             if (ln.get('item') or '').strip()
             and not (ln.get('item') or '').strip().lower().startswith('total')]
    t = doc.add_table(rows=1, cols=4)
    t.style = 'Table Grid'
    for i, head in enumerate(('Item', f'Monthly ({cur})', f'Annual ({cur})', 'Note')):
        _set_font(t.rows[0].cells[i].paragraphs[0].add_run(head), size=10, bold=True, colour=NAVY)
    for ln in lines:
        c = t.add_row().cells
        _set_font(c[0].paragraphs[0].add_run(str(ln.get('item') or '')), size=10)
        _set_font(c[1].paragraphs[0].add_run(f"{Decimal(str(ln.get('monthly') or 0)):,.2f}"), size=10)
        _set_font(c[2].paragraphs[0].add_run(f"{Decimal(str(ln.get('annual') or 0)):,.2f}"), size=10)
        _set_font(c[3].paragraphs[0].add_run(str(ln.get('note') or '')), size=9, colour=GREY)
    _para(doc, space_after=10)

    _para(doc, '4. What this costs', size=11.5, bold=True, colour=NAVY, space_after=4)
    cost_m, cost_a = a.cost_to_company()
    var_m, var_a = a.variance_to_quote()
    _kv_table(doc, [
        ('Package quoted to the candidate (monthly)', _money(cur, a.quoted_ctc_monthly)),
        ('Package quoted to the candidate (annual)', _money(cur, a.quoted_ctc_annual)),
        ('True cost of employment (monthly)', _money(cur, cost_m)),
        ('True cost of employment (annual)', _money(cur, cost_a)),
    ])
    if var_m or var_a:
        # State each column in its own direction. On HR's workbooks the monthly
        # figure UNDER-states the cost (it deducts the employee's own provident
        # fund) while the annual figure OVER-states it (it adds the leave-pay
        # accrual on top of a base that already contains it) — and the two do
        # not reconcile to each other. A single "higher than quoted" sentence
        # would be wrong on one of the two lines.
        def _phrase(var, period):
            if not var:
                return f'The {period} figure agrees.'
            word = 'HIGHER' if var > 0 else 'LOWER'
            return (f'The true {period} cost is {_money(cur, abs(var))} {word} than '
                    f'the {period} figure quoted.')

        _para(doc, space_after=4)
        _para(doc, f'{_phrase(var_m, "monthly")} {_phrase(var_a, "annual")}',
              size=9.5, bold=True, colour=RED, space_after=4)
        _para(doc,
              'The quoted structure deducted the employee\'s own provident-fund contribution '
              'from the company\'s cost, and added the leave-pay accrual to the annual column '
              'only. The employee\'s contribution comes out of a base salary that is already '
              'counted, so it is not a saving to the company; leave pay is an accrual inside '
              'base, not an extra cost. The quoted monthly and annual figures also do not '
              'reconcile to each other. Budget on the true cost of employment above.',
              size=9.5, colour=GREY, space_after=12)
    else:
        _para(doc, space_after=10)

    _para(doc, '5. Approval', size=11.5, bold=True, colour=NAVY, space_after=4)
    _para(doc,
          'By signing below each signatory authorises this appointment on the terms set '
          'out above. All signatures shown are required before an offer is issued.',
          size=10, space_after=8)

    sig = doc.add_table(rows=1, cols=4)
    sig.style = 'Table Grid'
    for i, head in enumerate(('Role', 'Name', 'Signature / decision', 'Date')):
        _set_font(sig.rows[0].cells[i].paragraphs[0].add_run(head), size=10, bold=True, colour=NAVY)
    approvals = a.approvals or {}
    chain = a.signatory_chain()
    for slug, label, _email in chain:
        rec = approvals.get(slug) or {}
        c = sig.add_row().cells
        _set_font(c[0].paragraphs[0].add_run(label), size=10, bold=True)
        _set_font(c[1].paragraphs[0].add_run(rec.get('by') or ''), size=10)
        decision = (rec.get('decision') or '').lower()
        mark = 'Approved' if decision == 'approved' else ('Declined' if decision == 'declined' else '')
        _set_font(c[2].paragraphs[0].add_run(mark or '________________'), size=10,
                  bold=bool(mark),
                  colour=(RED if decision == 'declined' else (NAVY if mark else None)))
        at = (rec.get('at') or '')[:10]
        _set_font(c[3].paragraphs[0].add_run(at or '____ / ____ / ________'), size=10)

    _para(doc, space_after=8)
    outstanding = a.outstanding_signatories()
    _labels = dict((s, l) for s, l, _e in chain)
    state = ('Fully approved.' if not outstanding
             else 'Outstanding: ' + ', '.join(_labels.get(s, s) for s in outstanding))
    _para(doc, f'Status: {a.get_status_display()}. {state}', size=9.5, colour=GREY, space_after=0)
    _para(doc,
          'Confidential — the pay of a named individual. Restricted to its signatories.',
          size=8.5, colour=GREY, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=0)

    from io import BytesIO
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def filename_for(authority) -> str:
    who = (authority.person_name or 'candidate').replace(' ', '_')
    kind = 'Authority_to_Regrade' if authority.kind == authority.Kind.REGRADE else 'Authority_to_Recruit'
    return f'{kind}-{who}-{authority.reference}.docx'
