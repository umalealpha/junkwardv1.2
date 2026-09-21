"""
ma_pl.py — Build a P&L report in the exact format of the CFO's
Management Accounts workbook.

Returns sections, line items, subtotals and computed totals (Gross
Profit, EBITDA, EBIT, PBT, PAT) using the layout defined in
`reporting.ma_pl_spec`. Always sums in BWP at JE-locked rates.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date

from django.db.models import Q, Sum

from ledger.models import JournalEntryLine
from .ma_pl_spec import MA_LINES, SECTIONS


ZERO = Decimal('0.00')


def _line_total(codes: list[str], sign: str, qs) -> Decimal:
    """Sum BWP activity for a set of account codes, signed per the line.

    For 'income' lines we return credit - debit (positive = inflow).
    For 'expense' lines we return debit - credit (positive = cost).

    Bug fix 2026-05-19 (Charmaine #1): ma_pl_spec.py uses bare GL codes
    ('100001', '600080'). Non-ADIC entities prefix their CoA codes with
    the company short code (e.g. ADSA_600080). The previous exact-match
    filter (`account__code__in=codes`) therefore returned nothing for
    every non-ADIC entity, which is why ADSA's P&L tiles were all 0.00.
    Match the spec code OR its `<COMPANY>_<code>` variant.
    """
    q = Q()
    for c in codes:
        q |= Q(account__code=c) | Q(account__code__endswith='_' + c)
    agg = qs.filter(q).aggregate(
        d=Sum('debit_bwp'), c=Sum('credit_bwp'),
    )
    d = agg['d'] or ZERO
    c = agg['c'] or ZERO
    if sign == 'income':
        return c - d
    return d - c


def _q(qs, codes, sign):
    return _line_total(codes, sign, qs)


def _ma_shape_from_entity_pl(entity_code: str, from_date: date, to_date: date) -> dict | None:
    """Adapter — return a build_ma_pl-shaped dict for a non-ADIC entity by
    routing through reporting.entity_pl_engine (its 10 per-entity templates).

    The dashboard / CFO dashboard / MA endpoint consumers read these `totals`
    keys: gross_written_premium (= 'Revenue' for trading entities), gross_profit,
    total_other_income, total_operating_expenses, ebitda, depreciation,
    finance_cost, pbt, pat. The insurance-only keys (net_earned_premium,
    net_claim_incurred, net_acquisition, gross_loss_ratio, net_loss_ratio,
    total_provisions, taxation) stay 0.00 — non-insurance entities don't have
    them, and the FE `isTrading` branch already hides those rows.

    Returns None if the entity has no template (caller falls back to the
    legacy insurance-spec path, preserving back-compat for any future entity
    that hasn't yet been templated).
    """
    try:
        from reporting.entity_pl_engine import build_entity_pl
        from reporting.entity_pl_templates import get_template
    except Exception:  # noqa: BLE001
        return None
    if get_template(entity_code) is None:
        return None

    # Sum in BWP so the group dashboard tiles are comparable across entities.
    ep = build_entity_pl(entity_code, from_date, to_date, use_bwp_columns=True)

    sec = {s['id']: Decimal(s.get('subtotal') or '0') for s in ep.get('sections', [])}
    tot = {t['id']: Decimal(t.get('amount') or '0') for t in ep.get('totals', [])}

    revenue   = sec.get('revenue', ZERO) + sec.get('commission_income', ZERO)
    cogs      = sec.get('cogs', ZERO) + sec.get('commission_expenses', ZERO)
    other_inc = sec.get('other_income', ZERO) + sec.get('interest_income', ZERO)
    # opex/finance/depreciation are stored as POSITIVE costs by the engine
    # (sign='expense' → debit-credit). MA convention emits them as the negative
    # impact on profit; mirror that for consumer compatibility.
    opex      = -sec.get('opex', ZERO)
    finance   = -sec.get('finance_costs', ZERO)
    deprec    = -sec.get('depreciation', ZERO)
    # Totals (template formulas): use what the engine computed, fall back to
    # the obvious derivation if a particular total isn't in this template.
    # Operating profit first (template total wins; else derive). cogs is a
    # positive cost magnitude; opex is already stored negative here.
    operating_profit = tot.get('operating_profit', revenue - cogs + other_inc + opex)
    # Gross Profit: template total wins. Else, if the entity has a cost-of-sales
    # layer (e.g. VCM salvage) it's revenue − COGS. If it has NO cost-of-sales
    # layer (e.g. QIH holding co), there is no gross-margin line distinct from the
    # operating result, so Gross Profit IS the operating profit. The old
    # `revenue − cogs` fallback made the Gross Profit line echo Revenue while the
    # real figure sat in EBITDA (Oprah in-app report 2026-06-23).
    has_cogs_layer = ('cogs' in sec) or ('commission_expenses' in sec)
    if 'gross_profit' in tot:
        gross_profit = tot['gross_profit']
    elif has_cogs_layer:
        gross_profit = revenue - cogs
    else:
        gross_profit = operating_profit
    ebitda           = operating_profit  # closest analogue when no D&A split
    ebit             = ebitda + deprec
    pbt              = tot.get('pbt', ebit + finance)
    pat              = pbt  # no separate tax line in entity templates today

    # Two summary "sections" so any consumer that iterates them still works.
    sections = [
        {'id': 'revenue_block', 'label': 'Revenue', 'sign': 'income',
         'subtotal_label': 'Revenue', 'subtotal': str(revenue.quantize(ZERO)),
         'lines': [{'id': 'revenue', 'label': 'Revenue',
                    'amount': str(revenue.quantize(ZERO)), 'sign': 'income'}]},
        {'id': 'opex_block', 'label': 'Operating Expenses', 'sign': 'expense',
         'subtotal_label': 'Operating Expenses',
         'subtotal': str((-opex).quantize(ZERO)),
         'lines': [{'id': 'opex', 'label': 'Operating Expenses',
                    'amount': str((-opex).quantize(ZERO)), 'sign': 'expense'}]},
    ]

    def _s(d): return str(d.quantize(ZERO))

    return {
        'from_date': from_date.isoformat() if hasattr(from_date, 'isoformat') else str(from_date),
        'to_date':   to_date.isoformat()   if hasattr(to_date,   'isoformat') else str(to_date),
        'sections':  sections,
        'totals': {
            'gross_written_premium':     _s(revenue),  # FE Revenue tile reads this
            'net_earned_premium':        _s(ZERO),
            'net_claim_incurred':        _s(ZERO),
            'gross_loss_ratio':          '0',
            'net_loss_ratio':            '0',
            'net_acquisition':           _s(ZERO),
            'gross_profit':              _s(gross_profit),
            'total_other_income':        _s(other_inc),
            'total_operating_expenses':  _s(opex),
            'total_provisions':          _s(ZERO),
            'ebitda':                    _s(ebitda),
            'depreciation':              _s(deprec),
            'ebit':                      _s(ebit),
            'finance_cost':              _s(finance),
            'pbt':                       _s(pbt),
            'taxation':                  _s(ZERO),
            'pat':                       _s(pat),
        },
        'source': 'entity_pl_router',
        'entity_code': entity_code,
    }


def build_ma_pl(
    from_date: date,
    to_date: date,
    company_id: int | None = None,
) -> dict:
    """Return the MA P&L in section-line-subtotal structure.

    Format:
        {
          'from_date': '...', 'to_date': '...',
          'sections': [
            {'id': 'net_earned_premium_block', 'label': 'Net Earned Premium',
             'lines': [{'id': 'gross_written_premium', 'label': 'GWP',
                        'amount': '94277310.39', 'sign': 'income'}, ...],
             'subtotal_label': 'Net Earned Premium',
             'subtotal': '...'},
            ...
          ],
          'totals': {
            'gross_written_premium': '...',     # <-- dashboard 'Revenue' tile reads this
            'net_earned_premium': '...',
            'net_claim_incurred': '...',
            'gross_loss_ratio': '...',
            'net_loss_ratio': '...',
            'net_acquisition': '...',
            'gross_profit': '...',
            'total_other_income': '...',
            'total_operating_expenses': '...',
            'total_provisions': '...',
            'ebitda': '...',
            'depreciation': '...',
            'ebit': '...',
            'finance_cost': '...',
            'pbt': '...',
            'taxation': '...',
            'pat': '...',
          },
        }
    """
    # CFO directive 2026-05-19 → 2026-06-08: for NON-ADIC entities, route to the
    # per-entity P&L template engine (reporting/entity_pl_engine.py) and adapt the
    # output into the MA shape this function's callers expect. The insurance MA
    # spec (NEP / Claims / Acquisition) only applies to ADIC + ADIL; for the 9
    # trading/holding/salvage/software/brokerage/forex entities the dashboard
    # was previously reading totals computed against ADIC GL codes (100001-10),
    # producing 0.00 across the board ("bug recurred"). The /reports/entity-pl
    # page already serves real per-entity P&Ls; this just routes the dashboard's
    # MA P&L call through the same engine for non-ADIC, so /dashboard, the CFO
    # dashboard and any other consumer of build_ma_pl all see real numbers.
    if company_id is not None:
        try:
            from core.models import Company as _Company
            _co = _Company.objects.filter(id=company_id).only('id', 'code').first()
            if _co and _co.code.upper() != 'ADIC':
                _adapted = _ma_shape_from_entity_pl(_co.code, from_date, to_date)
                if _adapted is not None:
                    return _adapted
        except Exception:    # noqa: BLE001 — never let the router break ADIC's path
            pass

    qs = JournalEntryLine.objects.filter(
        journal_entry__entry_date__gte=from_date,
        journal_entry__entry_date__lte=to_date,
        journal_entry__status='posted',
    )
    if company_id is not None:
        qs = qs.filter(journal_entry__company_id=company_id)

    # Build sections with lines and subtotals.
    section_results = []
    line_amounts: dict[str, Decimal] = {}

    for sec in SECTIONS:
        lines_out = []
        subtotal = ZERO
        for line_id in sec['lines']:
            spec = MA_LINES[line_id]
            amount = _q(qs, spec['codes'], spec['sign'])
            line_amounts[line_id] = amount
            lines_out.append({
                'id': line_id,
                'label': spec['label'],
                'amount': str(amount),
                'sign': spec['sign'],
            })
            subtotal += amount if spec['sign'] == 'income' else -amount

        section_results.append({
            'id': sec['id'],
            'label': sec['label'],
            'lines': lines_out,
            'subtotal_label': sec['subtotal_label'],
            'subtotal': str(subtotal),
        })

    # Direct line lookups (used in totals)
    gwp = line_amounts.get('gross_written_premium', ZERO)
    ceded = line_amounts.get('premiums_ceded', ZERO)
    upr = line_amounts.get('change_in_upr', ZERO)
    gross_claims = line_amounts.get('gross_claims', ZERO)
    ri_recovered = line_amounts.get('ri_claims_recovered', ZERO)
    subrog = line_amounts.get('subrogations_salvages', ZERO)

    # Subtotals (mirrors MA workbook formulas)
    net_earned_premium = gwp - ceded + upr   # income sign on UPR already applied
    net_claim_incurred = -gross_claims + ri_recovered + subrog

    # Loss ratios are always presented as POSITIVE percentages in the MA
    # workbook. `_line_total('expense')` returns claims as a positive cost, so
    # the raw arithmetic gave a negative ratio (e.g. -40.6%) — wrap in abs()
    # so the dashboard matches the MA pack. Fix from RECON swarm 2026-06-08 #1.
    # Presentation-only — no underlying P&L value changes.
    gross_loss_ratio = (
        abs(gross_claims / gwp) if gwp != ZERO else ZERO
    )
    net_loss_ratio = (
        abs(net_claim_incurred / net_earned_premium)
        if net_earned_premium != ZERO else ZERO
    )

    # Acquisition net (signs already applied per spec)
    acq_section = next(s for s in section_results if s['id'] == 'acquisition')
    net_acquisition = Decimal(acq_section['subtotal'])

    gross_profit = net_earned_premium + net_claim_incurred + net_acquisition

    other_income_section = next(s for s in section_results if s['id'] == 'other_income_block')
    total_other_income = Decimal(other_income_section['subtotal'])

    opex_section = next(s for s in section_results if s['id'] == 'operating_expenses')
    total_opex = Decimal(opex_section['subtotal'])

    prov_section = next(s for s in section_results if s['id'] == 'provisions')
    total_provisions = Decimal(prov_section['subtotal'])

    # CFO audit 2026-05-17: Provisions was being ADDED to EBITDA instead
    # of subtracted when the section subtotal happened to net positive
    # (e.g. period release > period addition). Same defensive treatment
    # for Operating Expenses. Both are costs from an EBITDA standpoint
    # regardless of how the period activity nets — we ALWAYS subtract
    # their magnitude. If the natural subtotal is already negative
    # (typical period of net addition), abs() flips it to positive and
    # we subtract; if it's positive (atypical release period), we still
    # subtract its magnitude.
    opex_cost = -abs(total_opex)
    provisions_cost = -abs(total_provisions)
    ebitda = gross_profit + total_other_income + opex_cost + provisions_cost

    # Below-the-line items not in any section
    depreciation = -_q(qs, MA_LINES['depreciation']['codes'], 'expense')
    ebit = ebitda + depreciation

    finance_cost = -_q(qs, MA_LINES['finance_cost']['codes'], 'expense')
    pbt = ebit + finance_cost

    taxation = -_q(qs, MA_LINES['taxation']['codes'], 'expense')
    pat = pbt + taxation

    return {
        'from_date': str(from_date),
        'to_date': str(to_date),
        'sections': section_results,
        'totals': {
            'gross_written_premium': str(gwp),
            'premiums_ceded': str(ceded),
            'change_in_upr': str(upr),
            'net_earned_premium': str(net_earned_premium),
            'gross_claims': str(gross_claims),
            'ri_claims_recovered': str(ri_recovered),
            'subrogations_salvages': str(subrog),
            'net_claim_incurred': str(net_claim_incurred),
            'gross_loss_ratio': str(gross_loss_ratio.quantize(Decimal('0.0001'))),
            'net_loss_ratio': str(net_loss_ratio.quantize(Decimal('0.0001'))),
            'net_acquisition': str(net_acquisition),
            'gross_profit': str(gross_profit),
            'total_other_income': str(total_other_income),
            # Opex + Provisions are emitted as negative magnitudes (cost
            # convention) so the dashboard renders them in parens.
            # `total_provisions` was previously the raw section subtotal
            # which could be positive when releases > additions; that
            # made the dashboard show "BWP 5.4M" without parens (looking
            # like income) even though the EBITDA math correctly subtracted
            # it. Fix: always emit -abs(...). The signed value used for
            # the EBITDA chain below is `opex_cost` / `provisions_cost`.
            'total_operating_expenses': str(opex_cost),
            'total_provisions': str(provisions_cost),
            'ebitda': str(ebitda),
            'depreciation': str(depreciation),
            'ebit': str(ebit),
            'finance_cost': str(finance_cost),
            'pbt': str(pbt),
            'taxation': str(taxation),
            'pat': str(pat),
        },
    }
