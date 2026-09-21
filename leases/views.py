"""
leases/views.py — IFRS 16 lease register API.

  GET/POST         /api/v1/leases/                list + add a property
  GET/PATCH/DELETE /api/v1/leases/<id>/           view / edit / remove a lease
  GET              /api/v1/leases/<id>/schedule/  full amortisation + FY summary
  POST             /api/v1/leases/compute/        live what-if — compute from raw
                                                   inputs WITHOUT saving (the
                                                   "drag the escalation" recompute)

Viewing is open to any authenticated staff member; adding / editing / removing a
lease is Finance-only (the register drives the GL).
"""
from __future__ import annotations

import math
from datetime import date

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .engine import compute_lease
from .models import Lease
from .serializers import LeaseSerializer, _lease_inputs


FINANCE_EMAILS = {"pkago@alphadirect.co.bw", "ktshutlhedi@alphadirect.co.bw"}  # Pako Kago, Kago Tshutlhedi


def _can_manage_leases(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:                 # NOT is_staff — that's only admin-site access, not a finance role
        return True
    try:
        from core.models import UserProfile, get_user_profile
        p = get_user_profile(user)
        if p and p.is_active and p.title in {
            UserProfile.Title.CFO, UserProfile.Title.FINANCE_MANAGER,
            UserProfile.Title.FINANCIAL_CONTROLLER,
        }:
            return True
    except Exception:  # noqa: BLE001
        pass
    # Match the FULL address, never just the local-part (pkago@gmail.com must NOT pass).
    return (getattr(user, "email", "") or "").strip().lower() in FINANCE_EMAILS


def _round(v):
    return round(v, 2) if isinstance(v, float) else v


def _round_deep(obj):
    if isinstance(obj, dict):
        return {k: _round_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_deep(x) for x in obj]
    return _round(obj)


class LeaseViewSet(viewsets.ModelViewSet):
    serializer_class = LeaseSerializer
    permission_classes = [IsAuthenticated]
    queryset = Lease.objects.all()

    def _guard_write(self):
        if not _can_manage_leases(self.request.user):
            return Response({"detail": "Adding or changing a lease is restricted to Finance."},
                            status=status.HTTP_403_FORBIDDEN)
        return None

    def create(self, request, *args, **kwargs):
        return self._guard_write() or super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return self._guard_write() or super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return self._guard_write() or super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return self._guard_write() or super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["get"])
    def schedule(self, request, pk=None):
        obj = self.get_object()
        try:
            return Response(_round_deep(compute_lease(**_lease_inputs(obj))))
        except (TypeError, ValueError, ArithmeticError) as exc:
            return Response({"detail": f"This lease can't be computed: {exc}"},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @action(detail=False, methods=["post"])
    def compute(self, request):
        """Live what-if: compute the full schedule from raw inputs, no save."""
        d = request.data
        if not isinstance(d, dict):
            return Response({"detail": "Expected a JSON object of inputs."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            commence = date.fromisoformat(str(d.get("commencement_date") or "")[:10])
            nums = {
                "monthly_payment": float(d.get("monthly_payment") or 0),
                "discount_rate_pct": float(d.get("discount_rate_pct") or 0),
                "escalation_pct": float(d.get("escalation_pct") or 0),
                "incentives": float(d.get("incentives") or 0),
                "initial_direct_costs": float(d.get("initial_direct_costs") or 0),
                "prepaid": float(d.get("prepaid") or 0),
                "dismantle": float(d.get("dismantle") or 0),
            }
            term = int(d.get("term_months") or 0)
            # distinguish "not provided" (default 6) from a provided 0 (invalid) —
            # `or 6` would silently turn a provided 0 into 6.
            raw_fye = d.get("fye_month")
            fye = int(raw_fye) if raw_fye not in (None, "") else 6
            timing = d.get("payment_timing") or "arrears"
        except (TypeError, ValueError) as exc:
            return Response({"detail": f"Check the inputs: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
        # Reject values that would render as invalid JSON (inf/nan) or blow up /
        # pin a worker (absurd term, out-of-range FYE, huge rate).
        if not all(math.isfinite(v) for v in nums.values()):
            return Response({"detail": "Numbers must be finite."}, status=status.HTTP_400_BAD_REQUEST)
        if not (1 <= term <= 1200):
            return Response({"detail": "Term must be between 1 and 1200 months."}, status=status.HTTP_400_BAD_REQUEST)
        if not (1 <= fye <= 12):
            return Response({"detail": "Financial year-end month must be 1–12."}, status=status.HTTP_400_BAD_REQUEST)
        if timing not in ("arrears", "advance"):
            return Response({"detail": "Timing must be 'arrears' or 'advance'."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            out = compute_lease(commencement_date=commence, term_months=term,
                                fye_month=fye, payment_timing=timing, **nums)
        except (TypeError, ValueError, ArithmeticError) as exc:
            return Response({"detail": f"Check the inputs: {exc}"}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_round_deep(out))
