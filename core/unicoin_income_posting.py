"""UniCoin online income capture sheet — server-side posting guard (C2).

Pack Task 5. The fixed category dropdown drives the posting and cannot be free
text, so it is enforced here rather than only in the UI:

  * Premium posts to BOTH Graphite and the general ledger.
  * Non-premium (operational / other) posts to the general ledger ONLY — its
    absence from Graphite is not a break.
  * A premium OR refund receipt on a policy that has NOT been issued holds as a
    DRAFT and posts nowhere until the policy exists. Operational / other income
    is not tied to a policy, so it still posts to the ledger.

Pure logic, no framework import, so it can be unit-proven off the box. The
ledger leg itself waits for the ledger migration (pack §3.5); this only decides
WHERE a receipt is allowed to post.
"""
from __future__ import annotations

CATEGORIES = frozenset({"premium", "operational", "refund", "other"})

#: Categories whose posting needs an issued policy; on an unissued policy they
#: hold as a draft rather than posting.
_NEEDS_ISSUED_POLICY = frozenset({"premium", "refund"})


class CategoryError(Exception):
    """An income category that is not one of the fixed dropdown values."""


def validate_category(raw) -> str:
    """The canonical category, or raise. Blank and free text are refused."""
    cleaned = str(raw).strip().lower()
    if cleaned in CATEGORIES:
        return cleaned
    raise CategoryError(f"Invalid income category: {raw!r}")


def posting_targets(category, policy_issued) -> dict:
    """Where a receipt of this category may post.

    Returns {"graphite": bool, "ledger": bool, "draft": bool}. ``policy_issued``
    must be a real bool — a DRF view hands through strings, and a truthy "false"
    would post a receipt that should have held as a draft, so a non-bool is a
    caller error and raises rather than being coerced.
    """
    cat = validate_category(category)
    if not isinstance(policy_issued, bool):
        raise CategoryError(
            f"policy_issued must be a boolean, got {type(policy_issued).__name__}"
        )

    if cat in _NEEDS_ISSUED_POLICY and not policy_issued:
        return {"graphite": False, "ledger": False, "draft": True}
    if cat == "premium":  # issued
        return {"graphite": True, "ledger": True, "draft": False}
    # operational, other, or a refund on an issued policy — ledger only.
    return {"graphite": False, "ledger": True, "draft": False}
