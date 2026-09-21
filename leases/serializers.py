"""leases/serializers.py — Lease CRUD + a light computed headline for the list."""
from __future__ import annotations

from datetime import date

from rest_framework import serializers

from .engine import compute_lease
from .models import Lease
from django.utils import timezone


def _lease_inputs(obj: Lease) -> dict:
    return dict(
        monthly_payment=obj.monthly_payment, discount_rate_pct=obj.discount_rate_pct,
        term_months=obj.term_months, escalation_pct=obj.escalation_pct,
        commencement_date=obj.commencement_date, fye_month=obj.fye_month,
        incentives=obj.incentives, initial_direct_costs=obj.initial_direct_costs,
        prepaid=obj.prepaid, dismantle=obj.dismantle, payment_timing=obj.payment_timing,
    )


class LeaseSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Lease
        fields = [
            "id", "name", "property_ref", "company",
            "commencement_date", "term_months", "monthly_payment",
            "escalation_pct", "discount_rate_pct", "fye_month", "payment_timing",
            "incentives", "initial_direct_costs", "prepaid", "dismantle", "notes",
            "created_at", "updated_at", "summary",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "summary"]

    def validate(self, attrs):
        """Bound the inputs so a saved lease can never be degenerate (0-month,
        absurd term, out-of-range FYE/rate) or produce a negative ROU — which
        would give nonsense schedules / a DoS-slow list. Supports partial update
        by falling back to the existing instance value."""
        from decimal import Decimal

        def g(k, default=None):
            return attrs[k] if k in attrs else getattr(self.instance, k, default)

        errs = {}
        mp = g("monthly_payment")
        if mp is not None and Decimal(str(mp)) <= 0:
            errs["monthly_payment"] = "Monthly payment must be greater than zero."
        term = g("term_months")
        if term is not None and not (1 <= int(term) <= 1200):
            errs["term_months"] = "Term must be between 1 and 1200 months."
        fye = g("fye_month")
        if fye is not None and not (1 <= int(fye) <= 12):
            errs["fye_month"] = "Financial year-end month must be 1–12."
        for k, label in (("escalation_pct", "Escalation"), ("discount_rate_pct", "Borrowing rate")):
            v = g(k)
            if v is not None and not (Decimal("0") <= Decimal(str(v)) <= Decimal("100")):
                errs[k] = f"{label} must be between 0 and 100 %."
        for k in ("incentives", "initial_direct_costs", "prepaid", "dismantle"):
            v = g(k)
            if v is not None and Decimal(str(v)) < 0:
                errs[k] = "Cannot be negative."
        timing = g("payment_timing")
        if timing is not None and timing not in {c[0] for c in Lease.Timing.choices}:
            errs["payment_timing"] = "Must be 'arrears' or 'advance'."
        if errs:
            raise serializers.ValidationError(errs)

        # Right-of-use asset must not go negative (incentives exceeding the PV of
        # the lease is a data-entry error under IFRS 16).
        try:
            r = compute_lease(
                monthly_payment=mp, discount_rate_pct=g("discount_rate_pct", 7),
                term_months=term or 60, escalation_pct=g("escalation_pct", 0),
                commencement_date=g("commencement_date"), fye_month=fye or 6,
                incentives=g("incentives", 0), initial_direct_costs=g("initial_direct_costs", 0),
                prepaid=g("prepaid", 0), dismantle=g("dismantle", 0),
                payment_timing=timing or "arrears",
            )
            if r["rou_cost"] < -0.01:
                raise serializers.ValidationError(
                    {"incentives": "Incentives exceed the lease liability — the right-of-use "
                                   "asset would be negative. Check the figures."})
        except (TypeError, ValueError, ArithmeticError):
            pass   # bad shape already covered above / by field validators
        return attrs

    def get_summary(self, obj):
        try:
            r = compute_lease(**_lease_inputs(obj))
        except Exception:  # noqa: BLE001 — a bad row must not blank the whole list
            return None
        # the FY the reporting date falls in (today), else the last FY
        today = timezone.localdate()
        this_fy = today.year if today.month <= obj.fye_month else today.year + 1
        cur = next((f for f in r["fy_summary"] if f["fy"] == this_fy), None)
        return {
            "initial_liability": round(r["initial_liability"], 2),
            "rou_cost": round(r["rou_cost"], 2),
            "depreciation_per_month": round(r["depreciation_per_month"], 2),
            "total_interest": round(r["total_interest"], 2),
            "this_fy": this_fy,
            "closing_liability": round(cur["closing_liability"], 2) if cur else None,
            "current_portion": round(cur["current_portion"], 2) if cur else None,
            "non_current": round(cur["non_current"], 2) if cur else None,
            "fy_interest": round(cur["interest"], 2) if cur else None,
            "fy_depreciation": round(cur["depreciation"], 2) if cur else None,
            "nbv": round(cur["nbv"], 2) if cur else None,
        }
