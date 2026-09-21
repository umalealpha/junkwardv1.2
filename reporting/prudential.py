"""
reporting/prudential.py — NBFIRA Prudential Limits computation.

Insurance Industry Regulations 2019 (S.I. 57 of 2019), Regulation 7 →
SCHEDULE 1 (general insurers — Alpha Direct is a general insurer, so
Schedule 1 applies, NOT Schedule 2's long-term figures). Six headline
measures the insurer stays within:
  1. Single non-Govt issuer (proxy)          ≤ 25 % bank deposits/inst (item 8.2)
  2. Shares — aggregate holding              ≤ 30 % of total investments (item 8.7)
  3. Foreign-currency investments (proxy)    ≤ 20 % — measures ALL non-BWP holdings,
                                             a conservative proxy for the 8.5 foreign-
                                             bond caps (total FX ≥ any bond subset)
  4. Immovable property — aggregate          ≤ 10 % of total investments (item 8.6)
  5. Liquidity cover (cash + near-cash)      ≥ 100 % of 12-mo gross claims (internal)
  6. Related-party loans/debentures          ≤ 5 % of total investments (item 8.8)

NOTE: single-issuer sub-limits (bonds 5 %, shares 2.5–5 %) and the immovable
5 % single-property sub-limit need per-instrument classification on Investment
rows — Tier-2 follow-up; today's engine measures the aggregate only.

Wires the existing Investments + Banking + Reports modules together so the
/compliance/nbfira/prudential page renders live ratios per company + period.

Source measurements (omni state, 2026-05-24):
  • Single counterparty   — derived from Investment.issuer aggregation,
    excluding instrument_type IN (treasury_bill, govt_bond)
  • Listed equities       — Investment.instrument_type == 'equity'
  • Foreign currency      — Investment.currency_code != 'BWP'
  • Direct property       — instrument_type == 'other' AND name LIKE '%property%'
                            (proxy until a dedicated is_property flag ships)
  • Liquidity cover       — bank balance + investments maturing ≤ 12 months
                            / 12-month projected gross claims
                            (projected = MA P&L gross claims annualised)
  • Related party         — Investment.issuer in company.related_party_issuers
                            (config list — empty for now → returns 0)
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional

from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal('0.00')

# Government issuers excluded from the single-counterparty 25% rule.
_GOVT_INSTRUMENT_TYPES = {'treasury_bill', 'govt_bond'}

# Listed-equity proxy. The Investment model has no is_listed flag yet so we
# assume any row tagged instrument_type='equity' is a listed equity. Private
# equity holdings should be tagged 'other' (the prudential rule wraps non-
# listed equities under a separate concentration test outside §5(2)(b)).
_LISTED_EQUITY_TYPES = {'equity'}


def _q(d) -> Decimal:
    """Coerce anything to a Decimal, zeroing None/empty strings."""
    if d is None or d == '':
        return ZERO
    if isinstance(d, Decimal):
        return d
    return Decimal(str(d))


def compute_prudential(
    *,
    company_id: Optional[str] = None,
    as_of: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """Return a dict shaped for the /compliance/nbfira/prudential page.

    Output:
      {
        'as_of':            '2026-03-31',
        'company':          {'id': '...', 'code': 'ADIC'},
        'total_portfolio':  '125000000.00',
        'measurements': {
            'single_counterparty':  {'amount': ..., 'ratio': 0.18, 'counterparty': 'Stanbic', 'status': 'ok'},
            'equities_total':       {'amount': ..., 'ratio': 0.22, 'status': 'ok'},
            'foreign_assets':       {'amount': ..., 'ratio': 0.05, 'status': 'ok'},
            'property':             {'amount': ..., 'ratio': 0.00, 'status': 'ok'},
            'liquidity':            {'cash_near_cash': ..., 'projected_claims_12m': ..., 'ratio': 1.45, 'status': 'ok'},
            'related_party':        {'amount': 0,    'ratio': 0.00, 'status': 'unknown',
                                     'note': 'No is_related_party flag — populate config list.'},
        },
        'warnings': [...],
      }

    Status traffic light:
      • ratio <= threshold * 0.8  -> ok       (green)
      • ratio <= threshold        -> warning  (amber)
      • ratio  > threshold        -> breach   (red)
      Liquidity flips the direction (>= threshold = ok).
    """
    from investments.models import Investment
    from ledger.models import Account
    from core.models import Company

    as_of = as_of or timezone.localdate()
    company = None
    if company_id:
        company = Company.objects.filter(pk=company_id).first()

    inv_qs = Investment.objects.filter(status='open')
    # Investment model has no company FK today — assume single legal entity
    # for now. Future: when company FK added, filter here.

    total_portfolio = ZERO
    by_issuer = defaultdict(lambda: ZERO)
    equities = ZERO
    foreign = ZERO
    property_holdings = ZERO
    maturing_12m = ZERO
    related_party = ZERO

    twelve_months_out = as_of + datetime.timedelta(days=365)

    for inv in inv_qs.select_related('currency_code'):
        amt = _q(inv.current_fair_value) or _q(inv.cost)
        total_portfolio += amt

        # Single counterparty — exclude government instruments
        if inv.instrument_type not in _GOVT_INSTRUMENT_TYPES:
            by_issuer[inv.issuer] += amt

        # Listed equities
        if inv.instrument_type in _LISTED_EQUITY_TYPES:
            equities += amt

        # Foreign currency
        if (inv.currency_code_id or '').upper() not in ('BWP', ''):
            foreign += amt

        # Direct property — proxy by instrument_type=other AND name match
        if inv.instrument_type == 'other':
            name_l = (inv.name or '').lower()
            if 'property' in name_l or 'land' in name_l or 'building' in name_l:
                property_holdings += amt

        # Liquidity — cash equivalents = T-bills + FD + investments maturing ≤ 12m
        if inv.instrument_type in ('treasury_bill', 'fixed_deposit'):
            maturing_12m += amt
        elif inv.maturity_date and inv.maturity_date <= twelve_months_out:
            maturing_12m += amt

    # Top single-counterparty exposure
    top_counterparty = None
    top_counterparty_amt = ZERO
    for issuer, amt in by_issuer.items():
        if amt > top_counterparty_amt:
            top_counterparty_amt = amt
            top_counterparty = issuer

    # Cash from bank accounts (Account.is_bank_account=True)
    bank_balance = ZERO
    bank_filter = {'is_bank_account': True, 'is_active': True}
    if company_id:
        bank_filter['owner_company_id'] = company_id
    bank_accounts = Account.objects.filter(**bank_filter)
    if bank_accounts.exists():
        # Sum from latest TB instead of looping JE lines for perf.
        from ledger.models import JournalEntryLine, JournalEntry
        agg = (JournalEntryLine.objects
               .filter(account__in=bank_accounts,
                       journal_entry__status=JournalEntry.Status.POSTED,
                       journal_entry__entry_date__lte=as_of)
               .aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp')))
        bank_balance = _q(agg.get('d')) - _q(agg.get('c'))

    cash_and_near_cash = bank_balance + maturing_12m

    # Projected 12-month gross claims — pull from MA P&L for last 12 months
    # and annualise. Conservative fallback: 50% of total premium last 12m.
    projected_claims_12m = _projected_claims_12m(company, as_of)

    # Ratios
    def _ratio(num, den):
        if not den or den == ZERO:
            return None
        return float(num / den)

    def _status(ratio, threshold, *, gte=False):
        if ratio is None:
            return 'unknown'
        ratio = Decimal(str(ratio))
        if gte:
            return 'ok' if ratio >= threshold else 'breach'
        if ratio <= threshold * Decimal('0.8'):
            return 'ok'
        if ratio <= threshold:
            return 'warning'
        return 'breach'

    measurements: Dict[str, Any] = {
        'single_counterparty': {
            'amount': str(top_counterparty_amt),
            'counterparty': top_counterparty or '',
            'ratio': _ratio(top_counterparty_amt, total_portfolio),
            'status': _status(
                _ratio(top_counterparty_amt, total_portfolio),
                Decimal('0.25'),
            ),
        },
        'equities_total': {
            'amount': str(equities),
            'ratio': _ratio(equities, total_portfolio),
            'status': _status(
                _ratio(equities, total_portfolio),
                Decimal('0.30'),
            ),
        },
        'foreign_assets': {
            'amount': str(foreign),
            'ratio': _ratio(foreign, total_portfolio),
            'status': _status(
                _ratio(foreign, total_portfolio),
                Decimal('0.20'),
            ),
        },
        'property': {
            'amount': str(property_holdings),
            'ratio': _ratio(property_holdings, total_portfolio),
            # Schedule 1 item 8.6: 10% aggregate for GENERAL insurers (the 25%
            # previously used is the long-term-insurer figure, item 9.6).
            'status': _status(
                _ratio(property_holdings, total_portfolio),
                Decimal('0.10'),
            ),
        },
        'liquidity': {
            'cash_near_cash': str(cash_and_near_cash),
            'bank_balance': str(bank_balance),
            'maturing_12m': str(maturing_12m),
            'projected_claims_12m': str(projected_claims_12m),
            'ratio': _ratio(cash_and_near_cash, projected_claims_12m),
            'status': _status(
                _ratio(cash_and_near_cash, projected_claims_12m),
                Decimal('1.00'),
                gte=True,
            ),
        },
        'related_party': {
            'amount': str(related_party),
            'ratio': _ratio(related_party, total_portfolio),
            'status': 'unknown',
            'note': ('Related-party loans/debentures ≤ 5% (Schedule 1 item 8.8). '
                     'Investment.is_related_party flag pending — returns 0, which '
                     'is NOT confirmed nil, until the field ships (Tier-2).'),
        },
    }

    warnings: List[str] = []
    if total_portfolio == ZERO:
        warnings.append(
            'No open investments found. Total-portfolio denominator is zero so '
            'concentration ratios cannot be computed. Add Investment rows or '
            'check the Investments → status filter.'
        )
    if projected_claims_12m == ZERO:
        warnings.append(
            'Projected 12-month gross claims could not be derived from MA P&L. '
            'Liquidity ratio computed without a denominator.'
        )

    return {
        'as_of': as_of.isoformat(),
        'company': ({'id': str(company.id), 'code': company.code, 'name': company.name}
                    if company else None),
        'total_portfolio': str(total_portfolio),
        'measurements': measurements,
        'warnings': warnings,
    }


def _projected_claims_12m(company, as_of) -> Decimal:
    """Annualised projected gross claims over the next 12 months.

    Pulls the most recent 12-month rolling gross-claims figure from posted
    JEs against MA P&L claims accounts (codes 103xxx — Claims). Conservative
    fallback: 50% of last-12-month gross written premium (industry average
    loss ratio for Botswana motor business).
    """
    from ledger.models import JournalEntry, JournalEntryLine, Account
    twelve_mo_ago = as_of - datetime.timedelta(days=365)

    claim_codes = Account.objects.filter(
        code__startswith='103', is_active=True,
    ).values_list('id', flat=True)
    if claim_codes:
        agg = (JournalEntryLine.objects
               .filter(account_id__in=list(claim_codes),
                       journal_entry__status=JournalEntry.Status.POSTED,
                       journal_entry__entry_date__gte=twelve_mo_ago,
                       journal_entry__entry_date__lte=as_of)
               .aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp')))
        claims = _q(agg.get('d')) - _q(agg.get('c'))
        if claims > ZERO:
            return claims

    # Fallback: 50% of last-12-month GWP
    premium_codes = Account.objects.filter(
        code__startswith='4', account_type='revenue', is_active=True,
    ).values_list('id', flat=True)
    if premium_codes:
        agg = (JournalEntryLine.objects
               .filter(account_id__in=list(premium_codes),
                       journal_entry__status=JournalEntry.Status.POSTED,
                       journal_entry__entry_date__gte=twelve_mo_ago,
                       journal_entry__entry_date__lte=as_of)
               .aggregate(d=Sum('debit_bwp'), c=Sum('credit_bwp')))
        # Revenue is credit-natural — credits minus debits = GWP
        gwp = _q(agg.get('c')) - _q(agg.get('d'))
        if gwp > ZERO:
            return gwp * Decimal('0.5')

    return ZERO
