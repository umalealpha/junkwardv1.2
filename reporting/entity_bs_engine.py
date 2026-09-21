"""
reporting/entity_bs_engine.py

Generic, template-driven Balance Sheet engine for the non-ADIC group entities.

Mirrors reporting/entity_pl_engine.py exactly. Reads
`reporting.entity_bs_templates.TEMPLATES`. ADIC + ADIL are OUT OF SCOPE
— they continue to use `reporting/ma_bs_spec.py` via `build_ma_balance_sheet`.

Returns the SAME shape as `build_ma_balance_sheet`:

  {
    'as_of':    'YYYY-MM-DD',
    'sections': [
      {id, label, side, lines:[{label, amount}], subtotal_label, subtotal},
      ...
    ],
    'totals': {
      'total_assets', 'total_liabilities', 'total_equity',
      'liabilities_and_equity', 'balanced',
    },
    'currency_code': 'BWP' | 'ZAR' | 'USD' | 'ZMW' | 'INR',
    'entity_code':   'QIH',
    'source':        'entity_bs_router',
  }

Sign convention (matches MA BS display + the BS dashboard card):
  asset      lines emit POSITIVE = net Dr (normal balance)
  liability  lines emit POSITIVE = net Cr (amount owed)
  equity     lines emit POSITIVE = net Cr (capital + reserves)
  contra     lines emit POSITIVE then SUBTRACTED from the subtotal
             (e.g. Accumulated Depreciation reduces PPE net)

CFO directive 2026-06-09 (Legakwa Ntabeni BS reconfig — approved same day).
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date
from typing import Iterable

from django.db.models import Q, Sum

from core.models import Company
from ledger.models import JournalEntryLine

from .entity_bs_templates import get_template


ZERO = Decimal('0.00')


def _match_q(keywords: Iterable[str], exclude: Iterable[str]) -> Q:
    """OR-of-icontains over Account.name, minus excludes. Same as entity_pl_engine."""
    inc = Q()
    for kw in keywords:
        inc |= Q(account__name__icontains=kw)
    if not exclude:
        return inc
    exc = Q()
    for kw in exclude:
        exc |= Q(account__name__icontains=kw)
    return inc & ~exc


def _line_balance(qs, side: str) -> Decimal:
    """Sum BWP activity for a filtered queryset, signed per the section side.

    Always uses BWP columns — the dashboard sums across the group in BWP and
    the per-entity reports use the same shape (currency label is purely
    presentational).

      asset      → debit-credit (positive = net debit = the asset's natural balance)
      liability  → credit-debit (positive = net credit = amount owed)
      equity     → credit-debit (positive = net credit = capital + reserves)
    """
    agg = qs.aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp'))
    d = agg['d'] or ZERO
    c = agg['c'] or ZERO
    return (d - c) if side == 'asset' else (c - d)


def build_entity_bs(entity_code: str, as_of: date) -> dict:
    """Build per-entity BS for one company, as at as_of (inclusive)."""
    template = get_template(entity_code)
    if template is None:
        raise ValueError(
            f"No BS template for entity '{entity_code}'. "
            f"ADIC/ADIL use the MA BS spec — call build_ma_balance_sheet."
        )

    company = (Company.objects.filter(code=template['entity_code']).first()
               or Company.objects.filter(code__iexact=template['entity_code']).first())
    if company is None:
        raise ValueError(f"Company {template['entity_code']} not found in DB.")

    currency = template.get('currency') or (
        company.base_currency.code if company.base_currency_id else 'BWP'
    )

    # All posted JE lines for this company up to as_of (inclusive).
    base = JournalEntryLine.objects.filter(
        journal_entry__company_id=company.id,
        journal_entry__entry_date__lte=as_of,
        journal_entry__status='posted',
    ).select_related('account')

    section_results = []
    total_assets = ZERO
    total_liab   = ZERO
    total_equity = ZERO

    # Track which accounts were already matched to a template line so the
    # residual plug only catches what's *missing* — not double-counting an
    # account that already showed up in a section line.
    matched_ids: set = set()

    for sec in template['sections']:
        side = sec['side']
        sub = ZERO
        out_lines = []
        for line in sec['lines']:
            qs = base.filter(_match_q(line['keywords'], line.get('exclude') or []))
            # Excluding accounts we already matched in a higher-priority earlier
            # line/section in the same template prevents one account from
            # contributing to two lines (e.g. "Receivable from Alpha Direct" must
            # not feed both Trade Receivables and Related Party Receivables).
            qs = qs.exclude(account_id__in=matched_ids)
            ids_here = list(qs.values_list('account_id', flat=True).distinct())
            matched_ids.update(ids_here)
            amt = _line_balance(qs, side)
            # No contra special-case — Accumulated Depreciation is credit-natural,
            # so on the asset side its Dr-Cr is naturally negative and reduces
            # PPE when summed. Same for Acc Provision-for-Doubtful-Debts etc.
            sub += amt
            out_lines.append({
                'label':  line['label'],
                'amount': str(amt.quantize(Decimal('0.01'))),
            })

        section_results.append({
            'id':              sec['id'],
            'label':           sec['label'],
            'side':            side,
            'lines':           out_lines,
            'subtotal_label':  sec['subtotal_label'],
            'subtotal':        str(sub.quantize(Decimal('0.01'))),
        })

        if side == 'asset':
            total_assets += sub
        elif side == 'liability':
            total_liab += sub
        elif side == 'equity':
            total_equity += sub

    # ── Residual plug — bring each section's subtotal up to its true
    #    account_type total. Catches anything not matched by a template line.
    #    Run BEFORE adding the synthetic Current-Year-P&L line so the math
    #    composes cleanly (TA must equal type_net['asset'] after this step).
    from django.db.models import Sum as _S
    type_net = {'asset': ZERO, 'liability': ZERO, 'equity': ZERO}
    for row in base.exclude(
        account__account_type__in=('revenue', 'expense'),
    ).values('account__account_type').annotate(
        d=_S('debit_bwp'), c=_S('credit_bwp'),
    ):
        d = row['d'] or ZERO
        c = row['c'] or ZERO
        at = (row['account__account_type'] or '').lower()
        if at.startswith('asset'):
            type_net['asset'] += (d - c)
        elif at.startswith('liability'):
            type_net['liability'] += (c - d)
        elif at.startswith('equity'):
            type_net['equity'] += (c - d)

    for s in section_results:
        if s['id'] == 'current_assets':
            r = type_net['asset'] - total_assets
            if abs(r) > Decimal('0.01'):
                s['lines'].append({'label': 'Other Assets (unclassified)',
                                   'amount': str(r.quantize(Decimal('0.01')))})
                new_sub = Decimal(s['subtotal']) + r
                s['subtotal'] = str(new_sub.quantize(Decimal('0.01')))
                total_assets += r
        elif s['id'] == 'current_liabilities':
            r = type_net['liability'] - total_liab
            if abs(r) > Decimal('0.01'):
                s['lines'].append({'label': 'Other Liabilities (unclassified)',
                                   'amount': str(r.quantize(Decimal('0.01')))})
                new_sub = Decimal(s['subtotal']) + r
                s['subtotal'] = str(new_sub.quantize(Decimal('0.01')))
                total_liab += r
        elif s['id'] == 'equity':
            r = type_net['equity'] - total_equity
            if abs(r) > Decimal('0.01'):
                s['lines'].append({'label': 'Other Equity (prior periods)',
                                   'amount': str(r.quantize(Decimal('0.01')))})
                new_sub = Decimal(s['subtotal']) + r
                s['subtotal'] = str(new_sub.quantize(Decimal('0.01')))
                total_equity += r

    # ── Current-year P&L plug for Equity. The balance identity is:
    #    sum(asset Dr-Cr) = sum(liab+equity Cr-Dr) + pl_net
    #    so total_equity MUST include pl_net for TA = TL+E to hold. Add it
    #    AFTER the residual so the residual doesn't subtract it back out.
    pl_net = ZERO
    for row in base.filter(
        account__account_type__in=('revenue', 'expense'),
    ).values('account__account_type').annotate(
        d=_S('debit_bwp'), c=_S('credit_bwp'),
    ):
        d = row['d'] or ZERO
        c = row['c'] or ZERO
        if row['account__account_type'] == 'revenue':
            pl_net += (c - d)   # revenue increases profit
        else:
            pl_net -= (d - c)   # expense decreases profit
    if pl_net != ZERO:
        for s in section_results:
            if s['id'] == 'equity':
                s['lines'].append({
                    'label':  'Current Year Profit / (Loss)',
                    'amount': str(pl_net.quantize(Decimal('0.01'))),
                })
                new_sub = Decimal(s['subtotal']) + pl_net
                s['subtotal'] = str(new_sub.quantize(Decimal('0.01')))
                total_equity += pl_net
                break

    liab_and_eq = total_liab + total_equity
    balanced = abs(total_assets - liab_and_eq) < Decimal('0.01')

    return {
        'as_of':         as_of.isoformat() if hasattr(as_of, 'isoformat') else str(as_of),
        'sections':      section_results,
        'totals': {
            'total_assets':           str(total_assets.quantize(Decimal('0.01'))),
            'total_liabilities':      str(total_liab.quantize(Decimal('0.01'))),
            'total_equity':           str(total_equity.quantize(Decimal('0.01'))),
            'liabilities_and_equity': str(liab_and_eq.quantize(Decimal('0.01'))),
            'balanced':               balanced,
        },
        'currency_code': currency,
        'entity_code':   company.code,
        'source':        'entity_bs_router',
    }
