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
    return {
        "net": _fmt_2dp(net_dec),
        "net_display": f"{net_dec:,.2f}",
        "requires_review": True,
        "note": note,
        "lines": lines,
    }
