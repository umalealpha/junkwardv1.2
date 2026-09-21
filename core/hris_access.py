"""
core/hris_access.py — Whitelist gate for the HRIS module.

CFO directive 2026-05-18: HRIS contains payroll details and is restricted
to the people who are also approved to handle CFO-level data. Only the
five named users can see the `/hris`, `/hr-analytics`, and legacy
`/hris/?legacy=1` pages. Everyone else is hard-redirected to /dashboard
without ever loading the HR data.

The current whitelist is hardcoded but ALSO env-overridable so the CFO
can add or remove people without a deploy. The env var
`OMNI_HRIS_ALLOWED_LOCAL_PARTS` accepts a comma-separated list of
local-parts (the bit before @). Match is case-insensitive and uses
local-part equality first, then a `startswith` fallback so a Microsoft
SSO email like 'pakokago@alphadirect.co.bw' still matches the
'pako' alias.

Bypass: Django superusers and `UserProfile.is_administrator` ALWAYS see
HRIS regardless of the whitelist — they are the people who can grant
access in the first place.

Identity proof: this gate runs AFTER Azure SSO has verified the
identity. The "Office 365 authenticator" prompt the CFO refers to is
the Azure conditional-access MFA, which fires at sign-in time before
this code ever runs. We don't need a second MFA step on every HRIS
click — the standing session is already MFA-backed.
"""
from __future__ import annotations

import os

# Default whitelist (lowercased local-parts of @alphadirect.co.bw emails).
# CFO directive 2026-05-18: Prathap (CFO), Arun (CEO), Kago, Pako, Unami.
# Each name covers multiple plausible Microsoft email patterns —
# `kago` matches `kago.tshutlhedi`, `pako` matches `pako.kago`, etc.
DEFAULT_ALLOWED_LOCAL_PARTS = (
    'pganesharajah',  # CFO — Prathap Ganesharajah
    'aiyer',          # CEO — Arun Iyer
    'arjuniyer',      # COO — Arjun Iyer (CFO directive 2026-07-05)
    'kago',           # Kago Tshutlhedi (Finance)
    'pako',           # Pako Lisley Kago (Finance)
    'unami',          # Unami Butale (Senior Management)
    'dikgopoleng',    # Dorothy Ikgopoleng (HR) — CFO directive 2026-06-23
    'tshephang',      # Tshephang Motswagae (Veritas payroll) — CFO directive 2026-06-23
    'lntabeni',       # Legakwa Ntabeni (Finance) — bank-upload uploader, CFO directive 2026-07-15
)


def _allowed_local_parts() -> set[str]:
    """Whitelist as a set of lowercased local-parts.

    Env override: `OMNI_HRIS_ALLOWED_LOCAL_PARTS` = `aiyer,pganesharajah,...`
    Falls back to DEFAULT_ALLOWED_LOCAL_PARTS when unset.
    """
    env = (os.environ.get('OMNI_HRIS_ALLOWED_LOCAL_PARTS') or '').strip()
    if not env:
        return {x.lower() for x in DEFAULT_ALLOWED_LOCAL_PARTS}
    return {p.strip().lower() for p in env.split(',') if p.strip()}


def _local_part(email: str | None) -> str:
    """Lowercased local-part of an email; '' if no '@'."""
    if not email or '@' not in email:
        return (email or '').strip().lower()
    return email.split('@', 1)[0].strip().lower()


def user_can_access_hris(user) -> bool:
    """True if `user` is allowed to see HRIS / payroll data.

    Order of precedence:
      1. Anonymous / inactive -> False.
      2. Django superuser -> True.
      3. UserProfile.is_administrator -> True.
      4. HR_MANAGER role assignment -> True.   (CFO directive 2026-05-21)
      5. Local-part of email is in the whitelist (exact or startswith).
      6. Else -> False.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(user, 'is_active', True):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    profile = getattr(user, 'profile', None)
    if profile and getattr(profile, 'is_administrator', False):
        return True

    # CFO directive 2026-05-21 / 2026-06-07: any user with an HR_MANAGER or
    # HRIS role assignment can access HRIS — granted explicitly to the HR team
    # (Dorothy Ikgopoleng, Unami Butale, Thapelo Morapedi). HRIS-role edits are
    # dual-approved (see hris.amendment_service).
    try:
        from core.models import UserRoleAssignment   # late import to avoid cycles
        # CFO directive 2026-07-09: department managers (Claims / Operations) do
        # NOT get the HRIS module — they were tripping the confidential-HR
        # password wall on team reports they don't need. They keep Employee
        # Self-Service (their own leave / payslips / profile), which needs no
        # HRIS access. Only real HR roles hold the module.
        # HR_VIEWER (CFO 2026-08-07): a read-only HR viewer holds the module so
        # it can see everyone's HR record, but carries NO payroll capability and
        # is deliberately kept OUT of user_can_view_payroll / is_hr — so it can
        # never reach another employee's pay. See can_view_compensation below.
        if UserRoleAssignment.objects.filter(
            user=user,
            role__code__in=['HR_MANAGER', 'HRIS', 'HR_VIEWER'],
        ).exists():
            return True
    except Exception:        # noqa: BLE001
        pass

    # CFO directive 2026-05-21 (Payroll headline): finance approvers + HR
    # staff need HRIS access for the Payroll surface.
    if profile:
        title = (getattr(profile, 'title', '') or '').lower()
        dept  = (getattr(profile, 'department', '') or '').lower()
        # CFO directive 2026-08-04: CEO / COO get HR + payroll visibility
        # (salaries / compensation), same read-only tier as Arun below. They
        # cannot edit HR records — see hris_role → 'ceo'.
        if title in {'cfo', 'finance_manager', 'financial_controller', 'hr_manager',
                     'ceo', 'coo'}:
            return True
        if 'hr' in dept or 'human res' in dept:
            return True

    local = _local_part(getattr(user, 'email', None))
    if not local:
        return False
    allow = _allowed_local_parts()
    if local in allow:
        return True
    # `startswith` fallback so `kago.tshutlhedi` matches `kago`, etc.
    for a in allow:
        if local == a or local.startswith(a):
            return True
    return False


def hris_role(user) -> str:
    """Return one of: 'anon' | 'ess' | 'mgr' | 'hr' | 'admin' | 'ceo' | 'superadmin'.

    CFO directive 2026-05-18 (Unami's TMS Orbit RBAC mapping):

        ess         — Employee Self Service. View own profile, apply for
                      leave, view own payslips / assessments.
        mgr         — Manager. View direct reports, approve their leave,
                      run performance assessments on them.
        hr          — HR admin. View everyone, manage leave administration,
                      add/edit employees.
        admin       — Full HRIS administration except superuser actions.
        ceo         — Arun Iyer (aiyer@). Full read access including
                      compensation + bonus pool, but cannot edit identity.
        superadmin  — Django superuser. Bypasses every check.

    Resolution order:
      1. Django superuser → 'superadmin'.
      2. UserProfile.is_administrator → 'admin'.
      3. CEO local-part 'aiyer' → 'ceo'.
      4. UserProfile.title FINANCE_MANAGER / FINANCIAL_CONTROLLER / CFO → 'hr'
         (Finance leadership owns HRIS day-to-day in Alpha Direct).
      5. Has at least one direct report on HRISProfile.manager → 'mgr'.
      6. Authenticated employee → 'ess'.
      7. Anonymous → 'anon'.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return 'anon'
    if getattr(user, 'is_superuser', False):
        return 'superadmin'
    profile = getattr(user, 'profile', None)
    if profile and getattr(profile, 'is_administrator', False):
        return 'admin'

    local = _local_part(getattr(user, 'email', None))
    if local == 'aiyer' or local.startswith('aiyer'):
        return 'ceo'

    # CFO directive 2026-08-04: the CEO / COO access titles get the same
    # read-only HR view as Arun — full read incl. compensation + bonus pool,
    # no edit. Title-based so it is not tied to one email address.
    if profile and getattr(profile, 'title', '') in ('ceo', 'coo'):
        return 'ceo'

    # CFO directive 2026-06-07: the 'HRIS' role grants full HRIS editing power
    # (the HR team — Unami, Dorothy, Thapelo). Their edits go through a
    # mandatory dual-approval workflow (see hris.amendment_service), so the
    # broad capabilities here are safe — nothing reaches the live record
    # without a second approver. Checked before 'hr' so it wins.
    try:
        from core.models import UserRoleAssignment
        if UserRoleAssignment.objects.filter(user=user, role__code='HRIS').exists():
            return 'hris'
    except Exception:        # noqa: BLE001
        pass

    # Finance leadership = HR admin in Alpha Direct (per CFO + Unami discussion)
    try:
        from core.models import UserProfile as _UP
        finance_titles = {
            getattr(_UP.Title, 'CFO', None),
            getattr(_UP.Title, 'FINANCE_MANAGER', None),
            getattr(_UP.Title, 'FINANCIAL_CONTROLLER', None),
        }
        finance_titles.discard(None)
        if profile and profile.title in finance_titles:
            return 'hr'
    except Exception:
        pass

    # CFO directive 2026-05-21: HR_MANAGER role assignment maps to 'hr'
    # tier (manage_leave_admin, manage_employees, view_all).
    try:
        from core.models import UserRoleAssignment
        if UserRoleAssignment.objects.filter(
            user=user, role__code='HR_MANAGER',
        ).exists():
            return 'hr'
    except Exception:        # noqa: BLE001
        pass

    # The HR_MANAGER *title* also maps to 'hr' (2026-08-03). This closes an
    # inconsistency rather than granting anything new: user_can_access_hris()
    # already accepts this title (see its title check above), and so do
    # hris.leave_encash_service.is_hr and hris.document_access.is_hr_doc_admin.
    # ONLY this function demanded a role ASSIGNMENT on top of the title.
    #
    # The effect of the gap: a title-only HR identity passed the HRIS door, then
    # resolved to 'ess', was refused every HR capability, and is_hris_unlocked()
    # withheld the privileged-role bypass — so it hit a shared-password wall it
    # can never answer (no session, no password).
    #
    # Verified on prod before shipping: every human holding this title
    # (tmorapedi, dikgopoleng, ubutale) ALREADY holds the HR_MANAGER or HRIS
    # role, so this changes no person's access. It only lets a title-only
    # service identity — the read-only hr-data-extract key — read HR data.
    try:
        from core.models import UserProfile as _UP2
        if profile and profile.title == getattr(_UP2.Title, 'HR_MANAGER', None):
            return 'hr'
    except Exception:        # noqa: BLE001
        pass

    # HR_VIEWER (CFO 2026-08-07): read-only, see-everyone HR viewer. Checked AFTER
    # every real HR/finance tier above (so a higher role always wins) but BEFORE
    # the 'mgr'/'ess' fallback, so a viewer who also happens to manage a small
    # team is still elevated to see-all. Its capability set carries view_all but
    # NO compensation/payroll and NO manage_* — see ROLE_CAPABILITIES['hr_viewer'].
    try:
        from core.models import UserRoleAssignment
        if UserRoleAssignment.objects.filter(user=user, role__code='HR_VIEWER').exists():
            return 'hr_viewer'
    except Exception:        # noqa: BLE001
        pass

    # CFO directive 2026-07-09: Claims / Operations manager ROLES no longer map
    # to the HRIS manager tier — they get Employee Self-Service only (own data),
    # like any staff member. Genuine people-managers are still detected below by
    # their real HRISProfile.manager direct-report links.

    # Senior Operational Staff (CFO directive 2026-08-24): a team-leader step
    # above ordinary operations staff. Gets the 'mgr' tier by TITLE (own data +
    # see/approve their own team + team reviews) even without a direct-report
    # link. Carries NO finance/payroll/salary access — the SENIOR_OPERATIONS
    # title is in none of the finance title-sets, is absent from the HRIS
    # module title-gate above, and is not a finance approver, so is_finance_
    # approver / can_view_all / can_view_compensation all stay False for it.
    try:
        from core.models import UserProfile as _UPso
        if profile and profile.title == getattr(_UPso.Title, 'SENIOR_OPERATIONS', None):
            return 'mgr'
    except Exception:        # noqa: BLE001
        pass

    # Manager detection: HRISProfile.manager FK reverse-lookup. Use a
    # late import so this module stays usable from migrations.
    # HRIS-002 fix (2026-06-10): the User↔Employee link is
    # payroll.Employee.user (related_name='employee_record') — UserProfile
    # has no `employee` field, so the old `profile.employee_id` read was
    # always None and 'mgr' was unreachable. Check the real link first,
    # keep the profile attr as a defensive fallback.
    try:
        from hris.models import HRISProfile
        emp_rec = getattr(user, 'employee_record', None)
        emp_id = getattr(emp_rec, 'id', None)
        if not emp_id:
            emp_id = getattr(profile, 'employee_id', None) if profile else None
        if emp_id and HRISProfile.objects.filter(manager_id=emp_id).exists():
            return 'mgr'
    except Exception:
        pass

    return 'ess'


# Capability bitmap each role can perform. The frontend mirrors this so
# the UI can hide buttons the user can't action; the backend still
# enforces every gate independently.
ROLE_CAPABILITIES: dict[str, set[str]] = {
    'anon':       set(),
    'ess':        {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment'},
    'mgr':        {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
                   'view_team', 'approve_team_leave', 'assess_team',
                   'view_talent'},
    # Read-only, see-EVERYONE HR viewer (CFO 2026-08-07). Sees all staff HR
    # records + own payslip, but NO compensation/payroll and NO manage_/approve_
    # anything. Deliberately NOT granted view_compensation/view_others_payslip,
    # and — because that capability set was never enforced — kept OUT of
    # user_can_view_payroll / is_hr / can_view_all so it cannot reach other
    # people's pay on any surface.
    'hr_viewer':  {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
                   'view_team', 'view_talent', 'view_all'},
    'hr':         {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
                   'view_team', 'approve_team_leave', 'assess_team',
                   'view_talent',
                   'view_all', 'manage_leave_admin', 'manage_employees'},
    # Full HRIS editing power (writes routed through dual approval).
    'hris':       {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
                   'view_team', 'approve_team_leave', 'assess_team',
                   'view_talent',
                   'view_all', 'manage_leave_admin', 'manage_employees',
                   'view_bonus_pool', 'view_compensation', 'manage_payroll',
                   'manage_tax_compliance', 'amend_hris'},
    'ceo':        {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
                   'view_team', 'approve_team_leave', 'assess_team',
                   'view_talent',
                   'view_all', 'view_bonus_pool', 'view_compensation'},
    'admin':      {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
                   'view_team', 'approve_team_leave', 'assess_team',
                   'view_talent',
                   'view_all', 'manage_leave_admin', 'manage_employees',
                   'view_bonus_pool', 'view_compensation', 'manage_payroll',
                   'manage_tax_compliance'},
    'superadmin': {'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
                   'view_team', 'approve_team_leave', 'assess_team',
                   'view_talent',
                   'view_all', 'manage_leave_admin', 'manage_employees',
                   'view_bonus_pool', 'view_compensation', 'manage_payroll',
                   'manage_tax_compliance', 'manage_rbac'},
}


def hris_entity_scope(user):
    """The entities a user's HRIS view is restricted to, or None if unrestricted.

    Mirrors core.models.allowed_company_ids: returns None for the unrestricted
    bucket (superuser / administrator / CFO) and a list of {id, code, name} for
    a user pinned via UserCompanyAccess (e.g. ADRisk-only). The frontend uses
    this to lock the company selector to the granted entity. CFO 2026-06-16.
    """
    try:
        from core.models import allowed_company_ids, Company
    except Exception:        # noqa: BLE001
        return None
    allowed = allowed_company_ids(user)
    if allowed == {'*'}:
        return None
    rows = (Company.objects.filter(id__in=allowed).order_by('code')
            if allowed else Company.objects.none())
    return [{'id': str(c.id), 'code': c.code, 'name': c.name} for c in rows]


def hris_access_payload(user) -> dict:
    """Shape returned by the HRIS access probe endpoint."""
    role = hris_role(user)
    scope = hris_entity_scope(user)
    return {
        'allowed':    user_can_access_hris(user),
        'email':      (getattr(user, 'email', '') or '').lower(),
        'is_superuser': bool(getattr(user, 'is_superuser', False)),
        'role':       role,
        'capabilities': sorted(ROLE_CAPABILITIES.get(role, set())),
        # Whether this user may see OTHER people's pay — the frontend hides the
        # salary column / pay screens when false (server still enforces it).
        'can_view_compensation': can_view_compensation(user),
        # None = consolidated (all entities). A list = locked to those entities.
        'entity_scope':        scope,
        'entity_scope_locked': scope is not None,
        'reason':     (
            'CFO directive 2026-05-18: HRIS restricted to '
            'Prathap, Arun, Kago, Pako, Unami + superuser / admin.'
        ),
    }


def user_can_amend_hris(user) -> bool:
    """True if the user may submit HRIS amendments (HRIS role or admin tiers)."""
    return hris_role(user) in {'hris', 'admin', 'superadmin'}


def can_view_compensation(user) -> bool:
    """True if the user may see OTHER employees' pay / compensation figures —
    grade-band salary in the HR directory, the pay-grade midpoints, and the
    old/new values inside a pending pay amendment.

    This is the enforcement the 'view_compensation' capability never had: it was
    listed in ROLE_CAPABILITIES but read by no endpoint, so withholding it meant
    nothing. Those three HRIS surfaces were gated only by MODULE access, so
    anyone who could open the HR directory could read pay. This gate closes that
    for the read-only hr_viewer WITHOUT changing any existing viewer:

      - superuser / administrator / HRIS / CEO-COO tiers            -> yes
      - anyone user_can_view_payroll accepts (finance & HR titles,
        HR department, HR_MANAGER|HRIS role, named payroll viewers) -> yes
      - anyone explicitly on the HRIS local-part whitelist (the
        execs / finance / HR the CFO named)                         -> yes
      - hr_viewer (module access via the HR_VIEWER role only)       -> NO

    Every current holder of HRIS module access falls in one of the first three
    buckets, so this removes NO ONE's existing visibility; only the new
    read-only viewer is held out.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if hris_role(user) in {'hris', 'admin', 'ceo', 'superadmin'}:
        return True
    try:
        from payroll.amendment_views import user_can_view_payroll  # lazy: avoid import cycle
        if user_can_view_payroll(user):
            return True
    except Exception:        # noqa: BLE001
        pass
    local = _local_part(getattr(user, 'email', None))
    if local:
        allow = _allowed_local_parts()
        if local in allow or any(local.startswith(a) for a in allow):
            return True
    return False
