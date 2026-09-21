"""
integrations/premium_gate.py — Motor-Claims "Gate 0" premium-status classifier.

Deterministic (no AI, no DB): given a claim's date of loss and the policy's
debit-order outcomes, decide whether the premium for the period of loss was
received, per General Condition 3.B (Continuation of cover — debit order):
cover is deemed cancelled at the END of the last paid period if premium is not
received by due date. One SUCCESSFUL debit is taken to buy ``period_days`` of
cover (a monthly debit order => 30 days).

Thresholds are Kago Tshutlhedi's approved memo v2 (6-Sep-2026):
  GREEN  premium_paid    — loss falls within cover bought by the last paid debit.
  AMBER  premium_arrears — premium not received, 1..30 days overdue at the loss.
  RED    premium_lapsed  — more than 30 days overdue.
  RED    premium_unpaid  — no successful debit on/before the loss (unpaid period).

Pure data in, plain dict out — the RealPay/Graphite adapter lives in
``claim_insight.py``, which fetches the debit rows and calls this. Kept free of
Django and the database so it unit-tests anywhere.
"""
from __future__ import annotations

from datetime import date, timedelta

# The one modelling assumption, stated for sign-off: a successful debit buys this
# many days of cover. v1 assumes a monthly debit order => 30 days; annually-paid
# policies are not on RealPay debit orders and get no card. Overdue is measured to
# the DATE OF LOSS (was cover in force when the accident happened), not to today.
DEFAULT_PERIOD_DAYS = 30


def classify_premium_gate(loss_date: date, debits: list[dict], *,
                          period_days: int = DEFAULT_PERIOD_DAYS) -> dict:
    """Classify the premium status for a loss. See module docstring for the rules.

    ``debits``: each item is a dict with ``due_date`` (date), ``status`` (str,
    only 'successful' — case-insensitive — counts as collected) and ``amount``.
    Returns a dict: level (green/amber/red), code, days_overdue, cover_paid_to, label.
    """
    # A debit due AFTER the loss cannot have covered the period of loss.
    eligible = [d for d in debits if d.get("due_date") and d["due_date"] <= loss_date]
    successful = [d for d in eligible
                  if str(d.get("status", "")).strip().lower() == "successful"]

    if not successful:
        return {
            "level": "red",
            "code": "premium_unpaid",
            "days_overdue": None,
            "cover_paid_to": None,
            "label": "No premium received on or before the date of loss — the loss "
                     "falls in an unpaid period, cover treated as cancelled (GC 3.B). "
                     "Do not settle without CFO written override.",
        }

    last_paid = max(d["due_date"] for d in successful)
    cover_end = last_paid + timedelta(days=period_days)

    if loss_date <= cover_end:
        return {
            "level": "green",
            "code": "premium_paid",
            "days_overdue": 0,
            "cover_paid_to": cover_end,
            "label": f"Premium received for the period of loss (cover paid to "
                     f"{cover_end:%d %b %Y}). Normal routing.",
        }

    days_overdue = (loss_date - cover_end).days
    if days_overdue <= 30:
        return {
            "level": "amber",
            "code": "premium_arrears",
            "days_overdue": days_overdue,
            "cover_paid_to": cover_end,
            "label": f"Premium {days_overdue} day(s) overdue at the date of loss "
                     f"(cover paid to {cover_end:%d %b %Y}). Hold settlement until "
                     f"premium received or bank error evidenced (GC 3.B).",
        }
    return {
        "level": "red",
        "code": "premium_lapsed",
        "days_overdue": days_overdue,
        "cover_paid_to": cover_end,
        "label": f"Premium {days_overdue} days overdue at the date of loss "
                 f"(cover paid to {cover_end:%d %b %Y}) — more than 30 days. Do not "
                 f"settle without CFO written override.",
    }
