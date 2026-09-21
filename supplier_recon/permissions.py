"""Supplier Payables Reconciliation — access control.

Follows omni's title-based model (``core.models.UserProfile.Title``), NOT Django
auth Groups — group membership is not maintained in this deployment, so a
group-based gate would silently lock everyone except superusers out.

Three levels
------------
read      — anyone who may see financials (``UserProfile.can_view_financials``),
            plus the configured board owner.
prepare   — payables staff (build a run, action a bill, raise an escalation),
            plus the configured board owner.
review    — sign off: finalise, reopen, resolve an escalation. Same title set
            that approves journal entries (CFO / FC / FM). **Never** granted by
            board ownership or by the admin flag.

Segregation of duties: whoever built a run cannot finalise it — enforced in
``services.finalise_run`` because it needs the run, not just the request.

Entity scoping: a run belongs to one legal entity and is only visible to users
permitted in that entity (``core.models.allowed_company_ids``). Vendors do not
cross entities — CFO directive 2026-05-18.
"""

from rest_framework.permissions import BasePermission

from core.models import UserProfile, allowed_company_ids, get_user_profile

# Sign-off titles — mirrors UserProfile.APPROVAL_TITLES.
REVIEW_TITLES = frozenset({
    UserProfile.Title.CFO,
    UserProfile.Title.FINANCIAL_CONTROLLER,
    UserProfile.Title.FINANCE_MANAGER,
})

# Payables staff who prepare the board.
PREPARE_TITLES = REVIEW_TITLES | frozenset({
    UserProfile.Title.ACCOUNTANT,
    UserProfile.Title.SENIOR_ACCOUNTANT,
    UserProfile.Title.BOOKKEEPER,
    UserProfile.Title.FINANCE_ANALYST,
})

# CFO directive 2026-08-26: operations staff — and the claims team — may READ the
# supplier recon board (they liaise with payables over it), but this is a
# VIEW-only widening. It is deliberately kept OUT of core's FINANCIALS_VIEW_TITLES
# so it opens THIS module only, not the GL / reports / dashboards. It grants no
# prepare and no sign-off: those stay with finance + the board owner above.
OPS_CLAIMS_VIEW_TITLES = frozenset({
    UserProfile.Title.OPERATIONS,
    UserProfile.Title.OPERATIONS_MANAGER,
    UserProfile.Title.CLAIMS_MANAGER,
    UserProfile.Title.CLAIMS_TEAM_LEADER,
    UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE,
    UserProfile.Title.JUNIOR_CLAIMS_ASSOCIATE,
    UserProfile.Title.CLAIMS_INTERN,
})


def _profile_title(user):
    prof = get_user_profile(user)
    if prof is None or not prof.is_active:
        return None, None
    return prof, prof.title


def _authenticated(user) -> bool:
    return bool(user and getattr(user, 'is_authenticated', False))


def owns_a_board(user) -> bool:
    """Is this user the configured owner of any entity's board?

    The board owner is an OPERATIONS role, not a finance one (Bharath
    Balasubramanian is Operations Manager in training — CFO 2026-07-25), so
    ownership has to carry view + prepare rights on its own. Otherwise
    correcting his omni title from the erroneous `financial_controller` to an
    operations title would lock him out of the board he owns.

    It does NOT carry sign-off: see can_review_recon.
    """
    if not _authenticated(user):
        return False
    from .models import ReconOwner
    return ReconOwner.objects.filter(owner=user).exists()


def can_view_recon(user) -> bool:
    if not _authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    prof, title = _profile_title(user)
    if prof and prof.can_view_financials:
        return True
    # Operations + claims staff may read this module (view only — see set above).
    if title in OPS_CLAIMS_VIEW_TITLES:
        return True
    # The person accountable for the board can always read it.
    return owns_a_board(user)


def can_prepare_recon(user) -> bool:
    if not _authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    prof, title = _profile_title(user)
    if prof is None:
        return False
    if prof.is_administrator:
        return True
    if title in PREPARE_TITLES:
        return True
    # The owner works their own board — actions bills, raises escalations.
    return owns_a_board(user)


def can_review_recon(user) -> bool:
    """Sign-off. Deliberately NOT granted by is_administrator — an IT admin flag
    must not confer financial authority — and NOT granted by board ownership
    either: locking a payables month is a finance act, so it stays with the CFO,
    the Financial Controller and the Finance Manager even when the owner is an
    operations role. The preparer-cannot-sign rule still applies on top."""
    if not _authenticated(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    _, title = _profile_title(user)
    return title in REVIEW_TITLES


def user_company_ids(user) -> set:
    """Entities this user may see. ``{'*'}`` means unrestricted."""
    return allowed_company_ids(user)


def scope_to_allowed_companies(qs, user, field='company_id'):
    """Restrict a queryset to the entities the user is permitted in."""
    allowed = user_company_ids(user)
    if allowed == {'*'}:
        return qs
    if not allowed:
        return qs.none()
    return qs.filter(**{f'{field}__in': allowed})


def can_access_company(user, company_id) -> bool:
    allowed = user_company_ids(user)
    if allowed == {'*'}:
        return True
    return str(company_id) in allowed


class CanViewRecon(BasePermission):
    message = 'Supplier reconciliation is restricted to finance and management staff.'

    def has_permission(self, request, view) -> bool:
        return can_view_recon(request.user)


class CanPrepareRecon(BasePermission):
    """Read for finance viewers; write for payables staff."""

    message = 'You must be payables or finance staff to action a supplier bill.'

    def has_permission(self, request, view) -> bool:
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return can_view_recon(request.user)
        return can_prepare_recon(request.user)


class CanReviewRecon(BasePermission):
    message = ('Only the CFO, Financial Controller or Finance Manager can '
               'finalise, reopen, or close out an escalation.')

    def has_permission(self, request, view) -> bool:
        return can_review_recon(request.user)
