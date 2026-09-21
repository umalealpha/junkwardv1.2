"""genric/money.py — rounding, stated once.

These are regulatory figures that end up on an invoice to a reinsurer. Rounding
is a decision, not a language default:

  * Python's ``round()`` is banker's rounding — round(0.5) is 0, round(1.5) is 2.
    On a cession that is a real cent, on the wrong side, in GENRIC's favour or
    ours depending on the digit. Never use it here.
  * ``Decimal`` with ``ROUND_HALF_UP`` is the finance convention and the one
    already used elsewhere in Omni (``billing/reverse_charge_models.py``).

Every intermediate step of the cession is quantised as it is produced, not once
at the end, because the worked example Finance signs off shows each step to the
cent and the invoice total must be the sum of the steps a human can re-key.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal('0.01')


def q2(value) -> Decimal:
    """Quantise to 2 decimal places, HALF UP. The only rounding in this app."""
    if value is None:
        return Decimal('0.00')
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money(value, currency: str = 'R') -> str:
    """Display form. Cents are always shown — a regulatory figure is never whole."""
    return f'{currency}{q2(value):,.2f}'
