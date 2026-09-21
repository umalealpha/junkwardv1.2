"""core/access_delegate.py — the limited access administrator.

CFO directive 2026-09-15: *"Unopa will be managing access going forward, he
can't give manager level access which is mine, he can't give super user access,
he can't give access to payroll, otherwise he can give access to other areas."*

Until today Omni had exactly one administrator switch — `UserProfile
.is_administrator` — and it was all or nothing. Flipping it for Unopa would have
handed him payroll, super user and every manager title, which is the opposite of
what was asked. So this module adds a SECOND, strictly weaker kind of
administrator.

**The list is an allow-list, and it is DERIVED, not typed out.** The CFO chose
this shape deliberately (15-Sep-2026) over the deny-list his original message
described. A deny-list of "no manager, no super user, no payroll" leaves every
title nobody thought to name — and every title added in future — quietly
grantable. Here the delegate may grant a title only if that title appears in
NONE of the money-power sets already defined on `UserProfile`:

    APPROVAL_TITLES          journal entries — approve
    CREATION_TITLES          journal entries — create
    PAYROLL_APPROVAL_TITLES  payroll imports
    FINANCIALS_VIEW_TITLES   GL, reports, dashboards, audit log
    SOD_MAKER_TITLES         high-risk origination
    SOD_CHECKER_TITLES       high-risk approval

plus an explicit block on the claims grades that carry PO-approval authority and
on `SYSTEM_API`. A new title added to `UserProfile.Title` therefore defaults to
NOT delegable and stays that way until someone puts it in a money set or
consciously exempts it here — which is the whole point of doing it this way.

The delegate can also never touch: `is_administrator`, `is_superuser`, their own
profile, or the profile of anyone who already holds administrative authority.
"""
from __future__ import annotations

from .models import UserProfile

T = UserProfile.Title

#: Titles that carry authority over money but sit in none of the frozensets
#: above. The two claims seniors approve claims payment orders alongside the
#: Claims Manager (see the Title enum comments), so they are manager-level in
#: everything but name.
_EXTRA_BLOCKED = frozenset({
    T.CLAIMS_TEAM_LEADER,
    T.SENIOR_CLAIMS_ASSOCIATE,
    T.SYSTEM_API,
})


def _blocked_titles() -> frozenset[str]:
    """Every title a delegated access admin may NOT grant."""
    return frozenset(
        set(UserProfile.APPROVAL_TITLES)
        | set(UserProfile.CREATION_TITLES)
        | set(UserProfile.PAYROLL_APPROVAL_TITLES)
        | set(UserProfile.FINANCIALS_VIEW_TITLES)
        | set(UserProfile.SOD_MAKER_TITLES)
        | set(UserProfile.SOD_CHECKER_TITLES)
        | set(_EXTRA_BLOCKED)
    )


def delegable_titles() -> frozenset[str]:
    """The allow-list: titles a delegated access admin may grant or revoke."""
    return frozenset(set(T.values) - set(_blocked_titles()))


def is_access_delegate(profile) -> bool:
    """A live, non-read-only profile carrying the delegate flag."""
    if not profile or not profile.is_active or profile.is_read_only_identity:
        return False
    return bool(getattr(profile, 'is_access_delegate', False))


_LOGIN_REFUSAL = (
    'Only the CFO can change how someone signs in. You can change what they '
    'may open, not their login.'
)


def delegate_refusal(actor_profile, target_profile, new_values: dict) -> str | None:
    """
    Why a delegated access admin may NOT make this change — or None if allowed.

    `new_values` is the set of profile fields being written. Anything absent is
    unchanged and therefore not the delegate's doing.

    A full administrator never reaches this function; it is asked only after
    `can_administer_users` has already said no.
    """
    if not is_access_delegate(actor_profile):
        return (
            'Administrator privileges are required. Only the CFO, a user marked '
            'is_administrator, or a delegated access administrator can manage profiles.'
        )

    # A delegate cannot edit their own access — no self-promotion, ever.
    if target_profile is not None and target_profile.pk == actor_profile.pk:
        return (
            'A delegated access administrator cannot change their own access. '
            'Ask the CFO.'
        )

    # A delegate cannot touch anyone who already holds admin authority.
    if target_profile is not None and (
        target_profile.can_administer_users or is_access_delegate(target_profile)
    ):
        return (
            'That user is an administrator. Only the CFO can change an '
            "administrator's access."
        )

    # A delegate may not touch a login. `UserProfileWriteSerializer` exposes
    # `password` and `username`, and none of the title rules below would see
    # them: a delegate could PATCH a new password onto the Finance Manager,
    # sign in as her, and approve payroll — precisely what the CFO reserved to
    # himself, reached through a side door. Delegates grant access; they do not
    # own credentials. (Fable review, 15-Sep-2026 — this was the one real
    # escalation in the first draft.)
    # Only on an EXISTING person: creating a new starter necessarily supplies a
    # username and a password, and refusing that would block the most ordinary
    # access request there is. There is no login to take over on a create.
    # (Fable round 2, 15-Sep-2026 — the first version of this rule broke the
    # "otherwise he can give access" half of the CFO's instruction.)
    if target_profile is not None:
        login = getattr(target_profile, 'user', None)
        # A password is refused outright — there is no "unchanged password" to
        # echo back. A username or an email is refused only when it DIFFERS
        # from what the person already has: the edit form posts the whole
        # profile every time, so refusing an unchanged echo would refuse every
        # save the delegate ever makes, and the screen would be a door onto a
        # room he cannot move in. (Fable round 3, 15-Sep-2026.)
        if new_values.get('password'):
            return _LOGIN_REFUSAL
        for field, current in (('username', getattr(login, 'username', None)),
                               ('email', getattr(login, 'email', None))):
            value = new_values.get(field)
            if value and (value or '').strip().lower() != (current or '').strip().lower():
                return _LOGIN_REFUSAL

    # On a create, the login guard above is off (there is no login yet), so
    # nothing stops a delegate creating a SECOND account carrying someone
    # else's address. SSO resolves a person by email with `.first()` and no
    # ordering, so which row wins is a matter of primary keys. (Fable round 3.)
    if target_profile is None:
        new_email = (new_values.get('email') or '').strip()
        if new_email:
            from django.contrib.auth.models import User
            if User.objects.filter(email__iexact=new_email).exists():
                return (
                    f'{new_email} already belongs to someone. Two accounts on '
                    'one address break the sign-in; ask the CFO.'
                )

    # A delegate may not touch anyone who ALREADY holds a reserved title. The
    # rule below stops them GRANTING a manager title; without this one they
    # could still deactivate the Finance Manager, or move her sideways, which
    # is just as much a manager-level access change.
    current = getattr(target_profile, 'title', None)
    if current is not None and current in _blocked_titles():
        return (
            f'{UserProfile.Title(current).label} is a role reserved for the CFO, '
            'so that person\'s access can only be changed by him.'
        )

    # Never the admin switches themselves.
    for field in ('is_administrator', 'is_access_delegate'):
        if field in new_values and bool(new_values[field]) != bool(
            getattr(target_profile, field, False)
        ):
            return (
                'Only the CFO can make someone an administrator. '
                'Ask the CFO for this one.'
            )

    # The allow-list.
    # On a CREATE an absent title is not "no change" — it is the model default,
    # which is `accountant`: a title that can raise journal entries and read the
    # general ledger. Resolve it before the check, or the allow-list is simply
    # skipped and the API hands out finance access. (Fable round 3.)
    if target_profile is None:
        title = new_values.get('title') or UserProfile._meta.get_field('title').default
    else:
        title = new_values.get('title')
    if title is not None and title != getattr(target_profile, 'title', None):
        if title not in delegable_titles():
            return (
                f'"{UserProfile.Title(title).label}" carries authority over money, '
                'payroll or manager-level approval, so it is reserved for the CFO. '
                'You can grant: '
                + ', '.join(sorted(UserProfile.Title(t).label for t in delegable_titles()))
                + '.'
            )

    return None
