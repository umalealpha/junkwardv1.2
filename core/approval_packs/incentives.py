"""The detail pack behind ONE staff incentive request.

A signer sees every person on the request, what each is being paid for and how
much, the total, and whether the manager has attested it.
"""
from __future__ import annotations

from decimal import Decimal

from core.approval_pack import make_pack, money


def _row(pk):
    from hris.incentive_models import IncentiveRequest
    return (IncentiveRequest.objects
            .select_related("maker", "company")
            .prefetch_related("lines__employee")
            .filter(pk=pk).first())


def build(pk):
    req = _row(pk)
    if req is None:
        return None

    lines = list(req.lines.all())
    total = sum((Decimal(l.amount) for l in lines if l.amount), Decimal("0.00"))
    no_amount = sum(1 for l in lines if not l.amount)

    rows = []
    for l in lines:
        person = l.name or getattr(l.employee, "full_name", "") or ""
        rows.append([person, (l.basis or l.justification or "")[:80], money(l.amount, "")])

    raised_by = ""
    if req.maker_id:
        raised_by = req.maker.get_full_name() or req.maker.get_username()

    summary = [
        {"label": "Title", "value": req.title or ""},
        {"label": "Period", "value": req.period or ""},
        {"label": "Department", "value": req.department or ""},
        {"label": "Company", "value": getattr(req.company, "name", "")},
        {"label": "People", "value": str(len(lines)) if lines else ""},
        {"label": "Total", "value": money(total)},
        {"label": "Raised by", "value": raised_by},
        {"label": "Status", "value": req.get_status_display()},
    ]

    items = []
    if not req.manager_attested:
        items.append("The manager has not attested this")
    if no_amount:
        items.append(f"{no_amount} line(s) carry no amount")

    return make_pack(
        "incentives",
        title=f"{req.title or 'Incentive'} · {req.period or ''}".strip(" ·"),
        subtitle="Staff incentive",
        summary=summary,
        columns=["Person", "What for", "Amount"],
        rows=rows,
        row_total=money(total),
        checks={"level": "check" if items else "clean", "items": items},
    )
