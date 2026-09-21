"""The detail pack behind ONE authority to recruit.

A signer sees the post, the entity and department it sits in, how many heads,
when it starts, the monthly and annual cost to company, and the salary build-up
when one was quoted — so the cost of the hire is on the same screen as the
signature.
"""
from __future__ import annotations

from core.approval_pack import make_pack, money

#: `salary_lines` is free-form JSON written by the requisition screen, so read
#: it by the keys it actually uses rather than assuming one shape.
_LABEL_KEYS = ("label", "name", "component", "title")
_AMOUNT_KEYS = ("amount", "value", "monthly")


def _row(pk):
    from recruitment.models import AuthorityToRecruit
    return AuthorityToRecruit.objects.filter(pk=pk).first()


def _first(d, keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return ""


def build(pk):
    a = _row(pk)
    if a is None:
        return None

    cur = a.currency or "BWP"
    kind = ""
    try:
        kind = a.get_kind_display()
    except Exception:  # noqa: BLE001 — display helpers are optional context
        kind = getattr(a, "kind", "") or ""

    summary = [
        {"label": "Reference", "value": a.reference or ""},
        {"label": "Kind", "value": kind},
        {"label": "Position", "value": a.position or ""},
        {"label": "Person", "value": a.person_name or ""},
        {"label": "Department", "value": a.department or ""},
        {"label": "Entity", "value": a.entity or ""},
        {"label": "Level", "value": a.level or ""},
        {"label": "Employment type", "value": a.employment_type or ""},
        {"label": "Headcount", "value": str(a.headcount) if a.headcount else ""},
        {"label": "Start", "value": str(a.effective_date) if a.effective_date else ""},
        {"label": "Monthly cost to company", "value": money(a.quoted_ctc_monthly, cur)},
        {"label": "Annual cost", "value": money(a.quoted_ctc_annual, cur)},
        {"label": "Hiring manager", "value": a.hiring_manager_name or ""},
        {"label": "Why", "value": (a.justification or "")[:200]},
        {"label": "Status", "value": a.get_status_display()},
    ]

    rows, columns = [], []
    lines = a.salary_lines if isinstance(a.salary_lines, list) else []
    for entry in lines:
        if not isinstance(entry, dict):
            continue
        rows.append([_first(entry, _LABEL_KEYS), money(_first(entry, _AMOUNT_KEYS), "")])
    if rows:
        columns = ["Item", "Amount"]

    items = []
    if not a.justification:
        items.append("No justification given")
    if not a.quoted_ctc_monthly and not a.quoted_ctc_annual:
        items.append("No cost to company quoted")

    heads = f"{a.headcount} post(s)" if a.headcount else ""
    return make_pack(
        "authority_to_recruit",
        title=f"{a.position or 'A post'} · {a.person_name or heads}".strip(" ·"),
        subtitle=f"Authority to recruit {a.reference or ''}".strip(),
        summary=summary,
        columns=columns,
        rows=rows,
        row_total=money(a.quoted_ctc_monthly, cur),
        checks={"level": "check" if items else "clean", "items": items},
    )
