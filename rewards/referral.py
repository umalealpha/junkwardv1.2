"""Pure referral code generation and eligibility logic for the Omni insurance app.

This module is deliberately free of Django, I/O, and money. It only decides
whether a referral may be credited, never what the credit is worth - the
amount is a CFO commercial term owned elsewhere.

Bad or missing data closes a benefit: None or unknown inputs yield a safe
'unknown'/locked result, never an optimistic one.
"""

import hashlib
import uuid

CODE_LENGTH = 8
_UNAMBIGUOUS_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_UNKNOWN_REASON = "unknown_code"


def make_code(member_id):
    """Return a stable, short, unambiguous referral code for a member.

    The same member always gets the same code; different members get
    different codes. The code uses no characters that can be misread
    (no 0, 1, I, O, L).
    """
    if not member_id:
        return ""
    digest = hashlib.sha256(str(member_id).encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big")
    code = ""
    for _ in range(CODE_LENGTH):
        code += _UNAMBIGUOUS_ALPHABET[value % len(_UNAMBIGUOUS_ALPHABET)]
        value //= len(_UNAMBIGUOUS_ALPHABET)
    return code


def normalise_code(code):
    """Normalise a code for comparison: uppercase, stripped, unambiguous.

    Returns an empty string for None or non-string input.
    """
    if not code or not isinstance(code, str):
        return ""
    return code.strip().upper()


def check_referral(code_owner_id, new_member_id, existing_pairs):
    """Check whether a referral may be credited.

    Returns a dict with 'ok' (bool) and 'reason' (str). Never returns a
    negative count and never opens a benefit on missing/unknown data.
    """
    if not code_owner_id or not new_member_id:
        return {"ok": False, "reason": _UNKNOWN_REASON}

    if not isinstance(existing_pairs, list):
        return {"ok": False, "reason": _UNKNOWN_REASON}

    if code_owner_id == new_member_id:
        return {"ok": False, "reason": "self_referral"}

    current_pair = (code_owner_id, new_member_id)
    for owner, referred in existing_pairs:
        if not owner or not referred:
            continue
        if (owner, referred) == current_pair:
            return {"ok": False, "reason": "already_credited_pair"}
        if referred == new_member_id:
            return {"ok": False, "reason": "already_referred_member"}

    return {"ok": True, "reason": ""}