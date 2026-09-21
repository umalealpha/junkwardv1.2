"""
reporting/entity_pl_engine.py

Generic, template-driven P&L engine for the non-ADIC group entities.

Reads:
  * `reporting.entity_pl_templates.TEMPLATES` — per-entity section/line spec
  * `core.models.Company`                     — for currency + display name
  * `ledger.models.JournalEntryLine`          — for posted GL activity

Returns the same shape `reporting.ma_pl.build_ma_pl` returns so the
frontend can render either with the same component:

  {
    'entity_code':  'QIH',
    'entity_name':  'Quantum Insurance Holdings',
    'report_title': 'Profit & Loss Statement',
    'currency':     'BWP',
    'from_date':    '...',
    'to_date':      '...',
    'sections':     [ {id, label, lines:[...], subtotal_label, subtotal}, ... ],
    'totals':       [ {id, label, amount}, ... ],
  }

Sections with all-zero lines are STILL returned (so the frontend can
choose to hide them), with a top-level `nonzero_section_ids` list
indicating which sections actually have activity.

CFO directive 2026-05-21 (Group P&L Prompt PDF):
ADIC is OUT OF SCOPE for this engine — its insurance MA P&L stays on
`build_ma_pl` in `reporting/ma_pl.py`. Engine raises if asked for
ADIC.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date
from typing import Iterable

from django.db.models import Q, Sum

from core.models import Company
from ledger.models import JournalEntryLine

from .entity_pl_templates import get_template


ZERO = Decimal('0.00')


def _match_q(keywords: Iterable[str], exclude: Iterable[str]) -> Q:
    """Build an OR-of-icontains Q over Account.name, minus excludes."""
    inc = Q()
    for kw in keywords:
        inc |= Q(account__name__icontains=kw)
    if not exclude:
        return inc
    exc = Q()
    for kw in exclude:
        exc |= Q(account__name__icontains=kw)
    return inc & ~exc


def _line_amount(qs, sign: str, use_bwp: bool) -> Decimal:
    """Sum filtered queryset, sign-adjusted.

    For income lines, return credit-debit (positive = inflow).
    For expense lines, return debit-credit (positive = cost).
    """
    if use_bwp:
        agg = qs.aggregate(d=Sum('debit_bwp'),    c=Sum('credit_bwp'))
    else:
        agg = qs.aggregate(d=Sum('debit_amount'), c=Sum('credit_amount'))
    d = agg['d'] or ZERO
    c = agg['c'] or ZERO
    return (c - d) if sign == 'income' else (d - c)


def build_entity_pl(
    entity_code: str,
    from_date: date,
    to_date: date,
    *,
    use_bwp_columns: bool = False,
) -> dict:
    """Build a per-entity P&L using its template.

    Args:
      entity_code: Company.code (e.g. 'QIH'), or the PDF short code
                   ('QTM') — get_template handles both.
      from_date / to_date: inclusive window.
      use_bwp_columns: if True, sums `debit_bwp` / `credit_bwp` (group
                   reporting). If False (default), sums the native-
                   currency `debit_amount` / `credit_amount` so foreign
                   entities (ADSA/ZAR, ADIPL/USD, AIZ/ZMW, ADRG/INR)
                   render in their own currency. CFO directive
                   2026-05-21: report in the entity's own currency.
    """
    template = get_template(entity_code)
    if template is None:
        raise ValueError(f"No P&L template for entity '{entity_code}'. "
                         f"ADIC uses the MA P&L spec — call build_ma_pl.")

    # Resolve to a real Company row — the engine accepts either
    # DB code or PDF alias.
    company = (Company.objects.filter(code=template['entity_code']).first()
               or Company.objects.filter(code__iexact=template['entity_code']).first())
    if company is None:
        raise ValueError(f"Company {template['entity_code']} not found in DB.")

    # Currency: template wins; fall back to Company.base_currency.
    currency = template.get('currency') or (
        company.base_currency.code if company.base_currency_id else 'BWP'
    )

    # JEL base queryset — posted entries only, scoped to this company.
    base = JournalEntryLine.objects.filter(
        journal_entry__company_id=company.id,
        journal_entry__entry_date__gte=from_date,
        journal_entry__entry_date__lte=to_date,
        journal_entry__status='posted',
    ).select_related('account')

    # Currency rule: use BWP columns only when the entity reports in
    # BWP. Foreign-currency entities sum their native amount columns
    # because their JEs are posted in their base currency.
    use_bwp = use_bwp_columns or (currency == 'BWP')

    section_results = []
    section_totals: dict[str, Decimal] = {}
    nonzero_section_ids = []

    for sec in template['sections']:
        lines_out = []
        subtotal = ZERO
        sec_sign = sec['sign']
        any_nonzero = False

        for line in sec['lines']:
            qs = base.filter(_match_q(line['keywords'], line.get('exclude') or []))
            amount = _line_amount(qs, sec_sign, use_bwp=use_bwp)
            if amount != ZERO:
                any_nonzero = True
            lines_out.append({
                'id':     line['id'],
                'label':  line['label'],
                'amount': str(amount.quantize(Decimal('0.01'))),
            })
            subtotal += amount

        section_results.append({
            'id':              sec['id'],
            'label':           sec['label'],
            'sign':            sec_sign,
            'subtotal_label':  sec['subtotal_label'],
            'subtotal':        str(subtotal.quantize(Decimal('0.01'))),
            'lines':           lines_out,
            'has_activity':    any_nonzero,
        })
        section_totals[sec['id']] = subtotal
        if any_nonzero:
            nonzero_section_ids.append(sec['id'])

    # Totals — each formula resolves section ids to numbers, then
    # applies +/- as written.
    def _eval(formula: str) -> Decimal:
        # Very small parser: tokenise on +/-, look up each token in
        # section_totals OR previously-computed totals.
        tokens = formula.replace('-', ' - ').replace('+', ' + ').split()
        if not tokens:
            return ZERO
        result = ZERO
        op = '+'
        for tok in tokens:
            if tok in ('+', '-'):
                op = tok
                continue
            value = section_totals.get(tok)
            if value is None:
                value = previous_totals.get(tok, ZERO)
            if op == '+':
                result += value
            else:
                result -= value
        return result

    previous_totals: dict[str, Decimal] = {}
    totals_out = []
    for t in template['totals']:
        amount = _eval(t['formula'])
        previous_totals[t['id']] = amount
        totals_out.append({
            'id':      t['id'],
            'label':   t['label'],
            'formula': t['formula'],
            'amount':  str(amount.quantize(Decimal('0.01'))),
        })

    return {
        'entity_code':         company.code,
        'pdf_alias':           template.get('pdf_alias', ''),
        'entity_name':         company.name,
        'report_title':        template.get('report_title', 'Profit & Loss Statement'),
        'currency':            currency,
        'from_date':           from_date.isoformat(),
        'to_date':             to_date.isoformat(),
        'sections':            section_results,
        'totals':              totals_out,
        'nonzero_section_ids': nonzero_section_ids,
    }
