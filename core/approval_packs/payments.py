"""The detail pack behind ONE payment awaiting approval.

A signer sees who is being paid, into which account, how much, what for, and the
existing one-line payment brief (Nth payment to this payee this month, how it
compares with their usual) — plus a flag when the payee has no account number or
the payment has no description.
"""
from __future__ import annotations

from decimal import Decimal

from core.approval_pack import make_pack, money


def _row(pk):
    from payments.models import Payment
    return (Payment.objects
            .select_related("contact", "company", "currency_code")
            .filter(pk=pk).first())


def build(pk):
    p = _row(pk)
    if p is None:
        return None

    # Currency.__str__ is "BWP - Pula"; the id IS the code.
    cur = p.currency_code_id or "BWP"
    payee = (p.payee_name if p.is_once_off
             else getattr(p.contact, "name", "")) or ""
    bank = " ".join(x for x in (p.payee_bank_name, p.payee_account_number) if x).strip()

    in_bwp = ""
    if p.amount_bwp is not None and p.amount is not None \
            and Decimal(p.amount_bwp) != Decimal(p.amount):
        in_bwp = money(p.amount_bwp)

    summary = [
        {"label": "Payment no", "value": p.payment_number or ""},
        {"label": "Payee", "value": payee},
        {"label": "Bank / account", "value": bank},
        {"label": "Date", "value": str(p.payment_date) if p.payment_date else ""},
        {"label": "Method", "value": p.get_payment_method_display()},
        {"label": "Amount", "value": money(p.amount, cur)},
        {"label": "Amount in BWP", "value": in_bwp},
        {"label": "Company", "value": getattr(p.company, "name", "")},
        {"label": "Reference", "value": p.reference or ""},
        {"label": "For", "value": (p.description or "")[:200]},
    ]

    items = []
    if not p.payee_account_number:
        items.append("No payee bank account number on this payment")
    if not p.description:
        items.append("No description — nobody can tell what this is for")

    # The one-liner the decision sheet already shows. Optional context only.
    note = ""
    try:
        from core.approvals_views import _payment_brief
        note = _payment_brief(pk) or ""
    except Exception:  # noqa: BLE001
        pass

    return make_pack(
        "payments",
        title=f"{payee} · {money(p.amount, cur)}".strip(" ·"),
        subtitle="Payment for approval",
        summary=summary,
        checks={"level": "check" if items else "clean", "items": items},
        note=note,
    )
