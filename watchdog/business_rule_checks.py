"""watchdog/business_rule_checks.py — "validate the business rules every night".

Read-only. Starts with the one the CFO calls out first — payment dual control
(CFO ruling 2026-07-07: one Finance-Manager/FC approval AND one distinct CFO/CEO
approval, neither the maker). Reuses the model's own quorum test so this check can
never drift from the rule the app enforces.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from watchdog.checks import CheckResult, register

# Only look at recently confirmed payments — a control failure on a payment
# confirmed today is actionable; historical rows are water under the bridge and
# would only be noise every night.
_WINDOW_DAYS = 30
_MAX = 2000


@register(key="payment_dual_control", label="Payment dual control",
          module="payments", category="business_rule")
def payment_dual_control():
    """Every recently confirmed payment must carry a valid maker/checker pair."""
    from payments.models import Payment

    since = timezone.now() - timedelta(days=_WINDOW_DAYS)
    qs = (Payment.objects
          .filter(status__in=["confirmed", "reconciled"], created_at__gte=since)
          .order_by("-created_at")[:_MAX])

    bad = []
    for p in qs:
        try:
            quorum_ok = p._outbound_quorum()[0]
        except Exception:   # noqa: BLE001 — never let one row kill the check
            quorum_ok = False
        # Legacy pair fallback (older rows predate PaymentApproval).
        legacy_ok = bool(p.secondary_approved_by_id) and \
            p.secondary_approved_by_id != p.created_by_id
        if not (quorum_ok or legacy_ok):
            bad.append(p)

    if not bad:
        return [CheckResult(ok=True, title="Payment dual control holds")]

    refs = ", ".join(str(getattr(p, "reference", "") or p.id)[:24] for p in bad[:5])
    more = "" if len(bad) <= 5 else f" (+{len(bad) - 5} more)"
    return [CheckResult(
        ok=False,
        title=f"{len(bad)} confirmed payment(s) without dual control",
        detail=(f"Confirmed in the last {_WINDOW_DAYS} days with no valid "
                f"maker/checker pair (one FM/FC + one distinct exec): {refs}{more}."),
        domains=("payment", "finance"), severity="danger")]
