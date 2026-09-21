"""FX Payment Planning — access control.

Follows omni's title-based model (``core.models.UserProfile.Title``), the same
approach as supplier_recon: group membership is not maintained in this
deployment, so a group gate would lock everyone out.

* **view**  — anyone who may see financials (``can_view_financials``). This is a
  treasury/finance planning view, no customer PII.
* **manage** — import a download, rebuild the forecast, edit a payee, add/skip a
  planned line, raise a payment request. Finance staff + CFO.
"""

from rest_framework.permissions import BasePermission

from core.models import UserProfile, get_user_profile

MANAGE_TITLES = frozenset({
    UserProfile.Title.CFO,
    UserProfile.Title.FINANCIAL_CONTROLLER,
    UserProfile.Title.FINANCE_MANAGER,
    UserProfile.Title.ACCOUNTANT,
    UserProfile.Title.SENIOR_ACCOUNTANT,
    UserProfile.Title.BOOKKEEPER,
    UserProfile.Title.FINANCE_ANALYST,
})


def _authenticated(user) -> bool:
    return bool(user and getattr(user, 'is_authenticated', False))


def can_view_fx_planning(user) -> bool:
    if not _authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    prof = get_user_profile(user)
    return bool(prof and prof.is_active and prof.can_view_financials)


def can_manage_fx_planning(user) -> bool:
    if not _authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    prof = get_user_profile(user)
    return bool(prof and prof.is_active and prof.title in MANAGE_TITLES)


class CanViewFxPlanning(BasePermission):
    message = 'You do not have access to FX payment planning.'

    def has_permission(self, request, view):
        return can_view_fx_planning(request.user)


class CanManageFxPlanning(BasePermission):
    message = 'Only Finance and the CFO can change FX planning.'

    def has_permission(self, request, view):
        return can_manage_fx_planning(request.user)
