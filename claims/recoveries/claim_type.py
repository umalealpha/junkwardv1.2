from __future__ import annotations

from enum import Enum
from typing import Union


class ClaimType(Enum):
    """Closed picklist of claim types for subrogation recovery tracking.

    These five categories cover all claim types in the Alpha Direct register
    (988 rows as of 17-Aug-2026). The enum exists to prevent typo-driven data
    fragmentation — the register spells one claim type as many as fifteen
    different ways (e.g. "Motor Accident", "MOTOR ACCIDENT", "Accident",
    "MOTPR ACCIDENT CLAIM"). normalise_claim_type collapses them.
    """
    MOTOR_ACCIDENT = "Motor Accident"
    BUILDINGS_COMBINED = "Buildings Combined"
    FIRE_SPECIAL_PERILS = "Fire and Special Perils"
    MONEY_FIDELITY = "Money and Fidelity"
    OTHER = "Other"


def normalise_claim_type(raw: Union[str, None, int, float]) -> ClaimType:
    """Collapse misspellings and whitespace variations onto the closed picklist.

    Takes a raw cell value from the live subrogation register (which may be None,
    a stray float, or free text with typos and inconsistent spacing) and returns
    a ClaimType enum member. Unrecognised input defaults to OTHER, never raises.

    The implementation uses an explicit alias table for known misspellings rather
    than fuzzy matching. This prevents "Non Motor" (which contains the substring
    "Motor") from being misclassified as MOTOR_ACCIDENT — we match cleaned whole
    phrases, never substrings.

    Args:
        raw: A value from a spreadsheet cell (str, None, int, float).

    Returns:
        A ClaimType enum member (always from the closed picklist).

    Examples:
        normalise_claim_type("Motor Accident Claim") -> ClaimType.MOTOR_ACCIDENT
        normalise_claim_type("MOTPR ACCIDENT CLAIM") -> ClaimType.MOTOR_ACCIDENT
        normalise_claim_type("Non Motor") -> ClaimType.OTHER
        normalise_claim_type(None) -> ClaimType.OTHER
        normalise_claim_type(42.0) -> ClaimType.OTHER
    """

    # Trap: non-string input (int, float from xlsb) — coerce to string or fall through to OTHER
    if raw is None:
        raw = ""
    elif isinstance(raw, (int, float)):
        raw = str(raw)

    # Guard: raw is now definitely a string
    if not isinstance(raw, str):
        return ClaimType.OTHER

    # Normalise: strip leading/trailing whitespace, collapse internal whitespace, uppercase
    cleaned = " ".join(raw.split()).upper()

    # Blank check (after normalisation, so "   " and "\t" become "")
    if not cleaned:
        return ClaimType.OTHER

    # Explicit alias table for known misspellings and variations.
    # Key: cleaned (uppercase, whitespace-collapsed) input.
    # Value: the ClaimType to return.
    #
    # Built from the live register (988 rows, 17-Aug-2026). Includes typos
    # ("MOTPR" for MOTOR, "FEDILITY" for FIDELITY), colloquial abbreviations
    # ("ACCIDENT" alone for "Motor Accident"), and case/spacing variations.
    aliases = {
        # MOTOR_ACCIDENT variations (263 rows with trailing spaces, 14 rows as "Accident" alone, 1 row as "MOTPR")
        "MOTOR ACCIDENT CLAIM": ClaimType.MOTOR_ACCIDENT,
        "MOTOR ACCIDENT": ClaimType.MOTOR_ACCIDENT,
        "ACCIDENT": ClaimType.MOTOR_ACCIDENT,
        "MOTPR ACCIDENT CLAIM": ClaimType.MOTOR_ACCIDENT,  # keying typo, 1 row

        # BUILDINGS_COMBINED variations
        "BUILDING COMBINED": ClaimType.BUILDINGS_COMBINED,
        "BUILDINGS COMBINED": ClaimType.BUILDINGS_COMBINED,

        # FIRE_SPECIAL_PERILS variations
        "FIRE AND SPECIAL PERILS CLAIM": ClaimType.FIRE_SPECIAL_PERILS,
        "FIRE AND SPECIAL PERILS": ClaimType.FIRE_SPECIAL_PERILS,

        # MONEY_FIDELITY variations (includes typo "FEDILITY" from register)
        "MONEY AND FIDELITY CLAIM (MONEY, AIRTIME,CHEQUES ETS)": ClaimType.MONEY_FIDELITY,  # typo "ets"
        "MONEY AND FIDELITY CLAIM (MONEY, AIRTIME,CHEQUES ETC)": ClaimType.MONEY_FIDELITY,
        "MONEY AND FEDILITY CLAIM (MONEY, AIRTIME,CHEQUES ETS)": ClaimType.MONEY_FIDELITY,  # "Fedility" typo
        "MONEY AND FEDILITY CLAIM (MONEY, AIRTIME,CHEQUES ETC)": ClaimType.MONEY_FIDELITY,  # "Fedility" typo
        "MONEY AND FIDELITY": ClaimType.MONEY_FIDELITY,
    }

    # Direct lookup in alias table
    if cleaned in aliases:
        return aliases[cleaned]

    # If not found, return OTHER (includes "Non Motor", "Glass", unknown strings)
    return ClaimType.OTHER
