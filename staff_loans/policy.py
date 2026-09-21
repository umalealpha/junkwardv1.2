"""
staff_loans/policy.py

The Staff Loan scheme rules, as one place. CFO directive 2026-07-15.

Two products
------------
1. **Staff loan** — a short cash loan. Capped at one month's salary, repaid
   within four months.
2. **Vehicle loan** — up to P40,000; the vehicle's blue book (registration
   book) is held in Alpha Direct's or Veritas's name until the loan is cleared.

Interest
--------
Flat (add-on) interest, so the employee sees exactly what they repay:

    interest        = principal x annual_rate x (term_months / 12)
    total_repayable = principal + interest
    monthly         = total_repayable / term_months

Default rate is 15.5% p.a. — the midpoint of the current Botswana bank
personal-loan range the CFO gave (~15-16%). Bank of Botswana prime was ~7.19%
(Feb 2026) and FNB unsecured personal rates sit well above that, so 15.5% is a
fair staff-scheme rate. The final approver (CFO) may set a different rate on
any individual loan; this is only the default the form pre-fills.

Interest is recognised in the GL when the loan is issued (it is small on a
≤4-month staff loan). The receivable is booked at the full amount repayable, so
each monthly deduction simply reduces that one balance to zero — which is what
"the staff loan balance decreases each month" means in the ledger.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal('0.00')
TWO_PLACES = Decimal('0.01')

# --- Scheme parameters (CFO 2026-07-15) --------------------------------------
DEFAULT_ANNUAL_RATE_PCT = Decimal('15.5')   # fallback if no rate row exists yet
DEFAULT_SPREAD_PCT = Decimal('10.0')         # margin over the BoB MoPR (5.5 + 10 = 15.5 now)
MAX_ANNUAL_RATE_PCT = Decimal('30')          # sanity ceiling — catches a 155-for-15.5 fat-finger
MIN_TERM_MONTHS = 1
STAFF_MAX_TERM_MONTHS = 6                    # CFO 2026-07-15: repayable within six months
MIN_MOTIVATION_WORDS = 50                    # applicants must properly motivate the request
VEHICLE_MAX_AMOUNT = Decimal('40000.00')     # "up to a maximum of 40,000 BWP"
VEHICLE_MAX_TERM_MONTHS = 24                  # not set by CFO — sensible ceiling

STAFF = 'staff'
VEHICLE = 'vehicle'

BLUE_BOOK_ALPHA = 'alpha_direct'
BLUE_BOOK_VERITAS = 'veritas'
BLUE_BOOK_HOLDERS = (
    (BLUE_BOOK_ALPHA, 'Alpha Direct'),
    (BLUE_BOOK_VERITAS, 'Veritas'),
)


def q(amount) -> Decimal:
    """Round to 2 dp, half-up (BWP convention)."""
    return Decimal(amount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def flat_interest(principal, annual_rate_pct, term_months) -> Decimal:
    p = Decimal(principal or 0)
    r = Decimal(annual_rate_pct or 0) / Decimal('100')
    n = Decimal(term_months or 0)
    if p <= 0 or r < 0 or n <= 0:
        return ZERO
    return q(p * r * (n / Decimal('12')))


def total_repayable(principal, annual_rate_pct, term_months) -> Decimal:
    return q(Decimal(principal or 0) + flat_interest(principal, annual_rate_pct, term_months))


def monthly_instalment(principal, annual_rate_pct, term_months) -> Decimal:
    n = int(term_months or 0)
    if n <= 0:
        return ZERO
    return q(total_repayable(principal, annual_rate_pct, term_months) / Decimal(n))


def validate_staff_loan(amount, term_months, monthly_salary) -> dict:
    """Return a dict of field -> error message. Empty dict = valid."""
    errs: dict[str, str] = {}
    amount = Decimal(amount or 0)
    term = int(term_months or 0)
    if amount <= 0:
        errs['amount_requested'] = 'Enter an amount greater than zero.'
    if monthly_salary is None or Decimal(monthly_salary) <= 0:
        errs.setdefault(
            'amount_requested',
            'We could not find your monthly salary — we look at your latest '
            'payslip and your active contract, and neither is on file yet. HR '
            'needs to load your pay details first.',
        )
    elif amount > Decimal(monthly_salary):
        errs['amount_requested'] = (
            f'A staff loan cannot be more than one month salary '
            f'(P{q(monthly_salary):,}).'
        )
    if not (MIN_TERM_MONTHS <= term <= STAFF_MAX_TERM_MONTHS):
        errs['term_months_requested'] = (
            f'A staff loan must be repaid within {STAFF_MAX_TERM_MONTHS} months '
            f'(1 to {STAFF_MAX_TERM_MONTHS}).'
        )
    return errs


# NOTE: interest is shown to the applicant up front (interest / total / monthly)
# and, on disbursement, the interest portion is credited to Interest income
# automatically (see services._post_issuance_je). CFO 2026-07-15.


def word_count(text) -> int:
    return len((text or '').split())


def validate_motivation(reason) -> dict:
    """Both loan types need a real motivation — at least 50 words on why the
    loan is needed. A staff loan is a favour, not an entitlement."""
    errs: dict[str, str] = {}
    n = word_count(reason)
    if n < MIN_MOTIVATION_WORDS:
        errs['reason'] = (
            f'Please motivate the request in your own words — at least '
            f'{MIN_MOTIVATION_WORDS} words (you have {n}). Explain why you need the loan.'
        )
    return errs


def validate_declarations(*, no_other_loans, loan_type, purchased_via_veritas) -> dict:
    """Shared attestations. Both loans: no other bank/FI loans. Vehicle loans:
    the vehicle must have been bought through the Veritas salvage yard."""
    errs: dict[str, str] = {}
    if not no_other_loans:
        errs['no_other_loans_declared'] = (
            'You must confirm you have no other loans with any bank or financial institution.'
        )
    if loan_type == VEHICLE and not purchased_via_veritas:
        errs['purchased_via_veritas'] = (
            'Vehicle loans are only for vehicles purchased through the Veritas salvage yard. '
            'Confirm this vehicle was bought there.'
        )
    return errs


def validate_rate(annual_rate_pct) -> dict:
    """Rate sanity — a real ceiling so a mistyped rate can't create a giant loan."""
    errs: dict[str, str] = {}
    if annual_rate_pct is None:
        errs['annual_rate_pct'] = 'Enter the interest rate.'
        return errs
    r = Decimal(annual_rate_pct)
    if r < 0:
        errs['annual_rate_pct'] = 'Interest rate cannot be negative.'
    elif r > MAX_ANNUAL_RATE_PCT:
        errs['annual_rate_pct'] = (
            f'That rate ({r}%) is above the {MAX_ANNUAL_RATE_PCT}% ceiling — '
            f'did you mean {DEFAULT_ANNUAL_RATE_PCT}%?'
        )
    return errs


def validate_affordability(monthly, monthly_salary) -> dict:
    """The monthly repayment must not exceed the employee's monthly salary
    (otherwise the payslip goes negative on the loan line alone)."""
    errs: dict[str, str] = {}
    if monthly_salary and Decimal(monthly) > Decimal(monthly_salary):
        errs['approved_amount'] = (
            f'The monthly repayment ({q(monthly)}) is more than one month salary '
            f'({q(monthly_salary)}). Lower the amount or lengthen the term.'
        )
    return errs


def validate_vehicle_loan(amount, term_months) -> dict:
    errs: dict[str, str] = {}
    amount = Decimal(amount or 0)
    term = int(term_months or 0)
    if amount <= 0:
        errs['amount_requested'] = 'Enter an amount greater than zero.'
    elif amount > VEHICLE_MAX_AMOUNT:
        errs['amount_requested'] = (
            f'A vehicle loan cannot be more than P{q(VEHICLE_MAX_AMOUNT):,}.'
        )
    if not (MIN_TERM_MONTHS <= term <= VEHICLE_MAX_TERM_MONTHS):
        errs['term_months_requested'] = (
            f'A vehicle loan term must be between {MIN_TERM_MONTHS} and '
            f'{VEHICLE_MAX_TERM_MONTHS} months.'
        )
    return errs
