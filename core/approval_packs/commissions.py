"""The detail pack behind ONE commission submission.

This is the approval the CFO screenshotted on 2026-09-20: the email told him an
agent, a period, a gross and a net, and nothing else. What he actually checks —
which policies, at what rate, against what premium, and whether the month looks
like the agent's usual — was only ever on the desktop page.

`commissions.review_flags.review_flags` already computed the sanity checks for
that page. Nothing ever put them in front of the person signing.
"""
from __future__ import annotations

from decimal import Decimal

from core.approval_pack import make_pack, money, pct


def _sub(pk):
    from commissions.models import CommissionSubmission
    return (CommissionSubmission.objects
            .select_related("agent", "group")
            .prefetch_related("lines")
            .filter(pk=pk).first())


def build(pk):
    sub = _sub(pk)
    if sub is None:
        return None

    agent = getattr(getattr(sub, "agent", None), "name", "") or "an agent"
    group = getattr(getattr(sub, "group", None), "name", "") or ""
    period = sub.period_label or ""

    # Sorted in Python so the prefetched cache is used — a per-line query here
    # would run once per reviewer per email (commissions/notify.py, K5).
    lines = sorted(sub.lines.all(), key=lambda l: (l.policy_number or ""))
    rows = [[
        l.policy_number or "—",
        l.client_name or "—",
        money(l.annualised_premium or l.amount_collected, ""),
        pct(l.commission_rate),
        money(l.commission_amount, ""),
    ] for l in lines]
    line_sum = sum((l.commission_amount or Decimal("0.00") for l in lines), Decimal("0.00"))

    summary = [
        {"label": "Agent", "value": agent},
        {"label": "Group", "value": group},
        {"label": "Month", "value": period},
        {"label": "Policies", "value": str(len(lines)) if lines else ""},
        {"label": "Gross", "value": money(sub.gross_commission)},
        {"label": f"Withholding ({pct(sub.withholding_rate)})" if sub.withholding_rate
         else "Withholding", "value": "−" + money(sub.withholding_amount)
         if sub.withholding_amount else ""},
        {"label": "Net payable", "value": money(sub.net_payable)},
    ]

    # Last month, so a spike is visible as a figure and not only as a warning.
    note = ""
    try:
        from commissions.review_flags import _prior_gross
        prior = _prior_gross(sub)
        if prior is not None:
            note = f"Last approved month for {agent}: {money(prior)}."
    except Exception:  # noqa: BLE001 — context is a bonus, never an error
        pass

    try:
        from commissions.review_flags import review_flags
        checks = review_flags(sub)
    except Exception:  # noqa: BLE001 — 'unknown', never 'clean': a crashed
        checks = {"level": "unknown", "items": []}   # check must not read as a passed one

    return make_pack(
        "commissions",
        title=f"{agent} · {period}".strip(" ·"),
        subtitle="Monthly agent commission",
        summary=summary,
        columns=["Policy", "Client", "Premium", "Rate", "Commission"],
        rows=rows,
        row_total=money(line_sum),
        checks=checks,
        note=note,
    )
