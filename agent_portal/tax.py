"""
agent_portal/tax.py — UniCoin *Instant Insurance* agent commission tax.

A FAITHFUL replica of Bharath Balasubramanian's "Tax Calculation - Commission
Compiler" workbook (July 2026). The CFO chose (2026-07-27) that an agent's
commission payslip is shown NET OF TAX, using Bharath's method — not a
"corrected" one. So this module reproduces his sheet exactly, including two
quirks we deliberately keep so the payslips tie to his numbers:

  1. His agent scale is a CUSTOM one, NOT the standard Botswana resident PAYE
     (which is 0/12/18/25%). Bharath uses 0 / 5 / 12.5 / 18.75 / 25% on the
     ANNUALISED commission (monthly × 12), then divides the annual tax by 12:

         annual = monthly_commission * 12
         if   annual <= 48000: annual_tax = 0
         elif annual <= 84000: annual_tax = (annual - 48000)  * 5%
         elif annual <=120000: annual_tax = 1800  + (annual - 84001)  * 12.5%
         elif annual <=156000: annual_tax = 6300  + (annual - 120001) * 18.75%
         else:                 annual_tax = 13050 + (annual - 156001) * 25%
         monthly_tax = annual_tax / 12

     Verified against his sheet: Denford Paradza monthly 31,843.25 -> annual
     382,119 -> annual_tax 69,579.50 -> monthly_tax 5,798.29 -> net 26,044.96.

  2. His formula subtracts P1 inside each band ((annual-84001) not
     (annual-84000), etc.). That is a spreadsheet off-by-one worth a few thebe
     at a band edge. We keep it so our figure equals his to the cent.

  3. He also computes an "Exclude VAT (14%)" column (commission / 1.14) but does
     NOT subtract it from the amount paid — net = commission - tax only. We
     surface the VAT figure as a MEMO on the payslip and never deduct it, mirror
     of his sheet.

Nothing here touches the GL or moves money — it only decides the numbers printed
on a payslip. If the CFO ever wants the standard Botswana PAYE scale instead,
change BANDS + BASES below (one place) and re-issue.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal('0.01')
VAT_RATE = Decimal('0.14')          # memo only — never deducted from net

# Bharath's custom agent scale (annualised BWP). Each tuple is
# (upper_bound_inclusive, base_tax_at_lower_edge, marginal_rate, lower_minus_one).
# `lower_minus_one` reproduces his (annual - <lower+1>) offset exactly.
_BANDS = [
    (Decimal('48000'),  Decimal('0'),     Decimal('0'),       Decimal('0')),
    (Decimal('84000'),  Decimal('0'),     Decimal('0.05'),    Decimal('48000')),
    (Decimal('120000'), Decimal('1800'),  Decimal('0.125'),   Decimal('84001')),
    (Decimal('156000'), Decimal('6300'),  Decimal('0.1875'),  Decimal('120001')),
    (None,              Decimal('13050'), Decimal('0.25'),    Decimal('156001')),
]


def _q(x) -> Decimal:
    return Decimal(str(x)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def annual_tax(annual) -> Decimal:
    """Bharath's annual income tax on an annualised commission (BWP).

    Returns an UNROUNDED Decimal (his sheet divides the raw annual figure by 12
    before it is shown, so rounding happens once, on the monthly figure)."""
    a = Decimal(str(annual))
    for upper, base, rate, lower in _BANDS:
        if upper is None or a <= upper:
            return base + (a - lower) * rate if rate else Decimal('0')
    return Decimal('0')  # unreachable (last band is open-ended)


@dataclass(frozen=True)
class AgentTax:
    """The tax view of one month's commission for one agent."""
    gross: Decimal          # commission paid this month (BWP)
    ex_vat: Decimal         # gross / 1.14 — MEMO ONLY, not deducted
    annual: Decimal         # gross * 12
    tax: Decimal            # monthly income tax (annual_tax / 12), rounded
    net: Decimal            # gross - tax  (what the agent is paid)


def agent_tax(gross_commission) -> AgentTax:
    """Compute the payslip tax view for a month's commission, Bharath's way."""
    gross = _q(gross_commission)
    annual = gross * 12
    tax = _q(annual_tax(annual) / 12)
    return AgentTax(
        gross=gross,
        ex_vat=_q(gross / (Decimal('1') + VAT_RATE)),
        annual=_q(annual),
        tax=tax,
        net=_q(gross - tax),
    )
