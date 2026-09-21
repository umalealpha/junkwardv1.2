"""Recovery register claim number parser.

The register tracks BWP 42.1M in subrogation claims. It contains two numbering
eras: legacy 8-digit form (20150018) from before Graphite, and Graphite form
(G2026004951). Only ~17% of register rows match claims in Graphite, so the
parser must plainly distinguish which era a number belongs to rather than guess
a link that isn't there.

Spreadsheet cells arrive as strings, integers, or floats (xlsb hands back
20241409.0 instead of '20241409'). The parser accepts any input type and never
raises an exception; bad inputs return UNKNOWN format instead.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum


class NumberFormat(Enum):
    """Claim number format and era.

    The register evolved through two distinct numbering systems. This enum
    marks which system a given number uses, allowing callers to handle each
    era's specific rules or link attempts without guessing.
    """
    LEGACY = "legacy"      # 8-10 digits; first 4 are year (2000-2100)
    GRAPHITE = "graphite"  # G + 4-digit year + digits
    UNKNOWN = "unknown"    # Unrecognized placeholder or malformed


@dataclass(frozen=True)
class ClaimNumber:
    """Parsed claim number from the recovery register.

    The register is large and messy. This dataclass holds both the parsed
    result AND explicit suspects, so downstream code can decide what to do
    with a Graphite number that has an extra digit, or a legacy number with
    a trailing note like " -SA".

    Attributes:
        normalised: Cleaned string representation (leading/trailing whitespace
                   stripped, case normalized to uppercase). Trailing notes are
                   preserved and flagged in is_suspect.
        fmt: NumberFormat enum value marking which era the number belongs to.
        year: Extracted 4-digit claim year, or None if the leading 4 digits
              are outside 2000-2100 or the number is UNKNOWN format.
        is_graphite_era: True only when fmt is GRAPHITE. Exists as an explicit
                        flag so callers can gate Graphite-specific logic without
                        re-testing the enum.
        is_suspect: True if the number has an unexpected digit count (Graphite
                   with more than 6 digits after the year), or trailing junk
                   (legacy with non-digit characters after the 8-10 digits).
                   These are real register values; they are flagged, not rejected.
    """
    normalised: str
    fmt: NumberFormat
    year: int | None
    is_graphite_era: bool
    is_suspect: bool


def parse_claim_number(raw) -> ClaimNumber:
    """Parse a claim number from the recovery register.

    Input may come from a spreadsheet: string, integer, float (xlsb numeric
    cells), None, or free text. No input causes an exception. Unknown or
    malformed values return UNKNOWN format with year=None.

    The parser recognises two eras:

    1. Graphite: G + 4-digit year (2000-2100) + 6 digits. The exact standard
       is G + year + 6; numbers with 7+ trailing digits are readable but
       flagged is_suspect=True.

    2. Legacy: 8-10 total digits, first 4 are year (2000-2100). Trailing
       non-digit characters (e.g., " -SA" from a spreadsheet note) are
       preserved in normalised and flagged is_suspect=True.

    Float inputs are converted to strings without a decimal point before
    parsing (20241409.0 → '20241409'). Whitespace is stripped; the leading
    G is uppercased for case-insensitive matching.

    Args:
        raw: A value from a spreadsheet cell. String, int, float, None, or
             any other type.

    Returns:
        ClaimNumber with fields normalised, fmt, year, is_graphite_era,
        is_suspect. Never raises.
    """

    # None and non-numeric/non-string inputs → UNKNOWN
    if raw is None:
        return ClaimNumber(
            normalised="",
            fmt=NumberFormat.UNKNOWN,
            year=None,
            is_graphite_era=False,
            is_suspect=False
        )

    # Convert float and int to string (no decimal point)
    if isinstance(raw, float):
        # Guard against NaN and infinity: these are invalid claim numbers
        if math.isnan(raw) or math.isinf(raw):
            return ClaimNumber(
                normalised="",
                fmt=NumberFormat.UNKNOWN,
                year=None,
                is_graphite_era=False,
                is_suspect=False
            )
        raw = str(int(raw))
    elif isinstance(raw, int):
        raw = str(raw)

    # If still not a string, it's unrecognized
    if not isinstance(raw, str):
        return ClaimNumber(
            normalised="",
            fmt=NumberFormat.UNKNOWN,
            year=None,
            is_graphite_era=False,
            is_suspect=False
        )

    # Clean: strip outer whitespace, uppercase for G detection
    cleaned = raw.strip().upper()

    # Empty after stripping → UNKNOWN
    if not cleaned:
        return ClaimNumber(
            normalised="",
            fmt=NumberFormat.UNKNOWN,
            year=None,
            is_graphite_era=False,
            is_suspect=False
        )

    # Try Graphite format: G + 4-digit year + digits (possibly with junk)
    if cleaned.startswith('G'):
        match = re.match(r'^G(\d{4})(.*)$', cleaned)
        if match:
            year_str = match.group(1)
            year_int = int(year_str)
            rest = match.group(2)

            # Only accept reasonable claim years
            if 2000 <= year_int <= 2100:
                # Standard Graphite is G + year + 6 digits = 11 chars total.
                # is_suspect if rest has non-digits or is not exactly 6 digits.
                is_suspect = not rest.isdigit() or len(rest) != 6

                return ClaimNumber(
                    normalised=cleaned,
                    fmt=NumberFormat.GRAPHITE,
                    year=year_int,
                    is_graphite_era=True,
                    is_suspect=is_suspect
                )

    # Try legacy format: 4-digit year + 4-6 more digits + optional trailing junk
    match = re.match(r'^(\d{4})(.*)$', cleaned)
    if match:
        year_str = match.group(1)
        year_int = int(year_str)
        rest = match.group(2)

        # Only accept reasonable claim years
        if 2000 <= year_int <= 2100:
            # Extract the digit sequence after the year
            digit_match = re.match(r'^(\d+)', rest)
            if digit_match:
                digits_after_year = digit_match.group(1)
                total_digits = 4 + len(digits_after_year)

                # Legacy is 8-10 total digits
                if 8 <= total_digits <= 10:
                    # Check if there's trailing non-digit content (notes, spaces, etc.)
                    has_trailing_junk = len(rest) > len(digits_after_year)
                    is_suspect = has_trailing_junk

                    return ClaimNumber(
                        normalised=cleaned,
                        fmt=NumberFormat.LEGACY,
                        year=year_int,
                        is_graphite_era=False,
                        is_suspect=is_suspect
                    )

    # Unrecognized format
    return ClaimNumber(
        normalised="",
        fmt=NumberFormat.UNKNOWN,
        year=None,
        is_graphite_era=False,
        is_suspect=False
    )
