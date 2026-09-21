"""watchdog/rotation.py — the 7-day deep-check rotation, verbatim from the CFO's
2026-08-21 plan. Every module gets a deep check once a week; the always-on checks
(business rules, cross-module integrity, bug board, machine-talk) run EVERY night
on top of the day's focus.

Weekday is Python's date.weekday(): Monday == 0 ... Sunday == 6.
Module keys are the Django app labels the checks tag themselves with.
"""
from __future__ import annotations

# weekday -> (human label, [module keys]).  "*" == full system sweep.
ROTATION: dict[int, tuple[str, tuple[str, ...]]] = {
    0: ("Finance Core",
        ("ledger", "reporting", "budgets", "reconciliation_hub", "fx",
         "investments", "leases")),
    1: ("Procurement & Payments",
        ("procurement", "payments", "supplier_recon", "billing",
         "customer_refunds", "commissions")),
    2: ("HR & People",
        ("hris", "payroll", "staff_loans", "recruitment", "rewards", "taskboard")),
    3: ("Insurance Operations",
        ("claims", "reinsurance", "underwriting", "healthcare", "salvage")),
    4: ("Banking & Cash",
        ("fnb", "realpay", "bank_feeds", "petty_cash", "assets")),
    5: ("Compliance & Support",
        ("internal_audit", "nbfira", "iso_compliance", "regulatory", "exceptions")),
    6: ("Full System Sweep", ("*",)),
}

# Categories that run every single night regardless of the day's focus. These are
# the CFO's "every night" promises: validate business rules, check modules talk to
# each other, read the bug board, read MACHINE-TALK.
ALWAYS_ON_CATEGORIES = ("integrity", "business_rule", "bug", "machine_talk")


def focus_for(weekday: int) -> tuple[str, tuple[str, ...]]:
    """(label, modules) for a weekday. Unknown weekday -> full sweep (safe default)."""
    return ROTATION.get(weekday, ("Full System Sweep", ("*",)))


def is_full_sweep(modules: tuple[str, ...]) -> bool:
    return "*" in modules
