"""
core/permissions.py — shared DRF permission classes.

CanViewFinancials
-----------------
CFO directive 2026-06-20: financial data (P&L, balance sheet, trial balance,
GL, cash, management/audit packs, the CFO dashboard, related-party and the
audit log) is for FINANCE and MANAGEMENT staff — NOT lower-level / operational
staff. Enforcement is by job title via UserProfile.can_view_financials
(superusers/admins always pass). Service API-key callers (Manus / Graphite
integrations) are scoped separately by ApiKeyScopePermission, so they bypass
the title gate here and keep working.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission


class CanViewFinancials(BasePermission):
    # NOTE: frontend/src/lib/api.ts matches this exact string to suppress the
    # spurious "Permission denied" toast for non-finance roles. If you change
    # this message, update that match too (else the toast re-appears).
    message = 'Financial data is restricted to finance and management staff.'

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        if not (user and getattr(user, 'is_authenticated', False)):
            return False
        if getattr(user, 'is_superuser', False):
            return True
        # Service API-key callers are scoped by ApiKeyScopePermission, not titles.
        if getattr(request, 'auth', None) is not None and type(request.auth).__name__ == 'ApiKey':
            return True
        from core.models import get_user_profile
        prof = get_user_profile(user)
        return bool(prof and prof.can_view_financials)


class CanViewRegulatoryReturns(BasePermission):
    """NBFIRA returns + the capital factors behind the Prescribed Capital Target.

    CFO directive 2026-08-17: "allow access to managers and fc and fm only" —
    deliberately TIGHTER than CanViewFinancials, which also lets in accountants,
    bookkeepers, finance analysts, auditors and read-only executives. A filed
    regulatory return is the company's whole financial position as submitted to
    the regulator, and this same gate guards the capital factors, which are
    WRITEABLE.

    Allowed: management (CEO / COO / CFO and the Claims / Operations / HR
    managers) plus the Financial Controller and the Finance Manager.
    Superusers always pass. Service API-key callers are scoped separately by
    ApiKeyScopePermission, as in CanViewFinancials.
    """

    ALLOWED_TITLES = frozenset({
        'ceo', 'coo', 'cfo',                        # management
        'claims_manager', 'operations_manager', 'hr_manager',
        'financial_controller',                     # FC
        'finance_manager',                          # FM
    })

    message = ('NBFIRA returns are restricted to management, the Financial '
               'Controller and the Finance Manager.')

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        if not (user and getattr(user, 'is_authenticated', False)):
            return False
        if getattr(user, 'is_superuser', False):
            return True
        if (getattr(request, 'auth', None) is not None
                and type(request.auth).__name__ == 'ApiKey'):
            return True
        from core.models import get_user_profile
        prof = get_user_profile(user)
        if not (prof and prof.is_active):
            return False
        if getattr(prof, 'is_administrator', False):
            return True
        return prof.title in self.ALLOWED_TITLES


class CanExportEftBatch(BasePermission):
    """EFT batch export (FNB BOL file) gate.

    Security (2026-08-21): EFTBatchExportView had NO permission_classes and fell
    back to the DRF default (IsAuthenticated) — so ANY authenticated staffer
    could pull the bank file, which carries UNMASKED vendor bank account numbers
    and amounts. That is the same raw-payload sensitivity the FNB feeds guard
    with CanManageFNB / _can_manage_fnb.

    Generating an outbound-payment file is a MAKER action, so the allow-set
    matches the outbound-payment views rather than being CFO-only:
      * outbound-payment makers — Financial Controller / Senior Accountant /
        Accountant / system-API (UserProfile.can_originate_controlled_txn),
      * the CFO (the control owner; the CFO SSO account is NOT a maker title and
        NOT a Django superuser) and administrators — i.e. the _can_manage_fnb
        trust level,
      * Django superusers, and service API-key callers (scoped separately by
        ApiKeyScopePermission).

    Blocked: operational / untitled staff, and the checker-only Finance Manager
    (who approves at the bank but must not originate the file — SoD).
    """

    message = ('The bank export file is restricted to outbound-payment makers '
               'and finance management.')

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        if not (user and getattr(user, 'is_authenticated', False)):
            return False
        if getattr(user, 'is_superuser', False):
            return True
        # Service API-key callers are scoped by ApiKeyScopePermission, not titles.
        if (getattr(request, 'auth', None) is not None
                and type(request.auth).__name__ == 'ApiKey'):
            return True
        from core.models import get_user_profile, UserProfile
        prof = get_user_profile(user)
        if not (prof and prof.is_active):
            return False
        # Maker arm (covers system-API automation too).
        if prof.can_originate_controlled_txn:
            return True
        # CFO / administrator arm — same trust level as _can_manage_fnb.
        return bool(prof.is_administrator or prof.title == UserProfile.Title.CFO)


def is_finance_administrator(user) -> bool:
    """Superuser OR UserProfile.is_administrator OR the CFO.

    The CFO's SSO account is NOT a Django superuser, so `is_superuser` alone is
    never the gate. Single canonical copy of the rule that
    ``fnb.api_views._can_manage_fnb`` applies — imported rather than re-written,
    because a second copy is how these gates drift apart.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    from core.models import get_user_profile, UserProfile
    prof = get_user_profile(user)
    if not prof:
        return False
    # A DEACTIVATED profile has no authority left. Omni's "Deactivate user"
    # flips only UserProfile.is_active (not auth.User.is_active), so without
    # this an offboarded finance administrator who could still sign in kept
    # every FNB power (Fable 5.1 audit 2026-09-02, H8).
    if not getattr(prof, 'is_active', True):
        return False
    return bool(prof.is_administrator or prof.title == UserProfile.Title.CFO)


class CanBulkUploadMasterData(BasePermission):
    """Bulk create/update of master data via the /admin/cfo-upload-* family.

    SECURITY FIX (2026-08-25, Manus nine-area retest P1): `cfo_upload_vendors`
    was `IsAuthenticated`, so ANY signed-in staffer could upload a workbook and
    create or patch vendor/customer master rows — in ANY legal entity, because
    the endpoint takes the company as a parameter rather than from the user's
    access. Vendor master data is the front door to payments: a new vendor row
    is the first half of paying someone.

    Allowed:
      * Django superusers,
      * service API-key callers (scoped separately at the authentication layer
        by core.api_key_auth — the established idiom in this module),
      * the `cfo-upload` permission code, i.e. the Bulk Uploader role seeded by
        `manage.py seed_bulk_uploader_role` specifically for this endpoint family,
      * finance administrators / the CFO.

    Blocked: everyone else. Entity scope is a SEPARATE check the view performs
    per company with `core.models.user_can_write_company` — holding this
    permission does not grant every entity.
    """

    message = ('Bulk master-data upload is restricted to the Bulk Uploader role '
               'and finance administrators.')

    def has_permission(self, request, view) -> bool:
        user = getattr(request, 'user', None)
        if not (user and getattr(user, 'is_authenticated', False)):
            return False
        if getattr(user, 'is_superuser', False):
            return True
        if (getattr(request, 'auth', None) is not None
                and type(request.auth).__name__ == 'ApiKey'):
            return True
        try:
            from core.models import UserRoleAssignment
            if UserRoleAssignment.objects.filter(
                user=user, revoked_at__isnull=True, role__is_active=True,
                role__permissions__code='cfo-upload',
            ).exists():
                return True
        except Exception:  # noqa: BLE001
            # Pre-migration / missing table: fall through to the title check
            # rather than silently granting.
            pass
        return is_finance_administrator(user)


def is_the_cfo(user) -> bool:
    """The CFO's own account, and nobody else at all (CFO 2026-09-09).

    His private screens — the build log, the scheduled-job switches. He asked
    for "my eyes only" and chose this over including the shared mailbox when
    the trade-off was put to him.

    Same hard shape as is_cfo_or_ceo below, and for the same reasons: title AND
    the real account, no superuser arm. Worth restating because each arm is
    load-bearing —

      * The shared ``excoboard@`` mailbox ALSO carries the cfo title in Omni.
        A title-only check would hand these pages to everyone who can open that
        mailbox, which is the opposite of what was asked for.
      * Any Omni administrator can re-title a profile, so a title alone is one
        edit away from void.
      * The CFO's SSO account is NOT a Django superuser, while the QC robots and
        the Super Admin ARE — so a superuser arm would admit exactly the wrong
        people and still lock him out.

    Consequence he was told about: signed in as excoboard@ these pages do not
    open. That is the point of them.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    from core.models import get_user_profile, UserProfile
    prof = get_user_profile(user)
    if not (prof and getattr(prof, 'is_active', True)):
        return False
    if prof.title != UserProfile.Title.CFO:
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    uname = (getattr(user, 'username', '') or '').strip().lower()
    return email == 'pganesharajah@alphadirect.co.bw' or uname == 'pganesharajah'


class IsTheCfo(BasePermission):
    """DRF gate for /api/v1/cfo/* — his own account only."""

    message = 'This screen is the CFO\'s own.'

    def has_permission(self, request, view):
        return is_the_cfo(request.user)


def is_cfo_or_ceo(user) -> bool:
    """Express Pay gate (CFO 2026-09-04): the CFO and the CEO, nobody else.

    BOTH arms are tied to the REAL account, not the title alone: the shared
    ``excoboard@`` mailbox also carries the ``cfo`` title in Omni, and any Omni
    administrator (which every cfo-titled profile is) can re-title a profile —
    so a title-only arm is one PATCH away from void. No superuser arm either:
    the CFO's SSO account is not a Django superuser, while the QC robots and
    the Super Admin ARE, so a superuser branch would admit exactly the wrong
    people (independent review, 2026-09-04).

    Why this is enough: Omni does not move money. Express Pay stages ONE payment
    into FNB's queue; it leaves the bank only when the CFO releases the batch in
    the FNB app with his phone's two-factor. That release is the second control.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    from core.models import get_user_profile, UserProfile
    prof = get_user_profile(user)
    if not (prof and getattr(prof, 'is_active', True)):
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    uname = (getattr(user, 'username', '') or '').strip().lower()
    if prof.title == UserProfile.Title.CFO:
        return email == 'pganesharajah@alphadirect.co.bw' or uname == 'pganesharajah'
    if prof.title == UserProfile.Title.CEO:
        return email == 'aiyer@alphadirect.co.bw' or uname == 'arun.iyer'
    return False


class IsCfoOrCeo(BasePermission):
    """DRF wrapper over ``is_cfo_or_ceo`` — the Express Pay endpoints."""
    message = 'Express Pay is for the CFO and the CEO only.'

    def has_permission(self, request, view):
        return is_cfo_or_ceo(request.user)
