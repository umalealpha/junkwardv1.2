"""The detail pack behind ONE journal entry.

A signer sees every account the entry touches with its debit and credit, the two
totals, and a flag the moment those totals do not balance.
"""
from __future__ import annotations

from decimal import Decimal

from core.approval_pack import make_pack, money


def _row(pk):
    from ledger.models import JournalEntry
    return (JournalEntry.objects
            .select_related("company", "currency_code")
            .prefetch_related("lines__account")
            .filter(pk=pk).first())


def build(pk):
    je = _row(pk)
    if je is None:
        return None

    lines = list(je.lines.all())
    debits = sum((Decimal(l.debit_amount) for l in lines if l.debit_amount), Decimal("0.00"))
    credits = sum((Decimal(l.credit_amount) for l in lines if l.credit_amount), Decimal("0.00"))

    rows = []
    for l in lines:
        acct = " ".join(x for x in (getattr(l.account, "code", ""),
                                    getattr(l.account, "name", "")) if x).strip()
        rows.append([acct, (l.description or "")[:60],
                     money(l.debit_amount, "") if l.debit_amount else "",
                     money(l.credit_amount, "") if l.credit_amount else ""])

    summary = [
        {"label": "Entry", "value": je.entry_number or ""},
        {"label": "Date", "value": str(je.entry_date) if je.entry_date else ""},
        {"label": "Company", "value": getattr(je.company, "name", "")},
        {"label": "Type", "value": je.get_journal_type_display()},
        {"label": "Lines", "value": str(len(lines)) if lines else ""},
        {"label": "Total debits", "value": money(debits)},
        {"label": "Total credits", "value": money(credits)},
        {"label": "Related party", "value": "Yes" if je.is_related_party else ""},
        {"label": "Notes", "value": (je.notes or "")[:200]},
        {"label": "Status", "value": je.get_status_display()},
    ]

    items = []
    if abs(debits - credits) > Decimal("0.01"):
        items.append(f"Debits and credits do not balance — {money(debits)} against "
                     f"{money(credits)}")
    if not je.description:
        items.append("This entry has no narration")

    return make_pack(
        "journal_entries",
        title=f"{je.entry_number or 'Journal'} · {je.description or ''}".strip(" ·"),
        subtitle="Journal entry",
        summary=summary,
        columns=["Account", "Detail", "Debit", "Credit"],
        rows=rows,
        row_total=money(debits),
        checks={"level": "check" if items else "clean", "items": items},
    )
