"""
payroll/paye.py

Pure functions — given an annual taxable income and an active set of
TaxBracket rows, returns the tax due. Never hard-codes BURS rates: the
brackets are stored in the database and editable by the CFO.

If no active brackets are seeded, calculate_paye() returns 0.00 and the
caller can decide whether to surface that as a warning (it should — a
zero-tax payroll usually means someone forgot to set up the bands).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal('0.00')
TWO_PLACES = Decimal('0.01')

# PAYE rounds HALF UP — a TAX decision, never a language default (CFO
# 2026-08-09, same rule as VAT). Every quantize below passed no `rounding=`,
# so Python's ROUND_HALF_EVEN applied and nothing in the repo sets a decimal
# context: an exact half-thebe always landed DOWN. Proven on the live BURS
# bands — monthly 7,000.04 charged 150.00 instead of 150.01; 11,000.24 charged
# 712.54 instead of 712.55. One thebe, every time, always under-collected.
# payroll/loan_service._q already did it this way.
ONE_HUNDRED = Decimal('100')


def calculate_annual_paye(annual_taxable_income, brackets) -> Decimal:
    """
    *brackets* is an iterable of (lower_bound, upper_bound, base_amount, rate_pct)
    tuples sorted ascending by lower_bound. The standard Botswana progressive
    schedule: pick the bracket where lower_bound < income <= upper_bound, then
        tax = base_amount + rate_pct% × (income − lower_bound)
    """
    income = Decimal(annual_taxable_income or 0)
    if income <= ZERO:
        return ZERO

    chosen = None
    for lo, hi, base, rate in brackets:
        lo = Decimal(lo or 0)
        hi_val = Decimal(hi) if hi is not None else None
        if income > lo and (hi_val is None or income <= hi_val):
            chosen = (lo, base or ZERO, rate or ZERO)
            break
    if chosen is None:
        return ZERO

    lo, base, rate = chosen
    return (Decimal(base) + (Decimal(rate) / ONE_HUNDRED) * (income - Decimal(lo))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def calculate_monthly_paye(monthly_taxable_income, brackets) -> Decimal:
    """Annualise the monthly figure, compute, then divide by 12."""
    annual = Decimal(monthly_taxable_income or 0) * Decimal('12')
    annual_tax = calculate_annual_paye(annual, brackets)
    return (annual_tax / Decimal('12')).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def active_brackets_from_db():
    """Pull the current ACTIVE TaxBracket rows from the database."""
    from .models import TaxBracket
    return list(
        TaxBracket.objects.filter(is_active=True)
        .order_by('lower_bound')
        .values_list('lower_bound', 'upper_bound', 'base_amount', 'rate_pct')
    )


def monthly_taxable_gross(payslip) -> Decimal:
    """The monthly PAYE base carried by *payslip*: taxable earnings less pre-tax
    (tax-deductible) contributions.

    Same rule as ``Payslip.recompute_totals`` — EARNING adds to the base,
    EARNING_NON_TAXABLE does not, EMPLOYEE_PRETAX reduces it by its MAGNITUDE
    (import stores deductions negative — the 2026-07-23 sign bug), and
    EMPLOYEE_DEDUCTION / COMPANY_CONTRIBUTION / TAX lines never touch it.

    It is a separate function rather than a call into ``recompute_totals``
    because that method has an early-return branch for source-currency payslips
    (which never computes a taxable base) and applies the housing sacrifice as a
    side effect. ``payroll/tests/test_taxable_gross.py`` asserts this function
    stays in step with ``recompute_totals``.
    """
    from .models import PayslipComponent

    base = ZERO
    for ln in payslip.lines.select_related('component').all():
        kind = ln.component.kind
        amt = ln.amount or ZERO
        if kind == PayslipComponent.Kind.EARNING:
            base += amt
        elif kind == PayslipComponent.Kind.EMPLOYEE_PRETAX:
            base -= abs(amt)
    return base.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def marginal_tax_on_extra(monthly_taxable_base, extra_amount, brackets) -> Decimal:
    """PAYE on a ONE-OFF taxable payment of *extra_amount* made to an employee
    whose regular monthly taxable pay is *monthly_taxable_base*.

        annual_base = monthly_taxable_base × 12
        tax         = PAYE(annual_base + extra) − PAYE(annual_base)

    The extra is added to ANNUAL income ONCE — it is deliberately NOT annualised.
    Annualising it (i.e. `calculate_monthly_paye(base + extra)`) treats a single
    payment as if it recurred every month and pushes the employee into bands they
    never reach, over-deducting badly at the low end: a P3,000/month employee
    (P36,000/year, below the P48,000 threshold, PAYE nil) would be charged tax on
    a P1,500 cash-out purely because 12 × P1,500 crosses the threshold.

    So the charge lands in the employee's OWN marginal band(s) and splits
    correctly across a band boundary — a junior on P8,000/month pays their
    12.5 % rate, not the top 27.5 %. That is the CFO's rule (17-Aug-2026): each
    person's own rate, never one flat rate for everybody.

    Never negative (a bracket table whose rate falls as income rises would
    otherwise credit tax); clamped at zero.
    """
    base = Decimal(monthly_taxable_base or 0)
    extra = Decimal(extra_amount or 0)
    if extra <= ZERO:
        return ZERO
    if base < ZERO:
        base = ZERO
    annual_base = base * Decimal('12')
    delta = (calculate_annual_paye(annual_base + extra, brackets)
             - calculate_annual_paye(annual_base, brackets))
    if delta < ZERO:
        return ZERO
    return delta.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
