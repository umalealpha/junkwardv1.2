"""Number-to-words for settlement amounts in pula and thebe."""
from decimal import Decimal, ROUND_HALF_UP

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen"
]
_TENS = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety"
]


def _under_1000(n):
    if n == 0:
        return ""
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " hundred")
        n %= 100
        if n:
            parts.append("and")
    if n >= 20:
        parts.append(_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def _int_to_words(n):
    if n == 0:
        return "zero"

    groups = []
    while n:
        groups.insert(0, n % 1000)
        n //= 1000

    scales = ["", "thousand", "million", "billion", "trillion"]
    parts = []
    for i, group in enumerate(groups):
        scale = scales[len(groups) - 1 - i]
        if group:
            words = _under_1000(group)
            if scale:
                words += " " + scale
            parts.append(words)

    if len(parts) > 1 and groups[-1] and groups[-1] < 100:
        parts[-1] = "and " + parts[-1]

    return " ".join(parts)


def pula_in_words(value):
    if not isinstance(value, Decimal):
        raise TypeError("amount must be a Decimal")
    if value < 0:
        raise ValueError("amount cannot be negative")

    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cents = int(rounded.scaleb(2))

    pula = cents // 100
    thebe = cents % 100

    pula_words = _int_to_words(pula)
    thebe_words = _int_to_words(thebe)

    return f"{pula_words[0].upper()}{pula_words[1:]} pula and {thebe_words} thebe"