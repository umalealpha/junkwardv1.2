"""salvage/permissions.py — VCM + ADIC only gate.

CFO directive 2026-05-14: Salvage Portal access is restricted to users
whose company is Veritas Capital Management (VCM) or Alpha Direct
Insurance Company (ADIC). Everyone else gets a clean 403 and the
sidebar entry is hidden client-side.

CR-001 fix 2026-05-28: also honour the topbar company-selector. Users
whose Employee.company FK is null or stale (e.g. legacy ADI row
soft-deleted in PR #104) were being denied even when the selector was
set to ADIC and they had legitimate access to that tenant. Now the
permission passes if the request's active company resolves to ADIC or
VCM AND the user is allowed in that tenant.
"""
from rest_framework.permissions import BasePermission


SALVAGE_COMPANY_CODES = {'VCM', 'ADIC'}


def user_can_access_salvage(user, request=None) -> bool:
    """Single source of truth — used by both the DRF permission class
    and the /api/v1/salvage/me-can-access/ probe the frontend hits to
    decide whether to render the sidebar entry.

    Pass conditions (any one):
      1. user.is_superuser
      2. Employee.company.code ∈ SALVAGE_COMPANY_CODES (canonical path)
      3. UserProfile.title == CFO (group oversight bypass)
      4. ``request`` supplied AND its resolved active company is in
         SALVAGE_COMPANY_CODES AND the user is permitted in that
         tenant via ``allowed_company_ids`` (CR-001 fix).
    """
    from payroll.models import Employee
    from core.models import UserProfile, get_user_profile

    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    emp = Employee.objects.filter(user=user).select_related('company').first()
    if emp and emp.company and emp.company.code in SALVAGE_COMPANY_CODES:
        return True
    profile = get_user_profile(user)
    if profile and profile.title == UserProfile.Title.CFO:
        return True

    # CR-001 — honour the topbar company-selector. Defence-in-depth: only
    # accept the selector value if the user is genuinely permitted in
    # that tenant (so an Employee.company=RSA user can't spoof
    # X-Company-ID: ADIC to gain salvage access).
    if request is not None:
        try:
            from core.mixins import resolve_company_id_param
            from core.models import Company, allowed_company_ids
            cid = resolve_company_id_param(request)
            if cid:
                comp = Company.objects.filter(id=cid).first()
                if comp and comp.code in SALVAGE_COMPANY_CODES:
                    allowed = allowed_company_ids(user)
                    if allowed == {'*'} or str(comp.id) in allowed:
                        return True
        except Exception:    # noqa: BLE001
            pass

    return False


class IsSalvageUser(BasePermission):
    """Allow only signed-in users whose company is VCM or ADIC (or
    superusers, or the CFO, or any user whose topbar selector is on
    ADIC/VCM and who is permitted in that tenant). Used on every
    salvage viewset."""

    message = (
        'Salvage Portal access is restricted to Veritas (VCM) and '
        'Alpha Direct Insurance (ADIC) users.'
    )

    def has_permission(self, request, view):
        return user_can_access_salvage(request.user, request=request)


# ---------------------------------------------------------------------------
# Veritas · Parts & Savings register (CFO 2026-09-10)
# ---------------------------------------------------------------------------
# "This is a register and I want people to actually enter data into this going
# forward, especially the Veritas staff. Bharath is the person who is going to
# manage this. Therefore give him the rights to this particular module in
# full." The module is a MEMORANDUM register — it posts no journal and moves no
# money — so write access is deliberately wide: anyone already allowed into the
# Veritas surface can add and correct lines, and the module manager is named
# here so the right never depends on which company row his employee record
# happens to carry.
PARTS_REGISTER_MANAGERS = {
    'bbalasubramanian@alphadirect.co.bw',    # Bharath Balasubramanian — module owner
}


def user_manages_parts_register(user) -> bool:
    """True for the named module manager(s), by username OR email."""
    if not user or not user.is_authenticated:
        return False
    for value in (getattr(user, 'username', ''), getattr(user, 'email', '')):
        if value and value.strip().lower() in PARTS_REGISTER_MANAGERS:
            return True
    return False


class IsPartsRegisterEditor(BasePermission):
    """Write access to the Parts & Savings register.

    Passes for the named module manager, and for anyone who already has the
    Veritas surface (VCM / ADIC staff, the CFO, superusers). Memorandum data,
    so there is no maker-checker gate here on purpose.
    """

    message = (
        'The Parts & Savings register is open to Veritas (VCM) and Alpha Direct '
        'staff. Ask Bharath Balasubramanian, who manages this module.'
    )

    def has_permission(self, request, view):
        return (user_manages_parts_register(request.user)
                or user_can_access_salvage(request.user, request=request))
