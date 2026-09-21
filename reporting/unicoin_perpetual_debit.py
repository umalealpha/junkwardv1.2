"""Perpetual / consent-breaching debit detector (A2).

Pack Task 4. A once-off plan carries exactly one instalment, a three-month plan
exactly three; neither may roll into the following policy year. Renewal is the
client's decision, so a debit past the agreed plan is a CONSENT issue, not a
reconciliation nicety.

Pure rule, so it can be unit-proven off the box. The caller supplies the counts
from the live data: ``collected_instalments`` MUST be the count of SUCCESS rows
only (status S), or a failed-then-retried cycle inflates the count and flags a
compliant plan.
"""
from __future__ import annotations

#: The instalment count each named plan is allowed. Stated in the pack.
PLAN_SIZES = {"once_off": 1, "three_month": 3}


def flag_perpetual_debit(*, plan_instalments, collected_instalments,
                         mandate_active, plan_end_date, today) -> dict:
    """Whether this contract is debiting past its agreed plan.

    Flags if more instalments collected than the plan allows, OR if an active
    mandate is still live past the plan end date. ``today == plan_end_date`` is
    NOT past the plan, so it is not flagged. ``plan_end_date`` None means the end
    is unknown, so the date test is skipped rather than crashing.

    Returns {"flagged": bool, "reason": str}.
    """
    if collected_instalments > plan_instalments:
        return {
            "flagged": True,
            "reason": (f"{collected_instalments} instalments collected against an "
                       f"agreed plan of {plan_instalments}"),
        }

    if mandate_active and plan_end_date is not None and today > plan_end_date:
        return {
            "flagged": True,
            "reason": (f"mandate still active past the plan end date "
                       f"{plan_end_date.isoformat()}"),
        }

    return {"flagged": False, "reason": ""}
