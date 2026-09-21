"""RealPay InstalmentStatus normalisation for the UniCoin reports.

Single source of truth for the code set is ``finance_monitoring`` — this module
adds only the stray-text handling the log needs (the mirror carries the full
words ``SUCCESSFUL/FAILED/PROCESSING/ERROR/CANCELLED`` as well as the single
letters). It does NOT re-declare the legend or the failure set: duplicating a
parser is how one total ends up computed two different ways.

Pack §4.7: the success set is {S, SUCCESS, SUCCESSFUL}; for a chase list F and E
are BOTH failures to collect (leaving E out understates the list); an unknown
code is never invented into a success or a failure.
"""
from __future__ import annotations

from reporting.finance_monitoring import (
    INSTALMENT_STATUS_LABEL,  # {'S':'Success','F':'Failed','W':'Processing',...}
    FAILED_STATUSES,          # ('F', 'E') — the canonical chase-list failure set
)

#: The raw tokens that mean a successful collection, per the pack. Whole words
#: as well as the letter, because the mirror carries both.
SUCCESS_SET = frozenset({"S", "SUCCESS", "SUCCESSFUL"})

#: Whole-word aliases → the canonical single letter in INSTALMENT_STATUS_LABEL.
#: Derived from the labels themselves so it cannot drift from the legend.
_WORD_TO_LETTER = {label.upper(): letter
                   for letter, label in INSTALMENT_STATUS_LABEL.items()}
_WORD_TO_LETTER.update({"SUCCESSFUL": "S"})  # a stray variant not in the labels


def normalise_instalment_status(raw) -> str:
    """A raw status value as its canonical single letter.

    None/blank → "". A whole word (SUCCESS, FAILED, ERROR, …) maps to its letter.
    A single letter passes through uppercased. Any other unknown value is
    returned trimmed and uppercased unchanged — never coerced to a code.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if text == "":
        return ""
    upper = text.upper()
    if upper in _WORD_TO_LETTER:
        return _WORD_TO_LETTER[upper]
    if len(text) == 1:
        return upper
    return upper


def is_collection_failure(raw) -> bool:
    """True only for a debit that did not collect: normalised F or E."""
    return normalise_instalment_status(raw) in set(FAILED_STATUSES)


def is_collection_success(raw) -> bool:
    """True only for a genuine success (normalised S)."""
    return normalise_instalment_status(raw) == "S"
