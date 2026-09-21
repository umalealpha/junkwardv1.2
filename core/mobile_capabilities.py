"""core/mobile_capabilities.py — server-truth mobile capability manifest.

Omni Mobile, Workstream A.

The phone must NEVER decide what a user may see or do from a job-title string, a
menu guess, or a successful data probe. This module computes one
``{capability: bool}`` dict from the SAME server-side access rules that already
guard each feature, and it is attached to the authenticated ``/me`` response
(``core.serializers.UserProfileMeSerializer``). The mobile client renders its
Home / Work / Inbox / People / Me surfaces from these booleans.

Every capability is derived from an EXISTING gate — nothing new is granted here:

  * ``core.models.UserProfile`` title/property gates  → finance + payroll
  * ``core.permissions.is_finance_administrator``      → FNB
  * ``bonu.access``                                    → legal / BONU
  * ``core.hris_access.hris_role`` + ``ROLE_CAPABILITIES`` → HR tiers, team, feedback
  * ``recruitment.authority_access.can_view``          → recruitment
  * ``hris.document_access._is_csuite``                → executive company pulse
  * ``core.models.user_has_permission`` (RBAC codes)  → department workspaces
    (claims / underwriting / management pack)

Design rules:
  * **Fail closed.** No user / anonymous / no profile ⇒ every action False.
  * **The locked read-only QC / screenshot identity** keeps VIEW capabilities
    (seeing a screen is the whole point of QC) but has every ACTION/WRITE
    capability forced False. It is a Django superuser, so several underlying
    gates would otherwise answer True — the exact failure seen live on
    2026-07-29 when the QC viewer advertised ``can_approve_payroll`` /
    ``can_administer_users`` as True.
  * **Read-only.** This module mirrors what the enforcing endpoints already
    decide; it never itself grants access. Each real endpoint re-checks.
"""
from __future__ import annotations

# Canonical capability keys — the server, the tests and the mobile client
# (frontend/src/app/(app)/capabilities.ts) must all agree on this exact set.
MOBILE_CAPABILITY_KEYS = (
    'view_personal_home',
    'manage_team',
    'give_monthly_feedback',
    'view_all_feedback',
    'view_claims_workspace',
    'capture_claims_action',
    'view_underwriting_workspace',
    'issue_underwriting_documents',
    'view_bonu',
    'capture_bonu_claim',
    'edit_bonu_legal',
    'view_finance_workspace',
    'create_finance_transaction',
    'approve_finance_transaction',
    'manage_fnb',
    'view_hr_workspace',
    'manage_leave',
    'manage_payroll',
    'manage_recruitment',
    'view_executive_dashboard',
)

# Capabilities that represent an ACTION / WRITE. Denied to the read-only QC /
# screenshot identity even though it is a Django superuser.
_ACTION_KEYS = frozenset({
    'capture_claims_action',
    'issue_underwriting_documents',
    'capture_bonu_claim',
    'edit_bonu_legal',
    'create_finance_transaction',
    'approve_finance_transaction',
    'manage_fnb',
    'give_monthly_feedback',  # writing a feedback record is an action
    'manage_leave',
    'manage_payroll',
    'manage_recruitment',
})


def empty_mobile_capabilities() -> dict:
    """Every capability False — used for the no-profile / no-authority payload."""
    return {k: False for k in MOBILE_CAPABILITY_KEYS}


def build_mobile_capabilities(user) -> dict:
    """Return ``{capability: bool}`` for ``user``, from existing server gates."""
    caps = empty_mobile_capabilities()
    if user is None or not getattr(user, 'is_authenticated', False):
        return caps

    from core.models import (
        get_user_profile,
        UserProfile,
        user_has_permission,
        is_payroll_processor,
    )
    from core.hris_access import hris_role, ROLE_CAPABILITIES
    from core.permissions import is_finance_administrator
    from bonu.access import can_view_bonu, can_capture_claim

    prof = get_user_profile(user)
    is_super = bool(getattr(user, 'is_superuser', False))
    # is_active REQUIRED: Omni's "Deactivate user" flips only UserProfile.is_active,
    # so a deactivated administrator must not keep phone capabilities (repeat of the
    # 2026-09-02 H8 hole closed in is_finance_administrator).
    is_admin = bool(prof and getattr(prof, 'is_administrator', False) and prof.is_active)
    title = getattr(prof, 'title', None) if prof else None
    read_only = bool(prof and prof.is_read_only_identity)
    T = UserProfile.Title

    # ---- Personal home — any active authenticated staff session ----
    caps['view_personal_home'] = (
        bool(getattr(user, 'is_active', False)) and (prof is None or prof.is_active)
    )

    # ---- HR tier capabilities (mirror the ROLE_CAPABILITIES bitmap) ----
    role_caps = ROLE_CAPABILITIES.get(hris_role(user), set())
    caps['manage_team'] = 'view_team' in role_caps
    caps['give_monthly_feedback'] = 'assess_team' in role_caps
    caps['view_all_feedback'] = 'view_all' in role_caps
    caps['view_hr_workspace'] = 'view_all' in role_caps
    caps['manage_leave'] = 'manage_leave_admin' in role_caps

    # ---- Finance ----
    if prof is not None:
        caps['view_finance_workspace'] = prof.can_view_financials
        caps['create_finance_transaction'] = (
            prof.can_create_journal_entries or prof.can_originate_controlled_txn
        )
        caps['approve_finance_transaction'] = (
            prof.can_approve_journal_entries or prof.can_check_controlled_txn
        )
    elif is_super:
        caps['view_finance_workspace'] = True

    caps['manage_fnb'] = is_finance_administrator(user)

    # ---- Payroll (processor OR approver OR HRIS payroll capability) ----
    caps['manage_payroll'] = (
        'manage_payroll' in role_caps
        or bool(prof and prof.can_approve_payroll)
        or is_payroll_processor(user)
    )

    # ---- BONU / legal (read == write for legal, per bonu/views.py) ----
    caps['view_bonu'] = can_view_bonu(user)
    caps['capture_bonu_claim'] = can_capture_claim(user)
    caps['edit_bonu_legal'] = can_view_bonu(user)

    # ---- Recruitment ----
    # recruitment is unconditional in INSTALLED_APPS, so import + call directly
    # (same pattern as the bonu / hris gates above); a real error surfaces rather
    # than being swallowed into a wrong "no access" (checklist H6 / L12).
    from recruitment.authority_access import can_view as _rec_can_view
    caps['manage_recruitment'] = bool(_rec_can_view(user))

    # ---- Claims workspace (claims-grade titles OR RBAC claim.view) ----
    claims_titles = {
        T.CLAIMS_MANAGER, T.CLAIMS_TEAM_LEADER, T.SENIOR_CLAIMS_ASSOCIATE,
        T.JUNIOR_CLAIMS_ASSOCIATE, T.CLAIMS_INTERN,
    } if prof is not None else set()
    caps['view_claims_workspace'] = bool(
        is_super or is_admin
        or (title in claims_titles)
        or user_has_permission(user, 'claim.view')
    )
    # Capture = claims grades except the intern, or an RBAC create/adjust grant.
    capture_titles = claims_titles - {T.CLAIMS_INTERN} if prof is not None else set()
    caps['capture_claims_action'] = bool(
        caps['view_claims_workspace'] and (
            is_super or is_admin
            or (title in capture_titles)
            or user_has_permission(user, 'claim.create')
            or user_has_permission(user, 'claim.adjust')
        )
    )

    # ---- Underwriting workspace ----
    # Reality check (Fable WS-A review): NO view enforces uw.view / uw.quote —
    # UnderwritingDocumentViewSet + QuoteViewSet are IsAuthenticated + company-
    # scoped, so every active signed-in staffer can already reach Quotes today.
    # Gating the mobile tile on the (unassigned) RBAC uw.* codes would HIDE the
    # live Quotes tool from the ops/sales users who use it. So mirror the real
    # gate — any active staff member (i.e. has a profile). The profile-less edge
    # accounts are excluded (they have no company access anyway, and the /me
    # endpoint already returns the all-false no-profile payload for them).
    # Workstream C introduces a dedicated underwriting role and tightens this;
    # the issue endpoint still enforces per-company write, and QC loses 'issue'.
    uw_staff = bool(is_super or (prof is not None and prof.is_active))
    caps['view_underwriting_workspace'] = uw_staff
    caps['issue_underwriting_documents'] = uw_staff

    # ---- Executive dashboard / company pulse ----
    from hris.document_access import _is_csuite
    exec_titles = {T.CEO, T.COO, T.CFO, T.EXECUTIVE} if prof is not None else set()
    caps['view_executive_dashboard'] = bool(
        is_super or is_admin or _is_csuite(user)
        or (title in exec_titles)
        or user_has_permission(user, 'report.management.view')
    )

    # ---- Read-only QC / screenshot identity: keep VIEW, drop every ACTION ----
    if read_only:
        for k in _ACTION_KEYS:
            caps[k] = False

    return caps
