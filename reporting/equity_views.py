"""reporting/equity_views.py — Equity / Cap Table ("Capital Story") API.

Serves the Alpha Direct Insurtech cap table, instruments, fundraising timeline,
ESOP and a dilution modeller for the omni Accounting › Equity section. Board /
exec-comp data → access is restricted to EXCO / Finance / admin (the cap table
carries founder + named option-holder detail). Off-GL, read-only.

CFO directive 2026-06-19 (Legakwa Ntabeni, "The Capital Story"): build this under
Accounting as an Equity section, structured per Open Cap Format (OCF).
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from . import equity_data

# EXCO / Finance / admin local-parts permitted to view the cap table.
# Env-overridable without a deploy: OMNI_EQUITY_VIEWERS=a,b,c
_EQUITY_VIEWERS = {
    "pganesharajah", "excoboard", "finance", "lntabeni", "ceooffice",
    "internalauditors", "aiyer", "arjuniyer",
}


def _can_view_equity(request) -> bool:
    u = getattr(request, "user", None)
    if not u or not u.is_authenticated:
        return False
    if u.is_superuser or u.is_staff:
        return True
    import os
    extra = {p.strip().lower() for p in os.environ.get("OMNI_EQUITY_VIEWERS", "").split(",") if p.strip()}
    allow = _EQUITY_VIEWERS | extra
    email = (getattr(u, "email", "") or "").lower()
    local = email.split("@")[0] if "@" in email else ""
    uname = (getattr(u, "username", "") or "").lower()
    return local in allow or uname in allow


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def equity_capital_story(request):
    """Full Capital Story payload (OCF-aligned) for the Equity section."""
    if not _can_view_equity(request):
        return Response(
            {"detail": "Equity / cap table is restricted to EXCO and Finance."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return Response(equity_capital_story_payload())


def equity_capital_story_payload() -> dict:
    # Served from the live editable register (equity app); falls back to the
    # seed constants only while the register is empty. Lazy import avoids an
    # import cycle (equity.api_views imports _can_view_equity from this module).
    from equity.api_views import build_capital_story
    return build_capital_story()
