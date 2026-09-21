"""
leases/engine.py — the IFRS 16 computation (single source of truth).

Pure functions, no DB. Given the lease inputs it returns the initial lease
liability (PV of the escalating payments), the right-of-use asset at cost, the
straight-line depreciation, the month-by-month amortisation + depreciation
schedules, and a summary by financial year (payments / interest / principal /
closing liability / current + non-current split / depreciation / NBV).

VERIFIED to the cent against Kago's "IFRS 16 ALPHA — Corrected FY26" workbook
(BIH ICON: initial liability 4,308,084.59; ROU 374,283.59; dep 6,238.06/mo;
30-Jun-2026 current 991,128.07 / non-current 718,340.03; total interest
807,967.88). Float arithmetic mirrors the Excel model; callers round for display.
"""
from __future__ import annotations

from datetime import date


def _month_add(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def _fy_of(d: date, fye_month: int) -> int:
    """The financial year (labelled by the year its FYE falls in) that month `d`
    belongs to. FYE month 6 → Jul-2025..Jun-2026 is FY2026."""
    return d.year if d.month <= fye_month else d.year + 1


def compute_lease(*, monthly_payment, discount_rate_pct, term_months, escalation_pct,
                  commencement_date: date, fye_month: int = 6,
                  incentives=0.0, initial_direct_costs=0.0, prepaid=0.0, dismantle=0.0,
                  payment_timing: str = "arrears") -> dict:
    base = float(monthly_payment)
    i = float(discount_rate_pct) / 100.0 / 12.0
    esc = float(escalation_pct) / 100.0
    n = int(term_months)
    fye_month = int(fye_month)
    advance = (payment_timing == "advance")

    def payment(m: int) -> float:               # m is 1-indexed
        return base * ((1 + esc) ** ((m - 1) // 12))

    # Initial lease liability = PV of the lease payments. In arrears the first
    # payment discounts one full period; in advance it discounts m-1 periods.
    liab0 = sum(payment(m) / ((1 + i) ** (m - 1 if advance else m)) for m in range(1, n + 1))

    rou_cost = liab0 + float(initial_direct_costs) + float(prepaid) + float(dismantle) - float(incentives)
    dep = rou_cost / n if n else 0.0

    schedule = []
    opening = liab0
    accum_dep = 0.0
    for m in range(1, n + 1):
        pay = payment(m)
        # In advance the payment is made at the START of the month, so interest
        # accrues on the balance AFTER that payment (annuity-due); in arrears it
        # accrues on the full opening balance. Either way the liability movement
        # is (pay - interest) and it unwinds to ~0 at the end of the term.
        interest = ((opening - pay) if advance else opening) * i
        closing = opening + interest - pay
        principal = pay - interest
        accum_dep += dep
        d = _month_add(commencement_date, m - 1)
        schedule.append({
            "m": m, "date": d.isoformat(), "fy": _fy_of(d, fye_month),
            "payment": pay, "opening": opening, "interest": interest,
            "closing": closing, "principal": principal,
            "depreciation": dep, "accum_dep": accum_dep,
            "carrying": max(rou_cost - accum_dep, 0.0),
        })
        opening = closing

    # Summary by financial year, built in ONE pass (not O(n²)). Current portion at
    # each FYE = the principal to be repaid in the FOLLOWING 12 months (next FY).
    agg: dict[int, dict] = {}
    principal_by_fy: dict[int, float] = {}
    for r in schedule:
        fy = r["fy"]
        principal_by_fy[fy] = principal_by_fy.get(fy, 0.0) + r["principal"]
        a = agg.get(fy)
        if a is None:
            a = agg[fy] = {"payments": 0.0, "interest": 0.0, "principal": 0.0,
                           "depreciation": 0.0, "closing": 0.0, "nbv": 0.0}
        a["payments"] += r["payment"]
        a["interest"] += r["interest"]
        a["principal"] += r["principal"]
        a["depreciation"] += r["depreciation"]
        a["closing"] = r["closing"]      # last row of the FY wins
        a["nbv"] = r["carrying"]
    fy_summary = []
    for fy in sorted(agg):
        a = agg[fy]
        closing = a["closing"]
        current = min(principal_by_fy.get(fy + 1, 0.0), max(closing, 0.0))
        fy_summary.append({
            "fy": fy,
            "payments": a["payments"], "interest": a["interest"], "principal": a["principal"],
            "closing_liability": closing, "current_portion": current,
            "non_current": max(closing - current, 0.0),
            "depreciation": a["depreciation"], "nbv": a["nbv"],
        })

    return {
        "initial_liability": liab0,
        "rou_cost": rou_cost,
        "depreciation_per_month": dep,
        "total_payments": sum(r["payment"] for r in schedule),
        "total_interest": sum(r["interest"] for r in schedule),
        "schedule": schedule,
        "fy_summary": fy_summary,
    }
