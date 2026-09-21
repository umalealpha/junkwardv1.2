"""The detail pack behind ONE leave encashment.

A reviewer sees who is cashing leave, how many days, the balance those days come
out of, the daily rate they were priced at, and the gross/tax/net — plus two
checks that catch the two ways this goes wrong: cashing more days than the
person has, and a gross/tax/net that does not add up.
"""
from __future__ import annotations

from decimal import Decimal

from core.approval_pack import make_pack, money


def _row(pk):
    from hris.leave_encash_models import LeaveEncashment
    return (LeaveEncashment.objects
            .select_related("employee", "company")
            .filter(pk=pk).first())


def build(pk):
    enc = _row(pk)
    if enc is None:
        return None

    who = getattr(getattr(enc, "employee", None), "full_name", "") or "an employee"
    company = getattr(getattr(enc, "company", None), "name", "") or ""
    kind = ""
    try:
        kind = enc.get_kind_display()
    except Exception:  # noqa: BLE001 — display helpers are optional context
        kind = getattr(enc, "kind", "") or ""

    summary = [
        {"label": "Employee", "value": who},
        {"label": "Company", "value": company},
        {"label": "Leave type", "value": enc.leave_type_code or ""},
        {"label": "Kind", "value": kind},
        {"label": "Days being cashed", "value": str(enc.days) if enc.days is not None else ""},
        {"label": "Leave balance", "value": str(enc.balance_at_request)
         if enc.balance_at_request is not None else ""},
        {"label": "Basic salary", "value": money(enc.basic_salary)},
        {"label": "Daily rate", "value": money(enc.daily_rate)},
        {"label": "Gross", "value": money(enc.amount)},
        {"label": "Tax", "value": ("−" + money(enc.tax_amount)) if enc.tax_amount else ""},
        {"label": "Net payable", "value": money(enc.net_amount)},
        {"label": "Last day", "value": str(enc.last_day) if enc.last_day else ""},
        {"label": "Reason", "value": (enc.reason or "")[:200]},
        {"label": "Status", "value": enc.get_status_display()},
    ]

    items = []
    if enc.days is not None and enc.balance_at_request is not None \
            and Decimal(enc.days) > Decimal(enc.balance_at_request):
        items.append(f"They are cashing {enc.days} days but their balance is "
                     f"{enc.balance_at_request}")
    if enc.amount and enc.net_amount is not None and enc.tax_amount is not None:
        if abs(Decimal(enc.amount) - Decimal(enc.tax_amount)
               - Decimal(enc.net_amount)) > Decimal("0.01"):
            items.append("Gross, tax and net do not add up")

    return make_pack(
        "leave_encash",
        title=f"{who} · {money(enc.net_amount)}".strip(" ·"),
        subtitle="Leave encashment",
        summary=summary,
        checks={"level": "check" if items else "clean", "items": items},
    )
