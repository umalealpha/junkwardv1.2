"""Plain-language for RealPay / EFT debit-failure reasons (A3).

Pack Task 3. Turns ``realpay_contract_installments.instalmentResponse`` (already
human text) into a plain reason for a call-centre list. The authoritative RealPay
Response Code Report (portal f?p=1230:79) and the DB-backed ``RealPayResponseCode``
table are the real source; until A3 is wired to that on the box this maps only
the common, unambiguous BankservAfrica wordings and NEVER invents meaning:

  * an unrecognised, non-empty message is returned VERBATIM and is_mapped() is
    False (so the caller can see how many reasons still need mapping);
  * a blank message returns a safe "No reason given", is_mapped() False.

Matches are ordered most-specific first, so a multi-reason string like
"Account closed - no account activity" reads as "account closed", not "no account".
"""
from __future__ import annotations

#: (substring triggers, canonical phrase). Order matters — first match wins, so
#: the more specific phrase must come before a shorter one it contains.
#:
#: The triggers below were reconciled against the ACTUAL distinct values of
#: realpay_contract_installments.instalmentResponse on the live replica
#: (2026-09-07): 12 distinct failure reasons across 6.6m rows. The verbatim
#: wordings RealPay emits are listed beside each canonical phrase so a future
#: reader can see the source, not a guess. "No Code Description Found" (the bank
#: returned a code with no text) is deliberately left unmapped — it has no
#: meaning to translate.
_MAPPINGS = [
    (("account closed",),                 "Account closed"),
    (("account frozen", "account blocked"), "Account frozen"),
    (("account dormant", "dormant account"), "Account dormant"),
    # "Acc failed validation" (2,474)
    (("failed validation",),              "Account failed validation"),
    # "Additional amnt not allowed" (442)
    (("additional amnt", "additional amount not allowed"),
                                          "Additional amount not allowed"),
    # "Acc does not exist" (12,613)
    (("no such account", "no account", "account not found",
      "does not exist", "acc does not exist"), "No such account"),
    # "INCORRECT ACCOUNT DETAIL" (475), "homing/sub acc no combination invalid" (12)
    (("invalid account", "account invalid", "incorrect account",
      "combination invalid"),            "Invalid account details"),
    (("account transferred",),            "Account transferred"),
    (("mandate cancelled", "mandate cancel", "authorisation cancelled"),
                                          "Mandate cancelled"),
    (("debits not allowed", "debit not permitted"),
                                          "Debits not allowed on this account"),
    (("payment stopped", "stop order", "stopped payment"), "Payment stopped"),
    # "Invalid transaction ref no" (1)
    (("invalid transaction ref",),        "Invalid transaction reference"),
    # "NO AVAILABLE FUNDS" (111,238) — the single biggest reason
    (("no available funds", "available funds", "insufficient funds",
      "no funds", "not enough funds"),    "Insufficient funds"),
]


def _match(raw):
    """Return the canonical phrase for a raw message, or None if unrecognised."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    for triggers, canonical in _MAPPINGS:
        if any(t in text for t in triggers):
            return canonical
    return None


def plain_language(raw) -> str:
    """A plain-English reason. Unknown text is returned verbatim; blank is safe."""
    if raw is None or not str(raw).strip():
        return "No reason given"
    hit = _match(raw)
    return hit if hit is not None else str(raw)


def is_mapped(raw) -> bool:
    """True only when the message matched a known reason."""
    return _match(raw) is not None
