from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


# Text in a money column that genuinely means "no amount".
_BLANK_MONEY = {'', '-', '--', 'n/a', 'na', 'nil', 'none', 'null', 'tba', 'tbc',
                'unknown', 'no number'}


def _to_decimal(value):
    """
    Read one money cell as a Decimal. Never raises.

    Falling back to zero is correct for a cell that genuinely holds no amount
    ('n/a', 'TBA', blank). It is NOT correct for a real amount that merely
    carries spreadsheet formatting: an earlier version degraded '1,260.00' to
    zero, which silently understated the recoverable by the whole cell on a
    P42.1m book. Formatting is stripped first, and only then does an
    unreadable value fall back to zero.

    Handles: thousands separators (comma or space), a currency prefix
    ('P', 'BWP'), and accounting negatives written as '(1,260.00)'.

    Args:
        value: int, float, str, Decimal, or None

    Returns:
        Decimal: the amount, or Decimal('0') where there genuinely is none.
    """
    if value is None:
        return Decimal('0')

    if isinstance(value, Decimal):
        return Decimal('0') if (value.is_nan() or value.is_infinite()) else value

    if isinstance(value, bool):
        # A tick in a money column is not an amount.
        return Decimal('0')

    if isinstance(value, (int, float)):
        try:
            result = Decimal(str(value))
        except Exception:
            return Decimal('0')
        return Decimal('0') if (result.is_nan() or result.is_infinite()) else result

    if isinstance(value, str):
        text = value.strip()
        if text.lower() in _BLANK_MONEY:
            return Decimal('0')

        # '(1,260.00)' is accounting notation for a negative.
        negative = text.startswith('(') and text.endswith(')')
        if negative:
            text = text[1:-1].strip()

        # Strip a currency mark, then thousands separators (comma or space).
        for mark in ('BWP', 'bwp', 'Bwp', 'P', 'p'):
            if text.startswith(mark):
                text = text[len(mark):].strip()
                break
        text = text.replace(',', '').replace(' ', '').replace(' ', '')

        if not text:
            return Decimal('0')
        try:
            result = Decimal(text)
        except Exception:
            # Genuine free text — the documented fallback.
            return Decimal('0')
        if result.is_nan() or result.is_infinite():
            return Decimal('0')
        return -result if negative else result

    # Lists, dicts and anything else are not amounts.
    return Decimal('0')


def compute_total_recoverable(
    assessor_fees,
    repair_costs,
    client_excess,
    towing_fees,
    legal_fees,
    salvage_amount,
) -> Decimal:
    """
    Compute the total recoverable amount per Keetile Mokhendo's formula.

    Total recovery = assessor_fees + repair_costs + client_excess + towing_fees
                   + legal_fees - salvage_amount

    All parameters may be int, float, str, Decimal, or None. None and blank
    values are treated as zero. Invalid inputs (free text, NaN, infinity,
    malformed strings) degrade to zero and do not raise an exception.
    The result is returned as a Decimal quantized to 2 decimal places using
    ROUND_HALF_UP (Alpha Direct's standard for money).

    If salvage exceeds the sum of costs, the result is negative — we do not
    clamp to zero, as negative values indicate over-recovery which must be
    tracked and reported.

    Args:
        assessor_fees: Assessor or surveyor fees
        repair_costs: Cost of repairs to the damaged property
        client_excess: Insurance excess paid by the insured
        towing_fees: Towing or transportation costs
        legal_fees: Legal costs incurred
        salvage_amount: Salvage value recovered from damaged goods

    Returns:
        Decimal: Total recoverable amount, quantized to 2 places, ROUND_HALF_UP.
    """
    assessor_fees_d = _to_decimal(assessor_fees)
    repair_costs_d = _to_decimal(repair_costs)
    client_excess_d = _to_decimal(client_excess)
    towing_fees_d = _to_decimal(towing_fees)
    legal_fees_d = _to_decimal(legal_fees)
    salvage_amount_d = _to_decimal(salvage_amount)

    total = (
        assessor_fees_d
        + repair_costs_d
        + client_excess_d
        + towing_fees_d
        + legal_fees_d
        - salvage_amount_d
    )

    # Quantize to 2 decimal places using ROUND_HALF_UP
    return total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def compute_outstanding(
    total_recoverable,
    recovered_to_date,
) -> Decimal:
    """
    Compute the outstanding balance still to be recovered.

    Outstanding = total_recoverable - recovered_to_date

    If recovered_to_date exceeds total_recoverable, the result is negative,
    indicating that more has been collected than the assessed loss. Invalid
    inputs degrade to zero without raising an exception.

    Args:
        total_recoverable: The total amount assessed as recoverable
        recovered_to_date: The amount already recovered/collected

    Returns:
        Decimal: Outstanding balance, quantized to 2 places, ROUND_HALF_UP.
    """
    total_recoverable_d = _to_decimal(total_recoverable)
    recovered_to_date_d = _to_decimal(recovered_to_date)

    outstanding = total_recoverable_d - recovered_to_date_d

    # Quantize to 2 decimal places using ROUND_HALF_UP
    return outstanding.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
