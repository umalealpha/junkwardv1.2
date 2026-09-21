"""
healthcare/quote_pdf.py — branded PDF for a Group Health quote / invoice.

CFO 2026-06-17 (idea pack): a proper Alpha Direct PDF (navy/orange header,
per-tier member tables, VAT, and — once invoiced — the FNB banking block + due
date) to replace the browser-print download. Built with reportlab (already a
dependency, same as healthcare/agreement_pdf.py).

    build_quote_pdf(quote) -> bytes
"""
from __future__ import annotations

import io
import os
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image,
)

# Alpha Direct logo — white monotone variant reads on the navy header band
# (Tlamelo 2026-06-18: "it still does not use the Alpha Direct Logo").
_LOGO_WHITE = os.path.join(os.path.dirname(__file__), 'ad-logo-white.png')

NAVY = HexColor('#0D1B2A')
ORANGE = HexColor('#F4A623')
GREY = HexColor('#6B7280')
LIGHT = HexColor('#F3F4F6')

from . import health_rates as HR
from .models import HealthQuote


def _money(v) -> str:
    return f"P{Decimal(v or 0):,.2f}"          # VAT + Incl-VAT: keep cents


def _money_whole(v) -> str:
    return f"P{Decimal(v or 0):,.0f}"          # Excl-VAT: whole Pula (printed-table match)


def build_quote_pdf(q: HealthQuote) -> bytes:
    invoiced = q.status == HealthQuote.Status.INVOICED
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"{q.ref} {q.client_name}")
    ss = getSampleStyleSheet()
    h_title = ParagraphStyle('t', parent=ss['Title'], textColor=ORANGE, fontSize=18, spaceAfter=2)
    h_sub = ParagraphStyle('s', parent=ss['Normal'], textColor=colors.white, fontSize=10)
    lbl = ParagraphStyle('l', parent=ss['Normal'], textColor=GREY, fontSize=8)
    val = ParagraphStyle('v', parent=ss['Normal'], textColor=NAVY, fontSize=9)
    foot = ParagraphStyle('f', parent=ss['Normal'], textColor=GREY, fontSize=7, leading=9)
    el = []

    # ── Header band ──
    head_kind = 'MEMBERSHIP LISTING / BILLING INVOICE' if invoiced else 'GROUP HEALTH INSURANCE QUOTATION'
    # Logo flowable (falls back to the wordmark text if the asset is missing).
    if os.path.exists(_LOGO_WHITE):
        logo = Image(_LOGO_WHITE, width=46 * mm, height=22.7 * mm)  # 2480x1226 ≈ 2.02:1
        logo.hAlign = 'LEFT'
        left_cell = logo
    else:
        left_cell = Paragraph('Alpha Direct Health', h_title)
    band = Table([[left_cell,
                   Paragraph(head_kind, ParagraphStyle('hk', parent=h_sub, alignment=2))]],
                 colWidths=[95 * mm, 83 * mm])
    band.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
    ]))
    el += [band, Spacer(1, 8)]

    # ── Quote / client meta ──
    meta = [
        [Paragraph('Quote Ref', lbl), Paragraph(q.ref, val),
         Paragraph('Client', lbl), Paragraph(q.client_name, val)],
        [Paragraph('Benefit Start', lbl), Paragraph(q.benefit_start.strftime('%d %b %Y') if q.benefit_start else '—', val),
         Paragraph('Underwriting', lbl), Paragraph(q.underwriting or 'Standard + Exclusions', val)],
        [Paragraph('VAT', lbl), Paragraph('14% (VAT Act Cap 50:01)', val),
         Paragraph('Status', lbl), Paragraph(q.get_status_display(), val)],
    ]
    if invoiced:
        meta.append([Paragraph('Invoice No', lbl), Paragraph(q.invoice_no, val),
                     Paragraph('Billing Period', lbl), Paragraph(q.billing_period or '—', val)])
    if q.client_address or q.contact_email:
        meta.append([Paragraph('Address', lbl), Paragraph(q.client_address or '—', val),
                     Paragraph('Contact', lbl), Paragraph(q.contact_email or q.contact_name or '—', val)])
    mt = Table(meta, colWidths=[24 * mm, 65 * mm, 24 * mm, 65 * mm])
    mt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 3), ('TOPPADDING', (0, 0), (-1, -1), 3)]))
    el += [mt, Spacer(1, 8)]

    # ── Per-tier member tables ──
    members = list(q.members.all())
    by_tier = {}
    for m in members:
        by_tier.setdefault(m.tier, []).append(m)

    cell = ParagraphStyle('c', parent=ss['Normal'], fontSize=8, leading=10)
    for tier, mem in by_tier.items():
        el.append(Paragraph(HR.PLAN_LABELS.get(tier, tier) + f"  ·  {len(mem)} lives", val))
        rows = [['#', 'Full Name', 'Status', 'Gen', 'Age', 'Band', 'Excl VAT', 'VAT', 'Incl VAT']]
        sub_e = sub_v = sub_i = Decimal('0')
        for i, m in enumerate(mem, 1):
            rows.append([str(i), m.full_name, HR.MEMBER_TYPES.get(m.member_type, m.member_type),
                         m.gender, str(m.age), m.age_band,
                         f"{m.premium_excl:,.0f}", f"{m.vat:,.2f}", f"{m.premium_incl:,.2f}"])
            sub_e += m.premium_excl; sub_v += m.vat; sub_i += m.premium_incl
        rows.append(['', f'{HR.PLAN_LABELS.get(tier, tier)} total', '', '', '', '',
                     f"{sub_e:,.0f}", f"{sub_v:,.2f}", f"{sub_i:,.2f}"])
        t = Table(rows, colWidths=[7 * mm, 50 * mm, 26 * mm, 10 * mm, 10 * mm, 18 * mm, 19 * mm, 16 * mm, 22 * mm])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 8), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (6, 0), (-1, -1), 'RIGHT'), ('ALIGN', (3, 0), (5, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, LIGHT]),
            ('BACKGROUND', (0, -1), (-1, -1), ORANGE), ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('LINEBELOW', (0, 0), (-1, 0), 0.5, NAVY), ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        el += [t, Spacer(1, 8)]

    # ── Per-tier discount summary (Tlamelo 2026-06-25) ──
    from decimal import ROUND_HALF_UP
    Q2 = Decimal('0.01')
    disc_rows = [['Plan Tier', 'Premium (excl)', 'Discount %', 'Discount', 'Net (excl)']]
    any_disc = False
    for tier, mem in by_tier.items():
        rack = sum((m.premium_excl for m in mem), Decimal('0'))
        pct = HR.discount_for(tier, q.tier_discounts or {})
        disc = (rack * pct / Decimal('100')).quantize(Q2, rounding=ROUND_HALF_UP)
        if disc != 0:
            any_disc = True
        gate = HR.TIER_DISCOUNT_GATES.get(tier, '')
        label = HR.PLAN_LABELS.get(tier, tier) + (f'  ({gate})' if gate else '')
        disc_rows.append([label, f"{rack:,.0f}", f"{pct:g}%", f"{disc:,.2f}", f"{rack - disc:,.2f}"])
    if any_disc:
        el.append(Paragraph('Discounts applied', val))
        dt = Table(disc_rows, colWidths=[68 * mm, 28 * mm, 22 * mm, 28 * mm, 32 * mm])
        dt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), NAVY), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTSIZE', (0, 0), (-1, -1), 8), ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT]),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        el += [dt, Spacer(1, 8)]

    # ── Grand totals (NET after the per-tier discount) ──
    # gross_excl is 0 on pre-discount quotes (added 2026-06-25); fall back to the
    # stored subtotal so their PDFs/invoices render unchanged.
    gross_disp = q.gross_excl if q.gross_excl else q.subtotal_excl
    tot_rows = [['Subtotal (excl VAT)', _money_whole(gross_disp)]]
    if q.discount_excl and Decimal(q.discount_excl) != 0:
        tot_rows.append(['Less discount', '-' + _money(q.discount_excl)])
        tot_rows.append(['Net subtotal (excl VAT)', _money(q.subtotal_excl)])
    tot_rows += [['VAT (14%)', _money(q.vat)],
                 ['TOTAL (incl VAT)', _money(q.total_incl)]]
    tot = Table(tot_rows, colWidths=[150 * mm, 28 * mm])
    tot.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'), ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('TEXTCOLOR', (0, -1), (-1, -1), NAVY),
        ('LINEABOVE', (0, -1), (-1, -1), 1, ORANGE), ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))
    el += [tot, Spacer(1, 10)]

    # ── Banking block (invoice only) ──
    if invoiced:
        from .quote_views import COMPANY_INVOICE as C
        el.append(HRFlowable(width='100%', thickness=0.5, color=LIGHT))
        el.append(Spacer(1, 4))
        el.append(Paragraph('Banking Details', val))
        bank = Table([
            ['Bank', C['bank'], 'Account Name', C['account_name']],
            ['Account No.', C['account_no'], 'Branch Code', C['branch_code']],
            ['SWIFT', C['swift'], 'VAT Reg.', C['vat_reg']],
        ], colWidths=[22 * mm, 67 * mm, 24 * mm, 65 * mm])
        bank.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 8),
                                  ('TEXTCOLOR', (0, 0), (0, -1), GREY), ('TEXTCOLOR', (2, 0), (2, -1), GREY),
                                  ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
        el += [bank, Spacer(1, 8)]

    el.append(HRFlowable(width='100%', thickness=0.5, color=LIGHT))
    el.append(Spacer(1, 4))
    el.append(Paragraph(
        'Premiums are risk-rated and may be re-rated subject to medical / risk-analysis outcomes. '
        'Cover is subject to policy wording, benefit limits and documented exclusions per the Alpha Direct '
        'Health Insurance Policy. This document was generated by Omni. Ref: ' + str(q.ref) + '.', foot))
    doc.build(el)
    return buf.getvalue()
