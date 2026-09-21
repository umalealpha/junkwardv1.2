"""
reporting/audit_pack.py

Generates the Audit Pack — an auditor-ready bundle of:
  1. Cover page (entity, period, scope, prepared-by)
  2. Trial Balance for the period
  3. General Ledger (every account with activity in the period)
  4. Posted Journal Entries listing
  5. Notes / sign-off page

Output is a single PDF, rendered with ReportLab.

Use either:
  build_audit_pack(from_date, to_date, company_id=None)        # JSON-ish
  build_audit_pack_pdf(from_date, to_date, company_id=None)    # bytes
"""

from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

from . import reports as _reports
from django.utils import timezone

ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# JSON builder (frontend / API consumer)
# ---------------------------------------------------------------------------

def build_audit_pack(from_date, to_date, company_id=None):
    """
    Assemble the full pack as nested dicts. Each section uses the existing
    builders in reporting/reports.py, so the audit pack and the on-screen
    reports can never disagree.
    """
    from billing.models import Invoice
    from ledger.models import JournalEntry
    from core.models import Company

    # CFO directive 2026-05-20: TB now takes from_date + to_date explicitly.
    # Audit pack already has both dates in scope from the wrapping
    # build_audit_pack(from_date, to_date) signature, so pass through.
    tb       = _reports.build_trial_balance(to_date, from_date=from_date, company_id=company_id)
    pl       = _reports.build_profit_loss(from_date, to_date, company_id=company_id)
    bs       = _reports.build_balance_sheet(to_date, company_id=company_id)

    # JE listing for the period
    je_qs = (JournalEntry.objects
             .filter(status=JournalEntry.Status.POSTED,
                     entry_date__gte=from_date, entry_date__lte=to_date)
             .select_related('created_by'))
    if company_id:
        je_qs = je_qs.filter(company_id=company_id)
    je_qs = je_qs.order_by('entry_date', 'entry_number')
    je_rows = [{
        'entry_number': je.entry_number,
        'entry_date':   str(je.entry_date),
        'description':  je.description,
        'journal_type': je.journal_type,
        'created_by':   je.created_by.username if je.created_by_id else '',
        'is_related_party': bool(getattr(je, 'is_related_party', False)),
    } for je in je_qs]

    company = None
    if company_id:
        c = Company.objects.filter(pk=company_id).first()
        if c:
            company = {'code': c.code, 'name': c.name, 'legal_name': c.legal_name}

    return {
        'from_date':        str(from_date),
        'to_date':          str(to_date),
        'company':          company,
        'trial_balance':    tb,
        'profit_loss':      pl,
        'balance_sheet':    bs,
        'journal_entries':  je_rows,
        'journal_entry_count': len(je_rows),
    }


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def _money(val) -> str:
    if val is None:
        return ''
    return f"{Decimal(str(val)):,.2f}"


def _section_title(text, styles):
    return Paragraph(f"<b>{text}</b>", styles['Heading2'])


def _table(data, col_widths=None, header_bg=colors.HexColor('#0B0B3B')):
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND',  (0, 0), (-1, 0), header_bg),
        ('TEXTCOLOR',   (0, 0), (-1, 0), colors.white),
        ('FONTNAME',    (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',    (0, 0), (-1, 0), 8),
        ('FONTSIZE',    (0, 1), (-1, -1), 7.5),
        ('GRID',        (0, 0), (-1, -1), 0.25, colors.HexColor('#E5E7EB')),
        ('ALIGN',       (-2, 1), (-1, -1), 'RIGHT'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
         [colors.white, colors.HexColor('#F9FAFB')]),
        ('VALIGN',      (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',(0, 0), (-1, -1), 4),
        ('TOPPADDING',  (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
    ]))
    return t


def build_audit_pack_pdf(from_date, to_date, company_id=None) -> bytes:
    """Return the audit pack as PDF bytes (ready to stream to the browser)."""
    pack = build_audit_pack(from_date, to_date, company_id)

    buf = io.StringIO()  # not used; reportlab takes BytesIO
    out = io.BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title='Audit Pack',
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Small', fontSize=8, leading=10,
                              textColor=colors.HexColor('#374151')))
    story = []

    # ── Cover ────────────────────────────────────────────────────────────
    company_label = ''
    if pack['company']:
        company_label = f"{pack['company']['code']} — {pack['company']['legal_name'] or pack['company']['name']}"
    else:
        company_label = 'All companies (consolidated)'

    story.append(Paragraph('<b>AUDIT PACK</b>', styles['Title']))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(company_label, styles['Heading2']))
    story.append(Paragraph(f"Period: <b>{pack['from_date']}</b> to <b>{pack['to_date']}</b>",
                           styles['Heading3']))
    story.append(Paragraph(f"Generated: {timezone.localdate().isoformat()}", styles['Small']))
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph(
        'This pack contains the trial balance, profit & loss, balance sheet, '
        'and full posted-journal-entry listing for the period above. '
        'All figures are derived from the same source as the on-screen reports.',
        styles['Small']))
    story.append(PageBreak())

    # ── Trial Balance ───────────────────────────────────────────────────
    story.append(_section_title(f"1. Trial Balance — as of {pack['to_date']}", styles))
    story.append(Spacer(1, 4 * mm))
    tb_rows = [['Code', 'Account', 'Type', 'Opening', 'DR', 'CR', 'Closing']]
    for row in pack['trial_balance'].get('accounts', []):
        tb_rows.append([
            row['code'], row['name'], row['account_type'],
            _money(row['opening_balance']),
            _money(row['period_debits']),
            _money(row['period_credits']),
            _money(row['closing_balance']),
        ])
    tb_totals = pack['trial_balance']['totals']
    tb_rows.append([
        '', '', 'TOTAL',
        '', _money(tb_totals['total_debits']), _money(tb_totals['total_credits']), '',
    ])
    story.append(_table(tb_rows,
        col_widths=[18*mm, 70*mm, 25*mm, 30*mm, 30*mm, 30*mm, 30*mm]))
    story.append(Spacer(1, 4 * mm))
    bal_text = 'BALANCED' if tb_totals.get('balanced') else 'OUT OF BALANCE'
    story.append(Paragraph(f"<b>{bal_text}</b>  DR {_money(tb_totals['total_debits'])} = "
                           f"CR {_money(tb_totals['total_credits'])}", styles['Small']))
    story.append(PageBreak())

    # ── P&L ─────────────────────────────────────────────────────────────
    story.append(_section_title(f"2. Profit & Loss — {pack['from_date']} to {pack['to_date']}", styles))
    story.append(Spacer(1, 4 * mm))
    pl = pack['profit_loss']
    pl_rows = [['Section', 'Code', 'Account', 'Amount']]
    for r in pl.get('revenue', {}).get('accounts', []):
        pl_rows.append(['Revenue', r['code'], r['name'], _money(r['balance'])])
    pl_rows.append(['', '', 'TOTAL REVENUE', _money(pl.get('revenue', {}).get('total'))])
    for r in pl.get('cost_of_insurance', {}).get('accounts', []):
        pl_rows.append(['Cost of Insurance', r['code'], r['name'], _money(r['balance'])])
    for r in pl.get('operating_expenses', {}).get('accounts', []):
        pl_rows.append(['Operating Expenses', r['code'], r['name'], _money(r['balance'])])
    pl_rows.append(['', '', 'NET PROFIT', _money(pl.get('net_profit'))])
    story.append(_table(pl_rows,
        col_widths=[40*mm, 18*mm, 130*mm, 40*mm]))
    story.append(PageBreak())

    # ── Balance Sheet (compact) ─────────────────────────────────────────
    story.append(_section_title(f"3. Balance Sheet — as of {pack['to_date']}", styles))
    story.append(Spacer(1, 4 * mm))
    bs = pack['balance_sheet']
    bs_rows = [['Section', 'Code', 'Account', 'Balance']]
    for sec_key, sec_label in [
        ('current_assets', 'Current Assets'),
        ('non_current_assets', 'Non-Current Assets'),
        ('current_liabilities', 'Current Liabilities'),
        ('non_current_liabilities', 'Non-Current Liabilities'),
        ('equity', 'Equity'),
    ]:
        for r in bs.get(sec_key, {}).get('accounts', []) or []:
            bs_rows.append([sec_label, r.get('code', ''), r.get('name', ''), _money(r.get('balance'))])
    bs_totals = bs.get('totals', {})
    bs_rows.append(['', '', 'TOTAL ASSETS', _money(bs_totals.get('total_assets'))])
    bs_rows.append(['', '', 'TOTAL LIABILITIES', _money(bs_totals.get('total_liabilities'))])
    bs_rows.append(['', '', 'TOTAL EQUITY', _money(bs_totals.get('total_equity'))])
    story.append(_table(bs_rows,
        col_widths=[40*mm, 18*mm, 130*mm, 40*mm]))
    story.append(PageBreak())

    # ── Journal Entries listing ─────────────────────────────────────────
    story.append(_section_title(
        f"4. Posted Journal Entries — {pack['from_date']} to {pack['to_date']} "
        f"({pack['journal_entry_count']} entries)", styles))
    story.append(Spacer(1, 4 * mm))
    je_rows = [['Date', 'Entry #', 'Type', 'Description', 'RP?', 'Created by']]
    for je in pack['journal_entries']:
        je_rows.append([
            je['entry_date'], je['entry_number'], je['journal_type'],
            (je['description'] or '')[:90],
            'Yes' if je['is_related_party'] else '',
            je['created_by'],
        ])
    if len(je_rows) == 1:
        je_rows.append(['—', '—', '', 'No posted entries in this period', '', ''])
    story.append(_table(je_rows,
        col_widths=[22*mm, 28*mm, 22*mm, 110*mm, 12*mm, 36*mm]))
    story.append(PageBreak())

    # ── Sign-off ───────────────────────────────────────────────────────
    story.append(_section_title('5. Reviewer Sign-off', styles))
    story.append(Spacer(1, 6 * mm))
    sign_rows = [
        ['Prepared by',       '________________________', 'Date', '__________'],
        ['Reviewed by (FM)',  '________________________', 'Date', '__________'],
        ['Approved by (CFO)', '________________________', 'Date', '__________'],
    ]
    story.append(Table(sign_rows, colWidths=[40*mm, 70*mm, 20*mm, 40*mm]))

    doc.build(story)
    return out.getvalue()
