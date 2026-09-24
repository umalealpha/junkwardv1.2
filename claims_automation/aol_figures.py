"""Agreement of Loss settlement arithmetic."""

from decimal import Decimal, ROUND_HALF_UP

_CENT = Decimal("0.01")


def _to_decimal(value):
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value))


def _quantize(value):
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _fmt_2dp(value):
    return f"{value:.2f}"


def settlement(sum_insured, excess, outstanding_premium=None, salvage_retained=None):
    if sum_insured is None or str(sum_insured).strip() == "":
        raise ValueError("sum_insured is required")

    sum_insured_raw = _to_decimal(sum_insured)
    if sum_insured_raw < 0:
        raise ValueError("sum_insured cannot be negative")

    sum_insured_dec = _quantize(sum_insured_raw)
    lines = [{"label": "Sum insured", "amount": _fmt_2dp(sum_insured_dec)}]

    total = sum_insured_dec
    deductions = [
        ("Less: excess", excess),
        ("Less: outstanding premium", outstanding_premium),
        ("Less: salvage retained by insured", salvage_retained),
    ]

    for label, raw in deductions:
        if raw is None:
            continue

        raw_dec = _to_decimal(raw)
        if raw_dec < 0:
            raise ValueError("deductions cannot be negative")

        amount = _quantize(raw_dec)
        if amount > 0:
            lines.append({"label": label, "amount": f"-{_fmt_2dp(amount)}"})
        total -= amount

    note = ""
    if total < 0:
        total = Decimal("0.00")
        note = "Deductions exceed the sum insured; net settlement floored at zero and requires review."

    net_dec = _quantize(total)
    # B9 — the approved Agreement of Loss money block is exactly three lines:
    # Total Claim, Less Excess, Total Claim Payable. `other_deductions` is what
    # this function took off BEYOND the excess; the letter refuses to print a
    # three-line block while that is non-zero, because the block would then show
    # a payable the three lines do not add up to. What else comes off is
    # for Finance to confirm.
    # A missing excess stays MISSING all the way to the letter. Quantising it to
    # 0.00 here is what made the letter's "never defaulted" guard useless.
    excess_dec = _quantize(_to_decimal(excess)) if excess is not None else None
    other = _quantize(sum_insured_dec - (excess_dec or Decimal("0.00")) - net_dec)
    return {
        "net": _fmt_2dp(net_dec),
        "net_display": f"{net_dec:,.2f}",
        "requires_review": True,
        "note": note,
        "lines": lines,
        "total_claim": _fmt_2dp(sum_insured_dec),
        "excess": _fmt_2dp(excess_dec) if excess_dec is not None else None,
        "other_deductions": _fmt_2dp(other if other > 0 else Decimal("0.00")),
    }
