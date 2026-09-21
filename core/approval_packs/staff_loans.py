"""The detail pack behind ONE staff loan application.

A signer sees the employee, the amount and term, the monthly instalment worked
out from them, and that instalment against the salary on file — which is the
question the CFO actually asks before signing a loan.
"""
from __future__ import annotations

from decimal import Decimal

from core.approval_pack import make_pack, money, pct


def _row(pk):
    from staff_loans.models import StaffLoanApplication
    return (StaffLoanApplication.objects
            .select_related("employee")
            .filter(pk=pk).first())


def build(pk):
    app = _row(pk)
    if app is None:
        return None

    who = getattr(getattr(app, "employee", None), "full_name", "") or "an employee"
    vehicle = " ".join(x for x in (app.vehicle_description, app.vehicle_reg) if x).strip()

    # The model already works this out at the loan's own rate. amount/term
    # ignores the interest and quotes a LOWER instalment than the schedule the
    # employee will actually repay.
    instalment = app.monthly_instalment if (
        app.amount_requested and app.term_months_requested) else None

    summary = [
        {"label": "Employee", "value": who},
        {"label": "Type", "value": app.get_loan_type_display()},
        {"label": "Amount", "value": money(app.amount_requested)},
        {"label": "Term", "value": f"{app.term_months_requested} months"
         if app.term_months_requested else ""},
        {"label": "Interest", "value": pct(app.annual_rate_pct)},
        {"label": "Monthly instalment", "value": money(instalment)},
        {"label": "Monthly salary", "value": money(app.monthly_salary_snapshot)},
        {"label": "Vehicle", "value": vehicle},
        {"label": "Reason", "value": (app.reason or "")[:200]},
        {"label": "Status", "value": app.get_status_display()},
    ]

    items = []
    if instalment is not None and app.monthly_salary_snapshot:
        salary = Decimal(app.monthly_salary_snapshot)
        if salary > 0 and Decimal(instalment) > salary / 3:
            items.append(f"Rule of thumb: the instalment is "
                         f"{int(Decimal(instalment) / salary * 100)}% of their monthly salary")
    if not app.no_other_loans_declared:
        items.append("They have not declared that they hold no other loan")

    return make_pack(
        "staff_loans",
        title=f"{who} · {money(app.amount_requested)}".strip(" ·"),
        subtitle="Staff loan",
        summary=summary,
        checks={"level": "check" if items else "clean", "items": items},
    )
