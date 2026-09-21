"""The detail pack behind ONE petty cash voucher.

A signer sees who was paid, how much, out of which float, against which expense
account and for what — and is told when no receipt is attached.
"""
from __future__ import annotations

from core.approval_pack import make_pack, money


def _row(pk):
    from petty_cash.models import PettyCashVoucher
    return (PettyCashVoucher.objects
            .select_related("location", "expense_account")
            .filter(pk=pk).first())


def build(pk):
    v = _row(pk)
    if v is None:
        return None

    acct = v.expense_account
    account = " ".join(x for x in (getattr(acct, "code", ""),
                                   getattr(acct, "name", "")) if x).strip()

    summary = [
        {"label": "Voucher", "value": v.voucher_number or ""},
        {"label": "Location", "value": getattr(v.location, "name", "")},
        {"label": "Date", "value": str(v.voucher_date) if v.voucher_date else ""},
        {"label": "Payee", "value": v.payee or ""},
        {"label": "Amount", "value": money(v.amount)},
        {"label": "Expense account", "value": account},
        {"label": "Receipt", "value": v.receipt_reference or ""},
        {"label": "For", "value": (v.description or "")[:200]},
        {"label": "Status", "value": v.get_status_display()},
    ]

    items = [] if v.receipt_attached else ["No receipt attached to this voucher"]

    return make_pack(
        "petty_cash",
        title=f"{v.payee or 'Petty cash'} · {money(v.amount)}".strip(" ·"),
        subtitle="Petty cash voucher",
        summary=summary,
        checks={"level": "check" if items else "clean", "items": items},
    )
