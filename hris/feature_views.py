"""
hris/feature_views.py — Endpoints for the Unami TMS Orbit feature pack.

CFO directive 2026-05-18 (Unami audit closeout):
  * /hris/api/leave-requests/   POST  apply for leave w/ optional cert
  * /hris/api/leave-balances/   GET   current-year balances per leave type
  * /hris/api/assessments/      POST  submit a 1-4 PMS review
  * /hris/api/my-itw8/          GET   BURS ITW8 figures for a tax year
  * /hris/api/bonus-simulate/   POST  3-layer bonus pool calculation

Every endpoint enforces both the whitelist (user_can_access_hris) and the
appropriate capability from core.hris_access.ROLE_CAPABILITIES so the
Microsoft-authenticated session alone is not enough — the user must also
hold the right role.
"""
from __future__ import annotations
from hris.departments import fold_legacy

import datetime as _dt
from decimal import Decimal
import logging
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import (
    ROLE_CAPABILITIES, hris_role, user_can_access_hris,
)
from core.hris_unlock import is_hris_unlocked
from hris.models import (
    HRISProfile, LeaveRequest, LeaveType, OKR, PerformanceReview, Recognition,
)

log = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

# Capabilities a plain employee (role 'ess') self-serves on their OWN data.
# These are reachable by EVERY authenticated employee — no HRIS whitelist, no
# HRIS password unlock — because the endpoints behind them are always
# self-scoped to the caller (their own leave / payslip / profile / assessment).
# CFO directive 2026-06-16 (Lakshmi Anand / ADRisk bug aec2f3ce): the module's
# self-service tier must be reachable by all staff (incl. subsidiaries), while
# payroll, all-employee data and compensation stay whitelist-locked.
SELF_SERVICE_CAPS = frozenset({
    'view_self', 'apply_leave', 'view_own_payslip', 'view_own_assessment',
})

# Capabilities a real line manager exercises on their OWN TEAM (CFO 2026-08-07).
#
# These were on the privileged tier, which means the HRIS whitelist — five
# people. Every other manager got "HRIS access is restricted" when they opened
# the leave queue or pressed Approve, and could only decide leave through the
# one-click link in the notification email. That is why approvals stall and why
# staff hear nothing back.
#
# This grants NO new authority: the email route already lets exactly these
# managers make exactly these decisions. It only makes the in-app path match.
# Every endpoint behind these capabilities is still entity-scoped AND filtered
# to leave routed to the caller (see leave_queue / decide_leave), so a manager
# still cannot touch another team's records.
# 'assess_team' joins it on 2026-08-09 (CFO): giving a direct report feedback
# sat behind the SHARED HRIS password, so all ten managers had to be told the
# same secret before any of them could coach anybody — a password ten people
# know is not a password. The capability itself is the real control, and it is
# already per-manager. The CFO, CEO, HR, HRIS and admins never saw this prompt,
# which is why it went unnoticed.
#
# STILL DELIBERATELY NARROW. 'view_team' is NOT here — it also fronts the
# all-employee leave report and the daily summary, which must stay behind the
# whitelist and the password. Note WHY 'assess_team' is safe and 'view_team' is
# not: of the roles holding 'assess_team' (mgr, hr, hris, ceo, admin,
# superadmin) every one except 'mgr' already bypasses the password anyway, so
# this opens the door to managers and nobody else. 'hr_viewer' — the role that
# broke this on 7 Aug — holds 'view_team' but NOT 'assess_team'.
# Before adding anything here, enumerate every role holding the capability
# (checklist H28).
TEAM_CAPS = frozenset({
    'approve_team_leave',
    'assess_team',
})


def _gate(request, *, capability: str | None = None) -> Response | None:
    """Return a 4xx Response if the user fails the HRIS access policy.

    Two tiers:
      * Self-service (`capability` in SELF_SERVICE_CAPS) — allowed for any role
        that holds the capability (every authenticated employee is at least
        'ess', which holds these). No whitelist, no unlock: the data is always
        self-scoped to the caller.
      * Privileged (a sensitive capability, OR no capability = an admin/all-
        employee surface) — unchanged from CFO directive 2026-05-18:
          403  not on the HRIS whitelist.
          401  whitelisted but HRIS is locked (`requires_unlock: true`).
          403  whitelisted + unlocked but the role lacks `capability`.
    """
    role = hris_role(request.user)
    caps = ROLE_CAPABILITIES.get(role, set())

    # Self-service tier — the only gate is "does the role hold the cap".
    if capability and capability in SELF_SERVICE_CAPS:
        if capability not in caps:
            return Response(
                {'detail': f'Role "{role}" cannot {capability.replace("_", " ")}.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    # Team tier (CFO 2026-08-07) — a genuine line manager may run their own
    # team's leave without being on the five-person HRIS whitelist. The
    # endpoints behind these capabilities scope to the caller's own team.
    if capability and capability in TEAM_CAPS and capability in caps:
        return None

    # Privileged tier — whitelist + unlock + (optional) capability.
    if not user_can_access_hris(request.user):
        return Response(
            {'detail': 'HRIS access is restricted to authorised personnel only.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not is_hris_unlocked(request.user):
        return Response(
            {
                'detail': 'HRIS is locked. Enter the HRIS password to continue.',
                'requires_unlock': True,
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if capability and capability not in caps:
        return Response(
            {'detail': f'Role "{role}" cannot {capability.replace("_", " ")}.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _resolve_manager_employee(user):
    """The caller's own payroll.Employee, via the user link or their email.

    Same two-link resolution _profile_for uses — email-linked staff must not
    silently fail the team-tier check below (CFO 2026-08-07).
    """
    from payroll.models import Employee
    emp = getattr(user, 'employee_record', None)
    if emp is not None:
        return emp
    email = (getattr(user, 'email', '') or '').strip()
    return Employee.objects.filter(email__iexact=email).first() if email else None


def _profile_for(user) -> HRISProfile | None:
    """Best-effort lookup of the HRISProfile linked to `user`.

    Tries two links so self-service (my profile / payslips / leave) works
    wherever EITHER link is populated:
      1. payroll.Employee.user  → user.employee_record  → HRISProfile  (the
         link hris_role() already uses; populated for SSO-provisioned staff).
      2. UserProfile.employee → Employee → HRISProfile  (legacy attr).
    Returns None only when neither link is paired — i.e. there is genuinely no
    staff record behind this login. If the record IS there but the HRIS extension
    row is missing, that row is created rather than reported as a missing link.
    CFO 2026-06-16: fixed so group-company staff see their own profile rather
    than a 404.
    """
    emp = getattr(user, 'employee_record', None)
    if emp is None:
        up = getattr(user, 'userprofile', None)
        emp = getattr(up, 'employee', None)
    if emp is None:
        return None

    # Self-heal the missing half. The staff record and the login are already
    # paired at this point — what is absent is only the HRIS extension row, which
    # payroll-imported joiners never get because nothing on that path creates one.
    # Without this the person is told "your staff record has not been linked to
    # your login", which is not true and sends them to HR to fix something that
    # is not broken; they simply cannot apply for leave, see a payslip or open
    # their own profile (2026-08-08: two developers, weeks after joining).
    # Creating the row is safe: it holds talent-management fields only, every one
    # of them optional, and no pay or leave figure is derived from its defaults.
    hp, created = HRISProfile.objects.get_or_create(employee=emp)
    if created:
        # Not silent: a row appearing here means a joiner reached self-service
        # without one, and the import path that should have created it is still
        # missing them. The person is unblocked either way.
        log.info('HRIS profile self-healed for employee id %s — the import path '
                 'did not create one', emp.pk)
    return hp


# Leave-engine rules — ELRA 2025 statutory defaults (HRIS blueprint, ADI/HC/HRIS/2026).
# These are the FROZEN fallback defaults. At runtime, get_leave_rules() overlays
# any active hris.LeaveType rows on top of these (DB wins) so HR can change a
# statutory value in admin without a code deploy. `days` = the company
# entitlement (may exceed the statutory floor); `statutory_min` = the ELRA floor.
# 'cos' carries the governing-law reference shown to staff (now ELRA 2025).
DEFAULT_LEAVE_RULES: dict[str, dict[str, Any]] = {
    'annual':        {'days': 21, 'paid_pct': 100, 'cos': 'ELRA s.219', 'statutory_min': 15,
                      'accrual_method': 'monthly', 'min_take': 8, 'carry_years': 3,
                      'rule': 'Accrues monthly; cannot be taken in advance of accrual. 8 days '
                              'must be taken within 6 months of cycle end (ELRA s.219). Company '
                              'grants 21 days (statutory minimum 15).'},
    'sick':          {'days': 20, 'paid_pct': 100, 'cos': 'ELRA s.220', 'statutory_min': 20,
                      'accrual_method': 'frontload', 'proof': 'medical_certificate',
                      'rule': '20 days at full pay, available immediately (front-loaded, not '
                              'accrued). Medical certificate required (ELRA s.220).'},
    'hospitalisation': {'days': 20, 'paid_pct': 100, 'cos': 'ELRA s.220', 'statutory_min': 20,
                      'accrual_method': 'frontload', 'proof': 'medical_certificate',
                      'rule': '20 days, separate from ordinary sick leave (ELRA s.220).'},
    'maternity':     {'days': 98, 'paid_pct': 70, 'cos': 'ELRA s.222', 'gender': 'F',
                      'accrual_method': 'frontload', 'blocks_termination': True,
                      'rule': '14 weeks (98 days) at 70% basic pay. No termination notice while '
                              'on maternity leave (ELRA s.222 / s.224). Female employees.'},
    'paternity':     {'days': 5, 'paid_pct': 100, 'cos': 'ELRA s.227', 'gender': 'M',
                      'accrual_method': 'frontload', 'window_weeks': 14, 'proof': 'birth_certificate',
                      'rule': '5 paid days, taken within 14 weeks of the birth. Birth certificate '
                              'required (ELRA s.227). Male employees.'},
    'compassionate': {'days': 5, 'paid_pct': 100, 'cos': 'ELRA s.221', 'statutory_min': 3,
                      'accrual_method': 'frontload',
                      'rule': '5 paid days per cycle for illness/death of a close family member '
                              '(company; ELRA s.221 statutory minimum 3).'},
    'study':         {'days': 10, 'paid_pct': 100, 'cos': 'CoS §7.12.3',
                      'accrual_method': 'frontload',
                      'rule': '10 days per year, max 5 per semester (company policy).'},
    'special':       {'days': 10, 'paid_pct': 100, 'cos': 'CoS §7.11',
                      'accrual_method': 'frontload',
                      'rule': '10 days for extenuating circumstances (company policy).'},
    # Time Doctor deduction (HR request, 2026-08-25; CFO chose employee
    # self-service). Staff whose tracked hours fall short apply here and state it,
    # so the shortfall is a recorded, applied absence rather than a silent payroll
    # adjustment. It is a NORMAL leave type in this flow, the same as annual/sick —
    # no special case. UNPAID with NO entitlement (days=0, paid_pct=0): the
    # standard balance builder yields a clean 0/0d card (available = max(0,
    # accrued - used)), and the annual-only advance-of-accrual gate never fires.
    'td_deduct':     {'days': 0, 'paid_pct': 0, 'cos': 'Time Doctor deduction',
                      'accrual_method': 'frontload',
                      'rule': 'Unpaid deduction for a Time Doctor tracked-hours '
                              'shortfall. No leave balance is earned or spent; this '
                              'records the absence so the deduction is applied '
                              'through Omni rather than silently.'},
}

# Back-compat alias: existing call sites referenced COS_LEAVE_RULES. Kept so the
# frozen defaults remain importable (e.g. leave_upload_views); runtime code
# should call get_leave_rules() to pick up HR's admin edits.
COS_LEAVE_RULES = DEFAULT_LEAVE_RULES


def get_leave_rules() -> dict[str, dict[str, Any]]:
    """The active leave-rule set: ELRA defaults overlaid by editable LeaveType
    rows (DB wins), so a statutory change is a data edit not a code deploy.

    Falls back to the frozen defaults for any code with no DB row, and never
    raises if the table is unavailable (returns defaults) — so behaviour is
    identical to the old constant until HR seeds/edits LeaveType.
    """
    rules: dict[str, dict[str, Any]] = {c: dict(r) for c, r in DEFAULT_LEAVE_RULES.items()}
    canonical = set(rules)   # only the staff-facing types — never the payroll taxonomy
    try:
        from hris.models import LeaveType
        rows = list(LeaveType.objects.filter(is_active=True))
        # Legacy data has case-variant duplicates (e.g. "maternity" 98@70 AND a
        # stale "MATERNITY" 84@50). Both lower-case to the same key, so apply
        # ELRA-referenced rows LAST (sort False<True) — the ELRA row always wins
        # over a legacy duplicate regardless of DB order.
        rows.sort(key=lambda lt: (lt.statutory_ref or '').upper().startswith('ELRA'))
        for lt in rows:
            code = (lt.code or '').lower().strip()
            if code not in canonical:
                continue          # ignore vac_*/hosp_*/lwop payroll leave codes
            merged = dict(rules[code])          # keep default gender/rule/etc.
            if lt.default_annual_days:
                merged['days'] = int(lt.default_annual_days)
            if lt.paid_pct is not None:
                merged['paid_pct'] = int(lt.paid_pct)
            if lt.statutory_ref:
                merged['cos'] = lt.statutory_ref
            if lt.accrual_method:
                merged['accrual_method'] = lt.accrual_method
            if lt.statutory_min_days:
                merged['statutory_min'] = int(lt.statutory_min_days)
            if lt.min_mandatory_take_days:
                merged['min_take'] = int(lt.min_mandatory_take_days)
            if lt.leave_window_weeks:
                merged['window_weeks'] = int(lt.leave_window_weeks)
            if lt.blocks_termination:
                merged['blocks_termination'] = True
            if lt.proof_type:
                merged['proof'] = lt.proof_type
            rules[code] = merged
    except Exception:
        pass
    return rules


# ─── Leave ────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_policy_spec(request):
    """GET /hris/api/leave-policy/?type=<code> — the discretionary question set.

    One definition (hris.discretionary_leave) drives the web form, the phone
    app and the server-side validation, so a question cannot exist on the
    screen but not in the check, or the other way round.

    Returns `discretionary: false` for every statutory type, which is the
    signal to render the ordinary short form.
    """
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied

    from hris import discretionary_leave as dl
    type_code = (request.query_params.get('type') or '').lower().strip()
    if not dl.is_discretionary(type_code):
        return Response({'discretionary': False, 'questions': []})

    profile = _profile_for(request.user)
    annual_available = None
    history = {'count': 0, 'days': 0.0, 'months': 12, 'recent': []}
    if profile is not None:
        from hris.leave_balance import balances_for_profile
        annual = next((b for b in balances_for_profile(profile)
                       if b['code'] == 'annual'), None)
        annual_available = float(annual['available']) if annual else 0.0
        history = dl.history_for(profile, type_code)

    return Response({
        'discretionary':   True,
        'type':            type_code,
        'notice_title':    dl.NOTICE_TITLE,
        'notice_body':     dl.NOTICE_BODY,
        'ack_text':        dl.ACK_TEXT,
        'min_words':       dl.MIN_WORDS,
        'proof_required':  type_code in dl.PROOF_REQUIRED_AT_APPLY,
        'proof_label':     dl.PROOF_LABEL.get(type_code, 'Supporting document'),
        'annual_available': annual_available,
        'history':         history,
        'questions':       dl.questions_for(type_code, annual_available),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_balances(request):
    """Return per-type entitlements + days used FYTD for the current user.

    Always returns the CoS rule set so the page can render every type
    even when the user has no balance row yet.
    """
    # Self-service: every employee sees their OWN balances (data below is
    # scoped to _profile_for(request.user)). CFO directive 2026-06-16.
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied

    profile = _profile_for(request.user)
    # Balance maths lives in hris.leave_balance (single source of truth, shared
    # with the leave-approval email) — CFO 2026-07-14.
    from hris.leave_balance import balances_for_profile
    balances = balances_for_profile(profile)
    return Response({
        'year':       timezone.now().year,
        'balances':   balances,
        'has_profile': profile is not None,
    })


def _is_genuine_manager(user) -> bool:
    """True if `user` is a real people-manager — appears as SOMEONE's
    HRISProfile.manager (mirrors the 'mgr' detection in core.hris_access
    .hris_role, so 'manager' means the same thing everywhere)."""
    emp = getattr(user, 'employee_record', None)
    if emp is None:
        return False
    return HRISProfile.objects.filter(manager_id=emp.id).exists()


def leave_manager_employee_ids(user) -> set:
    """Employee ids `user` may pick to review their leave — COMPANY-SCOPED.

    CFO directive 2026-07-25 (Motlatsi Molefe / Unicoin): staff of one group
    company must not be offered another company's managers. A Unicoin employee
    was shown the whole Alpha Direct management line.

    "Relevant to my company" is deliberately defined as *who manages my
    company's people*, not *who is payrolled by my company* — a genuine
    cross-entity manager (e.g. a group manager carried on the Alpha Direct
    payroll who runs Unicoin staff) must still appear to their own reports,
    while Alpha Direct managers with no Unicoin reports must not. So:

      * anyone who manages at least one person in MY company, PLUS
      * my own assigned line manager, always — HR set that link deliberately,
        so it wins even when it crosses a company boundary.

    Always returns a set of payroll.Employee ids — possibly EMPTY. There is
    deliberately no "unrestricted" escape hatch: a caller with no employee record
    gets an empty set, not the whole group's managers (DeepSeek review
    2026-07-25 flagged the earlier None-sentinel as an entity-isolation leak).
    Nothing is lost by it — apply_leave already rejects a caller with no
    HRISProfile, so such an account could never use the list anyway.

    Resolution deliberately uses `employee_record` only. _profile_for() also
    tries a second `UserProfile.employee` link, but core.models.UserProfile has
    no such field (and its accessor is `user.profile`, not `user.userprofile`) —
    that branch is vestigial and can never fire, so mirroring it here would be
    dead code guarding an impossible path. Re-check this if UserProfile ever
    gains an employee FK; test_userprofile_has_no_employee_link enforces it.
    """
    from payroll.models import Employee

    emp = getattr(user, 'employee_record', None)
    if emp is None:
        return set()

    ids: set = set()
    if emp.company_id is not None:
        ids.update(HRISProfile.objects
                   .filter(employee__company_id=emp.company_id)
                   .exclude(manager_id__isnull=True)
                   .values_list('manager_id', flat=True))

    profile = _profile_for(user)
    if profile is not None and profile.manager_id:
        ids.add(profile.manager_id)

    # A leaver must never be offered as an approver, even if their login is still
    # switched on (DeepSeek review 2026-07-25) — leave would route to someone who
    # has left. `on_leave`/`suspended` managers still approve; only terminated go.
    if ids:
        ids = set(Employee.objects.filter(id__in=ids)
                  .exclude(status=Employee.Status.TERMINATED)
                  .values_list('id', flat=True))

    # Never offer yourself as your own approver.
    ids.discard(emp.id)
    return ids


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_managers(request):
    """GET /hris/api/leave-managers/ — who an employee may choose to review
    their leave request (CFO directive 2026-07-15). Restricted to genuine
    people-managers (same definition core.hris_access uses for the 'mgr'
    role) — not the whole HR/Finance approver whitelist, and not EXCO.
    Any employee who can apply for leave may call this (self-service tier).

    Company-scoped since CFO directive 2026-07-25 — see
    leave_manager_employee_ids. apply_leave validates the submitted choice
    against the SAME helper, so the two can never drift apart.
    """
    denied = _gate(request, capability='apply_leave')
    if denied is not None:
        return denied

    from payroll.models import Employee

    # Bug caught in verification (2026-07-15): querying HRISProfile for the
    # manager's OWN profile row wrongly required the manager to have an
    # HRISProfile of their own — but "is a manager" is an Employee-level fact
    # (being referenced by someone ELSE's HRISProfile.manager), not dependent
    # on having a profile row themselves (plenty of real managers don't have
    # one). Query payroll.Employee directly instead — same source
    # _is_genuine_manager already uses correctly.
    allowed_ids = leave_manager_employee_ids(request.user)
    managers = (Employee.objects
               .filter(id__in=allowed_ids,
                       user__isnull=False, user__is_active=True)
               .exclude(user__email='')
               .select_related('user')
               .order_by('full_name'))

    my_profile = _profile_for(request.user)
    default_manager_user_id = None
    if my_profile is not None and my_profile.default_leave_approver_id:
        chosen = my_profile.default_leave_approver
        chosen_emp = getattr(chosen, 'employee_record', None)
        if (chosen.is_active and chosen_emp is not None
                and chosen_emp.id in allowed_ids):
            default_manager_user_id = chosen.id
    if default_manager_user_id is None and my_profile is not None and my_profile.manager_id:
        # Only pre-select a manager the server would actually accept — otherwise
        # an employee whose line manager has since left gets a pre-filled choice
        # that 400s on submit. The onboarding workflow may set an explicit default
        # leave approver; when it does not, the line manager remains the fallback.
        if my_profile.manager_id in allowed_ids:
            default_manager_user_id = getattr(my_profile.manager.user, 'id', None)

    payload = {
        'managers': [
            {'id': m.user_id, 'name': m.full_name, 'department': m.department or ''}
            for m in managers
        ],
        'default_manager_id': default_manager_user_id,
    }
    # An empty list used to make the picker vanish silently, and the request
    # would then be created with no approver at all — build_leave_email returns
    # None in that case, so nobody was ever told (silent black hole). Tell the
    # employee plainly instead; the frontend renders this.
    #
    # SAY WHICH of the two causes it is. An empty list has two very different
    # meanings and the message used to give only one of them:
    #
    #   (a) the caller has no payroll.Employee record at all — leave_manager_
    #       employee_ids() returns an empty set on the first line. This is by far
    #       the common case (99 of 188 active accounts on 31 Jul 2026), and the
    #       old wording blamed "your company" for having no approver, which sent
    #       HR looking at the company's management line instead of at the one
    #       missing link on that person's own account. Reported by an employee
    #       who then had to be told his own message was misleading.
    #
    #   (b) the caller IS linked, but nobody in their company manages anyone —
    #       genuinely a company-level gap, which is what the old text described.
    if not payload['managers']:
        if getattr(request.user, 'employee_record', None) is None:
            payload['empty_reason'] = (
                'Your staff record has not been linked to your login yet, so we cannot '
                'tell who approves your leave. Ask HR to link your employee record — '
                'once that is done your line manager appears here automatically.'
            )
            payload['empty_cause'] = 'no_employee_record'
        else:
            payload['empty_reason'] = (
                'No leave approver has been set up for your company yet. '
                'Ask HR to set your line manager before applying.'
            )
            payload['empty_cause'] = 'no_company_manager'
    return Response(payload)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def apply_leave(request):
    """Submit a leave request.

    Multipart body:
      type        : annual | sick | maternity | paternity | compassionate | study | special
      start_date  : YYYY-MM-DD
      end_date    : YYYY-MM-DD
      reason      : optional text
      certificate : file (PDF / JPG / PNG, ≤ 5 MB) — REQUIRED for sick leave

    Enforces Alpha Direct CoS:
      * §7.5.1 — Annual cannot be taken in advance of accrual.
      * §7.6.1 — Sick MUST include a medical certificate.
    """
    denied = _gate(request, capability='apply_leave')
    if denied is not None:
        return denied

    # HR-initiated application on behalf of an employee (bug f4464440 req 4 —
    # for staff with no system access at leave commencement, e.g. maternity).
    # Only HR with amendment rights may do it; the leave belongs to the target,
    # and the initiator can never also be the approver (enforced below).
    on_behalf_id = (request.data.get('on_behalf_employee_id') or '').strip()
    on_behalf = bool(on_behalf_id)
    if on_behalf:
        from core.hris_access import user_can_amend_hris
        from payroll.models import Employee as _Emp
        if not user_can_amend_hris(request.user):
            return Response(
                {'detail': 'Only HR with amendment rights can apply for leave on '
                           'behalf of an employee.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        target_emp = _Emp.objects.filter(pk=on_behalf_id).select_related('company').first()
        if target_emp is None:
            return Response({'detail': 'That employee was not found.'},
                            status=status.HTTP_400_BAD_REQUEST)
        profile = HRISProfile.objects.filter(employee=target_emp).first()
        if profile is None:
            return Response(
                {'detail': f'{target_emp.full_name} has no HR profile yet, so leave '
                           'cannot be applied on their behalf. Onboard them first.'},
                status=status.HTTP_400_BAD_REQUEST)
        leave_owner_user = target_emp.user      # may be None (no system access)
    else:
        profile = _profile_for(request.user)
        leave_owner_user = request.user
        if profile is None:
            return Response(
                # Plain English, and the SAME cause as the empty approver list above.
                # "HRISProfile" is an internal table name; an employee reading it has
                # no idea what to ask for. Say what is missing and who fixes it.
                {'detail': 'Your staff record has not been linked to your login yet, so leave '
                           'cannot be applied for. Ask HR to link your employee record.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    rules = get_leave_rules()
    type_code = (request.data.get('type') or '').lower().strip()
    if type_code not in rules:
        return Response(
            {'detail': f'Unknown leave type "{type_code}".',
             'allowed_types': sorted(rules.keys())},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Gender-locked leave (maternity → female, paternity → male). Bug 77137f16.
    # Enforced here so it can't be applied for or assigned regardless of the UI.
    greq = rules[type_code].get('gender')
    if greq:
        emp_gender = (profile.gender or '').strip()
        if not emp_gender:
            return Response(
                {'detail': (f'{type_code.capitalize()} leave is restricted by gender, '
                            'but your gender is not recorded on your HR profile. '
                            'Ask HR to set it first.')},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if emp_gender != greq:
            label = {'F': 'female', 'M': 'male'}.get(greq, greq)
            return Response(
                {'detail': f'{type_code.capitalize()} leave is only available to {label} employees.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Overdue-task hard gate (CFO 2026-08-12). omni used to only SHOW an
    # applicant's at-risk tasks to the approver — it never stopped the
    # application, which is how someone could book leave with overdue work open.
    # CFO ruling: NO leave of ANY type may be applied for by ANYONE — the CFO
    # included, no exemptions — while they have an overdue task. Clear or hand
    # over the work first. Applied before dates/cert/accrual so the reason is
    # always the tasks, never a secondary validation error.
    from taskboard.services import overdue_tasks_for
    # The gate is about the person whose leave this is. For an HR-initiated
    # application the owner may have no login (nothing to chase), so skip.
    overdue = overdue_tasks_for(leave_owner_user) if leave_owner_user else []
    if overdue:
        preview = '; '.join(
            f'"{t.title}" (was due {t.due_at.isoformat()})' for t in overdue[:10]
        )
        return Response(
            {'detail': (
                f'You have {len(overdue)} overdue task(s), so leave cannot be '
                f'applied for until they are finished or handed over to someone '
                f'else. Overdue: {preview}.'),
             'overdue_tasks': [
                 {'title': t.title, 'due_at': t.due_at.isoformat()} for t in overdue
             ]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        start = _dt.date.fromisoformat(str(request.data.get('start_date') or ''))
    except ValueError:
        return Response(
            {'detail': 'start_date must be ISO format YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if type_code == 'maternity':
        # Maternity is a statutory calendar-day entitlement that must not be
        # mis-typed or shortened (bug f4464440, Oprah 2026-09-03). The end date
        # is SYSTEM-LOCKED: computed from a single statutory parameter (ELRA
        # s.222 = 98 calendar days, day 1 inclusive) and NOT editable by the
        # applicant or the approving manager — whatever end_date they sent is
        # ignored. All 98 days count as full days (day types are forced to
        # 'full' above).
        stat_days = int(rules['maternity']['days'])
        end = start + _dt.timedelta(days=stat_days - 1)
    else:
        try:
            end = _dt.date.fromisoformat(str(request.data.get('end_date') or ''))
        except ValueError:
            return Response(
                {'detail': 'end_date must be ISO format YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if end < start:
            return Response(
                {'detail': 'end_date is before start_date.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Half-day support (Kago Tshutlhedi 2026-07-13). Day-type per boundary;
    # default FULL keeps legacy behaviour. Half-days are a working-day concept,
    # so they are ignored for maternity (calendar-day entitlement).
    _DAY_TYPES = {c[0] for c in LeaveRequest.DayType.choices}   # full / am / pm
    start_day_type = (request.data.get('start_day_type') or 'full').lower().strip()
    end_day_type   = (request.data.get('end_day_type')   or 'full').lower().strip()
    if start_day_type not in _DAY_TYPES or end_day_type not in _DAY_TYPES:
        return Response(
            {'detail': f'Day type must be one of {sorted(_DAY_TYPES)}.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if type_code == 'maternity':
        start_day_type = end_day_type = 'full'
    # Zero-length guard (req §3): a PM half-day start on the same date as an AM
    # half-day end is afternoon→morning of one day — a zero-length window.
    if start == end and start_day_type == 'pm' and end_day_type == 'am':
        return Response(
            {'detail': ('A PM half-day start cannot share the same date as an AM '
                        'half-day end — that is a zero-length request.')},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cert = request.FILES.get('certificate')
    if type_code == 'sick' and cert is None:
        return Response(
            {'detail': 'Sick leave requires a medical certificate (CoS §7.6.1).'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Maternity cannot be submitted without evidence of the confinement /
    # commencement date (bug f4464440, requirement 2 — ELRA s.222).
    if type_code == 'maternity' and cert is None:
        return Response(
            {'detail': ('Maternity leave requires a medical certificate stating the '
                        'confinement or commencement date (ELRA s.222).')},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if cert is not None and cert.size > 5 * 1024 * 1024:
        return Response(
            {'detail': 'Medical certificate exceeds 5 MB limit.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Enforce the documented PDF/JPG/PNG contract at the door — the cert is later
    # served inline to HR, so a .html/.svg upload would be a stored-XSS vector.
    if cert is not None:
        import os as _os
        from hris.leave_admin import CERT_CONTENT_TYPES
        if _os.path.splitext(cert.name)[1].lower() not in CERT_CONTENT_TYPES:
            return Response(
                {'detail': 'The medical certificate must be a PDF, JPG or PNG file.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Annual leave cannot be in advance of accrual (CoS §7.5.1).
    #
    # This gate used to recompute the accrual itself — entitlement × the CURRENT
    # month number / 12 — which had two faults. It credited the running month on
    # its first day (so August was bookable on 1 August, against ELRA s.219's
    # "accrues progressively"), and it ignored LeaveOpeningBalance entirely, so
    # everyone HR uploaded was gated against a number unrelated to their real
    # balance. leave_balance.balances_for_profile is the single source of truth
    # this module's docstring promised; ask it, don't re-derive it. What the
    # employee is shown on screen and what the gate allows are now the same
    # number, by construction.
    if type_code == 'annual':
        from hris.leave_balance import balances_for_profile
        annual = next((b for b in balances_for_profile(profile)
                       if b['code'] == 'annual'), None)
        available = Decimal(str(annual['available'])) if annual else Decimal('0')
        # requested must be in WORKING days to match the balance engine (also
        # working-day based). Using calendar days here falsely blocked any
        # annual request spanning a weekend. compute_days() on a transient row
        # (no leave_type → working-day path) gives the exact figure that will
        # be stored.
        requested = LeaveRequest(
            start_date=start, end_date=end,
            start_day_type=start_day_type, end_day_type=end_day_type,
        ).compute_days()
        if requested > available:
            return Response(
                # :g, not :.1f — both figures are already floored, and .1f ROUNDS:
                # an available balance of 14.58 read "14.6d" inside the very
                # message that enforces never-round-up, and at the edge it could
                # print "Available now: 2.5d, requested: 2.5d" while refusing.
                {'detail': ('Annual leave cannot be taken in advance of accrual '
                            f'(CoS §7.5.1). Available now: {float(available):g}d, '
                            f'requested: {float(requested):g}d. Annual leave is '
                            'credited at the end of each month, so your balance '
                            'rises when the month closes.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

    leave_type, _ = LeaveType.objects.get_or_create(
        code=type_code,
        defaults={'name': type_code.capitalize() + ' Leave',
                  'default_annual_days': rules[type_code]['days']},
    )

    # Overlap guard — LeaveRequest.clean() has this check but .create() never
    # runs clean(), so double-booking was possible. Enforce it on the request
    # path: reject a new request that overlaps an existing PENDING/APPROVED one
    # for the same employee.
    clash = (LeaveRequest.objects
             .filter(profile=profile,
                     status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.PENDING],
                     start_date__lte=end, end_date__gte=start)
             .first())
    if clash is not None:
        return Response(
            {'detail': (f'This overlaps an existing {clash.status} leave request '
                        f'({clash.start_date} → {clash.end_date}). Cancel or amend that one first.')},
            status=status.HTTP_409_CONFLICT,
        )

    # Who should review this (CFO directive 2026-07-15) — restricted to
    # genuine people-managers (hris.feature_views.leave_managers is the same
    # list the picker fetches). Optional: omitted → legacy behaviour (falls
    # back to HRISProfile.manager, see hris.leave_email.build_leave_email).
    requested_approver = None
    approver_id = (request.data.get('approver_id') or '').strip()
    if on_behalf:
        # HR-initiated: Unami Butale is the designated approver (req 4). If the
        # initiator IS Unami (applying for a delegated HR officer's own leave —
        # the documented exception) the CFO stands in via the existing hierarchy,
        # so the applicant is never also the approver.
        from django.contrib.auth.models import User
        from hris.amendment_service import UNAMI_EMAIL, CFO_EMAIL
        initiator_is_unami = (getattr(request.user, 'email', '') or '').lower() == UNAMI_EMAIL.lower()
        if initiator_is_unami:
            requested_approver = User.objects.filter(email__iexact=CFO_EMAIL, is_active=True).first()
        else:
            requested_approver = (User.objects.filter(email__iexact=UNAMI_EMAIL, is_active=True).first()
                                  or User.objects.filter(email__iexact=CFO_EMAIL, is_active=True).first())
        if requested_approver is not None and requested_approver.pk == request.user.pk:
            return Response(
                {'detail': 'The person applying on behalf of an employee cannot also '
                           'approve it. A different approver is required.'},
                status=status.HTTP_400_BAD_REQUEST)
    elif approver_id:
        from django.contrib.auth.models import User
        requested_approver = User.objects.filter(pk=approver_id, is_active=True).first()
        # Validate against the SAME company-scoped set the picker offers
        # (CFO 2026-07-25). Checking only "is a manager somewhere" let a
        # Unicoin employee post an Alpha Direct manager's id straight past the
        # scoped dropdown — the isolation has to hold on the server, not just
        # in the UI.
        # You can never review your own leave (bug 8c165d53 — the picker
        # excludes yourself, but the server must reject a self-id posted past it).
        if requested_approver is not None and requested_approver.pk == request.user.pk:
            return Response(
                {'detail': 'You cannot choose yourself to review your own leave. '
                           'Pick a different manager.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        allowed_ids = leave_manager_employee_ids(request.user)
        chosen_emp = getattr(requested_approver, 'employee_record', None) \
            if requested_approver is not None else None
        ok = (requested_approver is not None
              and chosen_emp is not None
              and _is_genuine_manager(requested_approver)
              and chosen_emp.id in allowed_ids)
        if not ok:
            return Response(
                {'detail': 'Choose a manager from the list to review this request.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
    else:
        # A controlled onboarding request may set an explicit default leave
        # reviewer. Validate it against the same active, company-scoped manager
        # set used by the picker. If it is no longer valid, preserve the legacy
        # line-manager fallback rather than routing leave to a stale login.
        default_approver = getattr(profile, 'default_leave_approver', None)
        if default_approver is not None:
            chosen_emp = getattr(default_approver, 'employee_record', None)
            allowed_ids = leave_manager_employee_ids(request.user)
            if (default_approver.is_active and chosen_emp is not None
                    and _is_genuine_manager(default_approver)
                    and chosen_emp.id in allowed_ids):
                requested_approver = default_approver
        if requested_approver is None and profile.manager_id is None:
            # No approver chosen AND no line manager on file → build_leave_email
            # returns None, so the request would sit PENDING with nobody notified.
            return Response(
                {'detail': ('Choose a manager to review this request. If the list is '
                            'empty, ask HR to set your line manager first.')},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # Reason handling (Unami Butale, HR, 2026-07-27 — via the CFO). HR asked for
    # a FIXED reason category instead of a mandatory free-text "why": an employee
    # is not obliged to disclose why they take leave, and forcing a justification
    # is a prejudice/discrimination exposure. This REVERSES the 2026-07-22 15-word
    # mandate at HR's request. `reason_category` is a fixed pick-list (which
    # includes "Prefer not to say"); the free-text `reason` is now OPTIONAL.
    reason = (request.data.get('reason') or '').strip()
    reason_category = (request.data.get('reason_category') or '').strip().lower()
    _valid_categories = {c[0] for c in LeaveRequest.ReasonCategory.choices}
    if reason_category and reason_category not in _valid_categories:
        return Response(
            {'detail': f'reason_category must be one of {sorted(_valid_categories)}.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Don't file a leave with no reason at all: require a category OR a free-text
    # note. Both the Omni screen and the Nexus app now send a category (the app
    # caught up 2026-08-03); accepting either still keeps an old cached phone
    # bundle working without forcing anyone to disclose WHY.
    if not reason_category and not reason:
        return Response(
            {'detail': 'Pick a reason category for this leave (you do not have to explain further).'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Discretionary leave: the hard gate (CFO 2026-09-10) ─────────────────
    # Compassionate / study / special only. Statutory leave falls straight
    # through this block unchanged — the paragraph above still stands for it.
    from hris import discretionary_leave as dl
    policy_answers: dict = {}
    policy_ack = False
    if dl.is_discretionary(type_code) and on_behalf:
        # HR applying for someone else cannot answer these questions, write
        # their motivation, or sign their undertaking — those are the
        # employee's own words and the whole point of the control. The
        # on-behalf route exists for people with no login (maternity), which
        # is not a discretionary type. Refuse it here as well as on screen.
        return Response(
            {'detail': (f'{type_code.capitalize()} leave is granted at the company\'s '
                        'discretion. The employee answers the questions and writes the '
                        'motivation themselves, and the CFO signs it off — it cannot be '
                        'applied for on their behalf. Ask them to apply on their own '
                        'Leave screen.')},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if dl.is_discretionary(type_code):
        import json as _json
        raw = request.data.get('policy_answers')
        if isinstance(raw, dict):
            policy_answers = raw
        elif raw:
            try:
                policy_answers = _json.loads(raw)
            except (TypeError, ValueError):
                policy_answers = {}
        if not isinstance(policy_answers, dict):
            policy_answers = {}
        # Only keep answers to questions we actually asked, as text — the field
        # is JSON, so an unbounded client payload would otherwise be stored.
        _asked = {q['key'] for q in dl.questions_for(type_code, None)}
        policy_answers = {k: str(v)[:2000] for k, v in policy_answers.items()
                          if k in _asked}
        policy_ack = str(request.data.get('policy_ack') or '').strip().lower() in (
            '1', 'true', 'yes', 'on')

        from hris.leave_balance import balances_for_profile
        _annual = next((b for b in balances_for_profile(profile)
                        if b['code'] == 'annual'), None)
        annual_available = Decimal(str(_annual['available'])) if _annual else Decimal('0')

        problems = dl.validate(
            type_code,
            answers=policy_answers, reason=reason, ack=policy_ack,
            start_date=start, today=timezone.localdate(),
            annual_available=annual_available, has_document=cert is not None,
        )
        if problems:
            return Response(
                {'detail': ' '.join(problems), 'errors': problems,
                 'policy': 'discretionary_leave'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Freeze the annual balance they were holding when they asked. By the
        # time the manager and the CFO read this the live figure has moved, and
        # "how many annual days was this person sitting on?" is the whole point.
        policy_answers[dl.ANNUAL_AT_APPLY_KEY] = f'{float(annual_available):g}'

    with transaction.atomic():
        lr = LeaveRequest.objects.create(
            profile=profile,
            leave_type=leave_type,
            start_date=start,
            end_date=end,
            start_day_type=start_day_type,
            end_day_type=end_day_type,
            # days is recomputed server-side in save() via compute_days(),
            # which now applies the half-day adjustment.
            days=Decimal((end - start).days + 1),
            reason=reason,
            reason_category=reason_category,
            status=LeaveRequest.Status.PENDING,
            requested_approver=requested_approver,
            policy_answers=policy_answers,
            policy_ack=policy_ack,
        )
        if cert is not None:
            lr.medical_certificate.save(cert.name, cert, save=True)

    # Audit trail for an HR-initiated application (bug f4464440 req 4/5): record
    # who applied on whose behalf, and who the enforced approver is.
    if on_behalf:
        from core.models import AuditLog
        AuditLog.objects.create(
            table_name='LeaveRequest', record_id=str(lr.pk),
            action=AuditLog.Action.CREATE, user=request.user,
            description=(f'HR-initiated {leave_type.name} for {profile.employee.full_name} '
                         f'({start} → {end}); approver '
                         f'{getattr(requested_approver, "email", "—")}. Applicant is not approver.'),
        )

    # Long-overdue-task gate (CFO 2026-08-07): applying with work more than 2
    # days past due does not block the application, but a CEO/CFO must
    # countersign before the manager can approve it. Sick / compassionate /
    # maternity / paternity leave is always exempt — nobody chases a task
    # before reporting they are ill.
    #
    # This is deliberately fail-OPEN: a broken gate must never stop somebody
    # applying for leave. But it must never fail SILENTLY either, or a bug
    # would quietly switch the control off with nobody the wiser — so the
    # failure is logged at ERROR with the person and the request on it
    # (DeepSeek review 2026-08-07).
    signoff = None
    try:
        from hris import exec_signoff_service
        from hris.exec_signoff_models import ExecSignoff
        signoff = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, request.user, leave_type=leave_type)
    except Exception:   # noqa: BLE001 — the gate must never break submit
        log.exception('OVERDUE GATE FAILED OPEN on leave %s for user id %s — the '
                      'request was accepted WITHOUT the executive check.',
                      lr.pk, getattr(request.user, 'id', None))

    # Discretionary leave ALWAYS goes to the CFO, overdue work or not
    # (CFO 2026-09-10). Raised after the overdue gate so that when both apply
    # the single sign-off carries both reasons and is upgraded to CFO-only.
    #
    # FAIL SAFE, not fail open, and louder than the overdue gate: if this
    # cannot be raised the request must NOT sit in a normal manager's queue
    # looking like ordinary leave, because the manager would approve it and the
    # control would have silently switched itself off. So the whole submission
    # is rolled back and the applicant is told to try again.
    if dl.is_discretionary(type_code):
        try:
            from hris import exec_signoff_service as _svc
            policy_so = _svc.require_leave_policy_signoff(
                lr, request.user,
                reason=dl.signoff_reason(type_code, profile.employee.full_name, lr.days),
            )
        except Exception:   # noqa: BLE001
            policy_so = None
            log.exception('DISCRETIONARY LEAVE SIGN-OFF FAILED for leave %s '
                          '(user id %s).', lr.pk, getattr(request.user, 'id', None))
        if policy_so is None:
            lr.delete()
            return Response(
                {'detail': ('This leave needs the CFO to sign it off and that could '
                            'not be set up just now, so nothing has been filed. '
                            'Please try again, and tell IT if it keeps failing.')},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        signoff = signoff or policy_so

    # Notify the requester's manager (req §4 — email includes the day-type
    # breakdown). Best-effort; a flaky mailer never blocks the application.
    try:
        from core.notifications import notify_leave_pending_approval
        notify_leave_pending_approval(lr)
    except Exception:   # noqa: BLE001 — notification must never break submit
        pass

    from hris import overdue_gate
    why = overdue_gate.signoff_payload(signoff)
    message = (f'Leave request submitted for {leave_type.name} '
               f'({lr.day_breakdown()}). Awaiting manager approval.')
    if signoff is not None:
        message = (f'Leave request submitted for {leave_type.name} '
                   f'({lr.day_breakdown()}). {why["overdue_message"]}')
    # Someone who ALSO has overdue work gets the overdue wording above, which
    # says nothing about staying at work. Both are true, so say both.
    if dl.is_discretionary(type_code) and 'at work' not in message:
        message = (f'{message} This leave is granted at the company\'s discretion, '
                   f'so the CFO signs it off after your manager. You must be at '
                   f'work until you are told it is approved.')

    return Response(
        {'id': str(lr.pk),
         'status': lr.status,
         'days': float(lr.days),
         'day_breakdown': lr.day_breakdown(),
         **why,
         'message': message},
        status=status.HTTP_201_CREATED,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_daily_summary(request):
    """GET /hris/api/leave-daily-summary/ — today's leave activity for the HR
    dashboard (CFO directive 2026-07-15): how many requests came in today and
    how many were approved today, plus a short preview list of each. Manager/
    HR tier (capability 'view_team') — never ess, mirrors leave_report.
    """
    gate = _gate(request, capability='view_team')
    if gate is not None:
        return gate

    today = timezone.localdate()
    _PREVIEW = 8

    requested_today = (LeaveRequest.objects
                       .filter(created_at__date=today)
                       .select_related('profile__employee', 'leave_type')
                       .order_by('-created_at'))
    approved_today = (LeaveRequest.objects
                      .filter(status=LeaveRequest.Status.APPROVED, decided_at__date=today)
                      .select_related('profile__employee', 'leave_type', 'approver')
                      .order_by('-decided_at'))

    def _row(lr, *, decided=False):
        row = {
            'id': str(lr.pk),
            'employee': lr.profile.employee.full_name if lr.profile_id else '—',
            'leave_type': lr.leave_type.name if lr.leave_type_id else 'Leave',
            'day_breakdown': lr.day_breakdown(),
            'status': lr.status,
        }
        if decided:
            row['approver'] = (lr.approver.get_full_name() or lr.approver.username) if lr.approver_id else ''
        return row

    return Response({
        'date': today.isoformat(),
        'requested_today_count': requested_today.count(),
        'approved_today_count': approved_today.count(),
        'requested_today': [_row(lr) for lr in requested_today[:_PREVIEW]],
        'approved_today': [_row(lr, decided=True) for lr in approved_today[:_PREVIEW]],
    })


# ─── Performance assessment (PMS) ─────────────────────────────────────────────

POTENTIAL_DIMENSIONS = (
    'learning_agility', 'leadership_capacity',
    'aspiration_drive', 'adaptability',
)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_assessment(request):
    """Submit a 1-4 PMS review for one direct report.

    Body:
      employee_id : HRISProfile id of the person being reviewed
      period      : e.g. 'FY26-H1'
      competencies: { 'communication': 3, 'execution': 4, ... }  (1-4 scale)
      values      : [ 3, 4, 3, ... ]                              (1-4 scale)
      potential   : { 'learning_agility': 3, ... }                (1-4 scale)
      okrs        : [ { name, weight_pct, score_h1, score_h2 }, ... ]
      narrative   : optional manager note

    Weighting (per Unami's spec): Competencies 80%, Values 20%.
    """
    denied = _gate(request, capability='assess_team')
    if denied is not None:
        return denied

    target_id = request.data.get('employee_id') or ''
    period = (request.data.get('period') or '').strip()
    if not target_id or not period:
        return Response(
            {'detail': 'employee_id and period are required.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # pk is a UUID: anything that is not one makes the queryset raise, which
    # surfaced as a 500 rather than a plain "bad request" (found 2026-08-09).
    try:
        target = HRISProfile.objects.filter(pk=target_id).first()
    except (ValueError, TypeError, DjangoValidationError):
        return Response({'detail': 'employee_id is not a valid id.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if target is None:
        return Response({'detail': 'employee_id not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    # The target must actually report to the caller. This endpoint took an
    # employee_id and wrote a review against it with no ownership check at all —
    # any manager who knew the shared HRIS password could file feedback on
    # ANYONE, in any team or company. That was masked by the password; on
    # 2026-08-09 'assess_team' joined TEAM_CAPS so managers no longer need it,
    # which would have made the hole trivially reachable. HR and the privileged
    # roles keep the company-wide view they already had.
    if hris_role(request.user) not in {'hr', 'hris', 'admin', 'ceo', 'superadmin'}:
        me = _resolve_manager_employee(request.user)
        target_mgr_id = getattr(target, 'manager_id', None)
        if me is None or target_mgr_id is None or target_mgr_id != me.pk:
            return Response(
                {'detail': 'You can only record feedback for your own team.'},
                status=status.HTTP_403_FORBIDDEN,
            )

    competencies = request.data.get('competencies') or {}
    values       = request.data.get('values') or []
    potential    = request.data.get('potential') or {}
    okrs         = request.data.get('okrs') or []
    narrative    = (request.data.get('narrative') or '').strip()

    # Validate the 1-4 scale on every numeric field. The Unami spec is
    # strict here — anything outside [1, 4] gets rejected so reports
    # don't end up with a 5/5 by accident.
    def _validate_scale(section_name: str, values_iter) -> Response | None:
        for v in values_iter:
            try:
                n = float(v)
            except (TypeError, ValueError):
                return Response(
                    {'detail': f'{section_name}: non-numeric score "{v}".'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if n < 1 or n > 4:
                return Response(
                    {'detail': f'{section_name}: score {n} outside 1-4 scale.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        return None

    for chk in (
        _validate_scale('competencies', (competencies or {}).values() if isinstance(competencies, dict) else []),
        _validate_scale('values',       values if isinstance(values, list) else []),
        _validate_scale('potential',    (potential or {}).values() if isinstance(potential, dict) else []),
    ):
        if chk is not None:
            return chk

    # Compute composite score per Unami's weighting.
    def _avg(xs):
        xs = [float(x) for x in xs if x not in (None, '')]
        return sum(xs) / len(xs) if xs else 0.0

    comp_avg = _avg((competencies or {}).values() if isinstance(competencies, dict) else [])
    val_avg  = _avg(values if isinstance(values, list) else [])
    composite = round(comp_avg * 0.8 + val_avg * 0.2, 3)

    with transaction.atomic():
        pr = PerformanceReview.objects.create(
            profile=target,
            period=period,
            # SUBMITTED (not the DRAFT default) so the review actually feeds the
            # 9-box / succession / IDP views — _latest_review only reads
            # SUBMITTED/FINALISED. (HRIS audit 2026-06-09, BUG-B.)
            status=PerformanceReview.Status.SUBMITTED,
            competency_scores=competencies if isinstance(competencies, dict) else {},
            values_scores=values if isinstance(values, list) else [],
            # potential_scores MUST be a list of numbers — _avg() iterates it.
            # The form posts a dict; storing it raw made _avg iterate the keys
            # (strings) → 0 for everyone → whole 9-box vertical axis broken.
            # Coerce dict→values. (HRIS audit 2026-06-09, BUG-A.)
            potential_scores=(list(potential.values()) if isinstance(potential, dict)
                              else (potential if isinstance(potential, list) else [])),
        )
        if narrative:
            # We piggy-back on the model — narrative lives in a field if it
            # exists, otherwise it's appended to the JSON blob for now.
            if hasattr(pr, 'narrative'):
                pr.narrative = narrative
                pr.save(update_fields=['narrative'])

        if isinstance(okrs, list):
            for o in okrs:
                try:
                    OKR.objects.create(
                        profile=target,
                        period=period,
                        name=str(o.get('name') or '').strip(),
                        weight_pct=Decimal(str(o.get('weight_pct') or 0)),
                        score_h1=Decimal(str(o.get('score_h1') or 0)),
                        score_h2=Decimal(str(o.get('score_h2') or 0)),
                    )
                except Exception:
                    continue

    return Response({
        'review_id':       str(pr.pk),
        'employee':        target.employee.full_name,
        'period':          period,
        'composite_score': composite,
        'competency_avg':  round(comp_avg, 3),
        'values_avg':      round(val_avg, 3),
        'weighting':       {'competencies_pct': 80, 'values_pct': 20},
        'message':         'Assessment recorded.',
    }, status=status.HTTP_201_CREATED)


# ─── ITW8 — BURS Tax Certificate ──────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_itw8(request):
    """Return the figures the BURS ITW8 certificate needs for a tax year.

    Query: ?year=2025

    The figures are computed from posted Payslip rows when the payroll
    module is wired up. Until then we surface the structure with zeros so
    the frontend page renders deterministically and HR knows where the
    numbers will land.
    """
    denied = _gate(request, capability='view_own_payslip')
    if denied is not None:
        return denied

    try:
        year = int(request.query_params.get('year') or timezone.now().year)
    except (TypeError, ValueError):
        return Response({'detail': 'year must be an integer.'},
                        status=status.HTTP_400_BAD_REQUEST)

    basic = Decimal('0')
    housing = Decimal('0')
    transport = Decimal('0')
    cellphone = Decimal('0')
    other_allowances = Decimal('0')
    paye = Decimal('0')
    is_live = False

    # Locate the linked payroll.Employee row for the requesting user.
    # OneToOneField is User.employee_record (payroll/models.py:73).
    # If the caller is HR / Finance pulling someone else's ITW8 they pass
    # ?employee=<uuid|number> which the gate above already permits.
    emp = None
    try:
        from payroll.models import Employee
        emp = getattr(request.user, 'employee_record', None)
        # HR / Finance can lookup by query param
        ref = (request.query_params.get('employee') or '').strip()
        if ref and (request.user.is_superuser or
                    _has_capability(request.user, 'view_others_payslip')):
            looked = (Employee.objects.filter(employee_number=ref).first()
                      or Employee.objects.filter(id=ref).first())
            # Entity scope (CFO 2026-06-16): a scoped user can't pull an
            # ITW8 for an employee outside their granted entities.
            if looked is not None:
                from core.mixins import scoped_company_ids
                _ids = scoped_company_ids(request)
                if _ids is None or (_ids and str(looked.company_id) in _ids):
                    emp = looked
    except Exception:  # noqa: BLE001
        pass

    # Pull from posted payslips: filter by tax-year against period.end_date.
    try:
        from payroll.models import Payslip, PayslipLine, PayslipComponent
        if emp:
            qs = (Payslip.objects.filter(employee=emp,
                                          period__end_date__year=year)
                                  .select_related('period'))
            for ps in qs:
                paye += Decimal(getattr(ps, 'paye_amount', 0) or 0)
                for ln in (PayslipLine.objects.filter(payslip=ps)
                                                 .select_related('component')):
                    comp  = ln.component
                    code  = (getattr(comp, 'code', '') or '').upper()
                    name  = (getattr(comp, 'name', '') or '').lower()
                    kind  = (getattr(comp, 'kind', '') or '').lower()
                    amt   = Decimal(getattr(ln, 'amount', 0) or 0)
                    # PAYE handled at payslip level, skip the line copy
                    if 'paye' in code.lower() or kind in ('tax','employee_tax'):
                        continue
                    # Skip deductions for the earnings buckets
                    if kind == 'deduction' or amt < 0:
                        continue
                    # Bucket — BURS earnings classes
                    if code in ('BASIC', 'BASIC_SALARY') or 'basic salary' in name:
                        basic += amt
                    elif code in ('HOUSING_ALLOWANCE', 'HOUSING_BENEFIT', 'FURNITURE_BENEFIT') or 'hous' in name:
                        housing += amt
                    elif 'transp' in name or 'travel' in name or code in ('TRANSPORT_ALLOWANCE','VEHICLE_ALLOWANCE'):
                        transport += amt
                    elif 'cell' in name or 'phone' in name or code == 'CELLPHONE_ALLOWANCE':
                        cellphone += amt
                    elif kind == 'earning' or 'allow' in name or 'commission' in name or 'bonus' in name:
                        other_allowances += amt
                is_live = True
    except Exception:  # noqa: BLE001 — never break the certificate render
        pass

    gross = basic + housing + transport + cellphone + other_allowances
    net   = gross - paye

    employee_name = emp.full_name if emp else ''
    employee_id_num = ''
    if emp:
        employee_id_num = (getattr(emp, 'national_id', '') or
                           getattr(emp, 'employee_number', '') or
                           getattr(emp, 'external_ref', ''))

    # Employer block from settings — falls back to known prod values
    from django.conf import settings
    employer_name = getattr(settings, 'BURS_EMPLOYER_NAME', '') or 'Alpha Direct Insurance Co.'
    employer_tin  = getattr(settings, 'BURS_EMPLOYER_TIN',  '') or '12345678-01-01'

    return Response({
        'is_live':            is_live,
        'tax_year':           year,
        'employee_name':      employee_name,
        'employee_id_number': employee_id_num,
        'employer_name':      employer_name,
        'employer_burs_tin':  employer_tin,
        'basic_salary':       float(basic),
        'allowances': {
            'housing':          float(housing),
            'transport':        float(transport),
            'cellphone':        float(cellphone),
            'other':            float(other_allowances),
        },
        'gross_earnings':     float(gross),
        'paye_deducted':      float(paye),
        'net_pay':            float(net),
    })


def _has_capability(user, capability: str) -> bool:
    """Tiny helper — check Django group / superuser flag."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    # Honour the HR-feature-access table if present
    try:
        from hris.models import HRISFeatureAccess
        return HRISFeatureAccess.objects.filter(user=user, **{capability: True}).exists()
    except Exception:  # noqa: BLE001
        return False


# ─── Bonus simulation ─────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bonus_simulate(request):
    """Run Unami's 3-layer bonus pool calculation.

    Body:
      org_multiplier  : float (e.g. 1.10 for 110% of target pool)
      dept_multiplier : float (e.g. 0.95)
      individual_score: float in [1, 4]  (1-4 PMS scale)
      target_bonus    : float (BWP)

    Formula:
      bonus = org × dept × (individual / 4) × target
    """
    denied = _gate(request)
    if denied is not None:
        return denied

    try:
        org   = float(request.data.get('org_multiplier'  or 0) or 0)
        dept  = float(request.data.get('dept_multiplier' or 0) or 0)
        ind   = float(request.data.get('individual_score' or 0) or 0)
        targ  = float(request.data.get('target_bonus'    or 0) or 0)
    except (TypeError, ValueError):
        return Response({'detail': 'All four inputs must be numeric.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if ind < 1 or ind > 4:
        return Response({'detail': 'individual_score must be in the 1-4 PMS scale.'},
                        status=status.HTTP_400_BAD_REQUEST)

    bonus = org * dept * (ind / 4.0) * targ
    return Response({
        'inputs':  {
            'org_multiplier':   org,
            'dept_multiplier':  dept,
            'individual_score': ind,
            'target_bonus':     targ,
        },
        'formula': 'org × dept × (individual / 4) × target',
        'layers': {
            'org_layer':        org * targ,
            'plus_dept':        org * dept * targ,
            'plus_individual':  org * dept * (ind / 4.0) * targ,
        },
        'bonus':   round(bonus, 2),
    })


# ---------------------------------------------------------------------------
# CFO directive 2026-05-20 (Manus HRIS audit Part 3) — 5 new endpoints
# ---------------------------------------------------------------------------


# 1 — Manager leave approval -------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_queue(request):
    """GET /hris/api/leave-requests/queue/
    Returns pending LeaveRequests the caller can decide on.
    Gated by the standard HRIS approver gate; the caller never sees
    their own request in the queue (segregation enforced at decide-time
    anyway).

    CFO 2026-08-07: gated on the 'approve_team_leave' capability rather than
    bare HRIS access, so a real line manager can see the requests routed to
    them. The routing filter below already limits everyone except HR to their
    own team, so this shows a manager only what was always theirs to decide.
    """
    gate = _gate(request, capability='approve_team_leave')
    if gate is not None:
        return gate
    qs = (LeaveRequest.objects
          .filter(status=LeaveRequest.Status.PENDING)
          .select_related('profile__employee', 'leave_type')
          .order_by('-created_at'))
    # Entity scope (CFO 2026-06-16) — approver only sees their entities' queue.
    from core.mixins import apply_company_scope
    qs = apply_company_scope(request, qs, 'profile__employee__company_id')
    if request.user.is_authenticated:
        qs = qs.exclude(profile__employee__user_id=request.user.id)
    # CFO 2026-07-24: show only leave ROUTED to this caller (they were picked as
    # the approver, or they are the employee's manager) — the leave picker
    # (2026-07-15) assigns a specific approver, so a manager/CFO should not see
    # the whole company's leave. Genuine HR keeps the full queue for oversight.
    from django.db.models import Q
    from core.hris_access import hris_role
    from hris.amendment_service import UNAMI_EMAIL, _local
    # HR head (Unami) + genuine HR keep the full oversight queue; everyone else
    # — incl. the CFO / superusers — sees only leave routed to them.
    #
    # The oversight branch ALSO requires real HRIS clearance (Fable review
    # 2026-08-07). hris_role() maps the finance titles — CFO, Finance Manager,
    # Financial Controller, HR Manager — to role 'hr' BEFORE it checks for
    # direct reports. So once approve_team_leave gained the lighter TEAM_CAPS
    # tier, a finance-titled account that is not on the whitelist would have
    # jumped straight to the whole company's pending leave: names, reasons,
    # certificate flags. Without clearance they fall through to the routed
    # filter below and still run their own team, which is the point of the tier.
    hr_oversight = ((hris_role(request.user) in ('hr', 'hris')
                     or _local(getattr(request.user, 'email', '')) == _local(UNAMI_EMAIL))
                    and user_can_access_hris(request.user))
    if not hr_oversight:
        # H50 fix (2026-08-22): the LIST must show exactly what the caller can
        # DECIDE. decide_leave lets a manager act when they are the picked
        # approver OR the employee's manager — resolved through
        # _resolve_manager_employee (user- OR email-linked). The old list filter
        # instead required requested_approver IS NULL for the manager branch and
        # matched the manager only by user link, so a manager could approve (via
        # a direct link/email) a request that never appeared in their own queue.
        # Mirror decide_leave's predicate here so the two can't drift.
        cond = Q(requested_approver=request.user)
        me = _resolve_manager_employee(request.user)
        if me is not None:
            cond |= Q(profile__manager_id=me.id)
        qs = qs.filter(cond)
    from hris.leave_admin import tasks_in_window_count
    page = list(qs[:100])
    # CFO 2026-09-07: warn the approver when the balance on screen is a GUESS.
    # With no hire date the accrual engine falls back to 1 January and credits a
    # full year to date — 14.00 days on 7 September, whoever you are and however
    # recently you joined. 58 active employees read exactly that. Encashment is
    # already refused for them (`leave_encash_service.apply_encashment`), but
    # ordinary leave can still be booked against the figure, so the approver is
    # the only check left until HR loads the date. A profile with an HR-uploaded
    # annual opening balance is NOT flagged: that figure is HR's own and the
    # engine accrues forward from its as-at date, which post-dates the hire.
    # One extra query for the page, not one per row.
    _no_hire = {lr.profile_id for lr in page
                if lr.profile.employee.hire_date is None}
    _has_opening = set()
    if _no_hire:
        from hris.models import LeaveOpeningBalance
        _has_opening = set(LeaveOpeningBalance.objects
                           .filter(profile_id__in=_no_hire,
                                   leave_type_code__iexact='annual')
                           .values_list('profile_id', flat=True))
    _assumed = _no_hire - _has_opening
    from hris import discretionary_leave as dl
    rows = []
    for lr in page:
        # Discretionary leave (CFO 2026-09-10): the manager decides on the
        # answers and the pattern, not on the dates alone — so both travel with
        # the queue row. history_for is one query per discretionary row; those
        # are a handful a month, not the whole queue.
        _code = lr.leave_type.code if lr.leave_type_id else ''
        _is_disc = dl.is_discretionary(_code)
        rows.append({
            'id':           str(lr.id),
            'employee':     lr.profile.employee.full_name,
            'department':   lr.profile.employee.department or '',
            'leave_type':   lr.leave_type.name if lr.leave_type_id else '',
            'leave_code':   lr.leave_type.code if lr.leave_type_id else '',
            'start_date':   str(lr.start_date),
            'end_date':     str(lr.end_date),
            'days':         float(lr.days or 0),
            'start_day_type': lr.start_day_type,
            'end_day_type':   lr.end_day_type,
            'day_breakdown':  lr.day_breakdown(),
            'reason':       lr.reason or '',
            'reason_category': lr.get_reason_category_display() if lr.reason_category else '',
            'has_certificate': bool(getattr(lr, 'medical_certificate', None) and lr.medical_certificate.name),
            # Ask #7 (Unami): a manager can decline based on pending work — so
            # show how many of the employee's open tasks fall inside the leave
            # window, right on the queue row (was email-only before).
            'tasks_in_window': tasks_in_window_count(lr),
            # True = this person has no start date on record, so their leave
            # balance is counted from 1 January and is not a real figure.
            'balance_is_assumed': lr.profile_id in _assumed,
            'created_at':   lr.created_at.isoformat(),
            # Discretionary leave extras — empty/False for ordinary leave.
            'is_discretionary': _is_disc,
            'policy_answers': dl.display_answers(_code, lr.policy_answers) if _is_disc else [],
            'policy_ack':     bool(lr.policy_ack),
            'policy_history': dl.history_for(lr.profile, _code) if _is_disc else None,
        })
    return Response({'count': len(rows), 'pending': rows,
                     'assumed_balances': sum(1 for r in rows
                                             if r['balance_is_assumed'])})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def decide_leave(request, leave_id):
    """POST /hris/api/leave-requests/<uuid:leave_id>/decide/
    Body: {"decision": "approve"|"reject", "notes": "..."}
    The caller must (a) have HRIS access, (b) not be the requester
    themselves (segregation), (c) be a manager / HR / admin.
    """
    # Must hold the approve_team_leave capability (mgr/hr/hris/ceo) — plain
    # HRIS access (ess) is not enough to decide someone else's leave.
    gate = _gate(request, capability='approve_team_leave')
    if gate is not None:
        return gate
    decision = (request.data.get('decision') or '').strip().lower()
    notes    = (request.data.get('notes') or '').strip()
    if decision not in ('approve', 'reject'):
        return Response({'detail': 'decision must be approve|reject.'}, status=400)

    # Entity scope — an approver can only decide leave for employees in their
    # own companies (mirrors leave_queue / manager_inbox). Prevents approving
    # another entity's request by UUID.
    #
    # Lock the row + re-read the committed status (select_for_update) so an
    # approve/refuse racing an employee self-cancel can't both win — the first
    # to commit wins, the second sees the changed status and is rejected
    # (Kago Tshutlhedi race requirement, 2026-07-16).
    from core.mixins import apply_company_scope
    with transaction.atomic():
        lr = (apply_company_scope(request,
                                  LeaveRequest.objects.select_for_update().filter(pk=leave_id),
                                  'profile__employee__company_id')
              .first())
        if lr is None:
            return Response({'detail': 'Leave request not found.'}, status=404)
        if lr.status not in (LeaveRequest.Status.PENDING, LeaveRequest.Status.DRAFT):
            return Response({'detail': 'This request has already been actioned.'}, status=409)
        if lr.profile.employee.user_id and lr.profile.employee.user_id == request.user.id:
            return Response({'detail': 'You cannot approve your own leave.'}, status=403)

        # Team tier (CFO 2026-08-07): a line manager who is not on the HRIS
        # whitelist may decide only leave that is actually routed to them —
        # they were picked as the approver, or they are the employee's manager.
        # HR and above keep the full oversight queue, unchanged.
        if not user_can_access_hris(request.user):
            me = _resolve_manager_employee(request.user)
            routed_to_me = (
                lr.requested_approver_id == request.user.id
                or (me is not None and lr.profile.manager_id == me.id))
            if not routed_to_me:
                return Response(
                    {'detail': 'This leave request was not routed to you. '
                               'Only the employee’s own manager or HR can decide it.'},
                    status=status.HTTP_403_FORBIDDEN)

        # Long-overdue-task gate (CFO 2026-08-07): an application flagged for a
        # CEO/CFO countersignature cannot be APPROVED until that signature is
        # in. Declining is always allowed — a manager may still say no.
        if decision == 'approve':
            from hris import exec_signoff_service
            from hris.exec_signoff_models import ExecSignoff
            pending = exec_signoff_service.blocking_signoff(
                ExecSignoff.Module.LEAVE, lr.pk)
            if pending is not None:
                return Response(
                    {'detail': exec_signoff_service.block_message(pending),
                     'needs_exec_signoff': True},
                    status=status.HTTP_409_CONFLICT)
            # Discretionary leave (CFO 2026-09-10): ask the opposite question
            # too — is there a SIGNED countersignature? The check above only
            # sees a row that was written; this one refuses when none exists.
            from hris import discretionary_leave as _dl
            unsigned = _dl.approval_blocked_reason(lr)
            if unsigned:
                return Response({'detail': unsigned, 'needs_exec_signoff': True},
                                status=status.HTTP_409_CONFLICT)

        lr.status = (LeaveRequest.Status.APPROVED if decision == 'approve'
                     else LeaveRequest.Status.REFUSED)
        lr.approver = request.user
        lr.decided_at = timezone.now()
        if notes:
            lr.decision_notes = notes
        _fields = ['status', 'approver', 'decided_at', 'decision_notes', 'updated_at']
        # Dual approval (Unami 2026-07-27): a manager-approved leave that carries
        # a certificate to authenticate — sick leave (LeaveType.requires_medical
        # _cert) — enters the HR verification queue. HR then authenticates the
        # dates + certificate (fraud control). The leave is already approved;
        # HR verification is an overlay, so this never blocks the employee.
        if (lr.status == LeaveRequest.Status.APPROVED
                and lr.leave_type_id
                and getattr(lr.leave_type, 'requires_medical_cert', False)):
            lr.hr_review_state = LeaveRequest.HRReviewState.PENDING
            _fields.append('hr_review_state')
        lr.save(update_fields=_fields)
        # Clear the attendance days this leave covers (CFO 2026-08-03) — see
        # hris.leave_backfill. Inside the same transaction as the decision so a
        # half-applied state can't survive; the helper never raises.
        from hris.leave_backfill import backfill_workdays_for_leave
        backfill_workdays_for_leave(lr)

    # Tell the EMPLOYEE the answer (CFO 2026-08-07). Outside the transaction so
    # a slow mailer never holds the row lock, and best-effort so it can never
    # roll back a decision that has already been made.
    try:
        from core.notifications import notify_leave_decided
        notify_leave_decided(lr)
    except Exception:   # noqa: BLE001
        pass

    return Response({
        'id':        str(lr.id),
        'status':    lr.status,
        'approver':  request.user.username,
        'decided_at': lr.decided_at.isoformat(),
        'notes':     lr.decision_notes,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_leave(request, leave_id):
    """POST /hris/api/leave-requests/<uuid:leave_id>/cancel/

    The requester withdraws their OWN leave. Two cases:
      * still PENDING / DRAFT — withdraw it, as before;
      * already APPROVED but NOT YET STARTED — cancel it too (Oprah Mogomotsi
        request; CFO 2026-08-07: "cancel approved leave i am ok with it").

    Leave that has already started or finished is NOT self-cancellable: those
    days are history, and unwinding them is an HR correction, not a click.

    The balance frees itself — it counts only APPROVED/PENDING, so a CANCELLED
    request drops out. Attendance does NOT free itself: approving stamped those
    days "on leave", so cancelling has to put them back or the person shows as
    covered on days they are now expected to work.

    Kago Tshutlhedi feature request 2026-07-16; extended to approved leave 2026-08-08.
    """
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'detail': 'No employee profile is linked to your account.'}, status=404)

    with transaction.atomic():
        # Lock + re-read: if an approver actions it at the same moment, the
        # first commit wins and the loser gets a clear message.
        lr = (LeaveRequest.objects.select_for_update()
              .filter(pk=leave_id, profile=profile).first())
        if lr is None:
            return Response({'detail': 'Leave request not found.'}, status=404)
        was_approved = lr.status == LeaveRequest.Status.APPROVED
        cancellable = (LeaveRequest.Status.PENDING, LeaveRequest.Status.DRAFT,
                       LeaveRequest.Status.APPROVED)
        if lr.status not in cancellable:
            return Response({'detail': 'This request has already been actioned.'}, status=409)
        # Approved leave can only be cancelled BEFORE it starts. Once the first
        # day has arrived those days are history and putting them back is an HR
        # correction, not a self-service click.
        if was_approved and lr.start_date and lr.start_date <= timezone.localdate():
            return Response(
                {'detail': 'This leave has already started, so it cannot be cancelled '
                           'here. Ask HR to correct it.'},
                status=status.HTTP_409_CONFLICT)

        lr.status = LeaveRequest.Status.CANCELLED
        lr.decided_at = timezone.now()
        lr.decision_notes = ('Approved leave cancelled by the employee before it started.'
                             if was_approved else 'Cancelled by the employee.')
        lr.save(audit_user=request.user,
                audit_description=f'Employee cancelled leave request {lr.pk}',
                update_fields=['status', 'decided_at', 'decision_notes', 'updated_at'])

        # Approving stamped these days "on leave"; cancelling must un-stamp them,
        # or the workforce brief stops chasing hours the person now owes.
        if was_approved:
            from hris.leave_backfill import unbackfill_workdays_for_leave
            unbackfill_workdays_for_leave(lr)
    # Confirm the withdrawal in writing (CFO 2026-08-07) — every leave outcome
    # now reaches the employee, not just approvals.
    try:
        from core.notifications import notify_leave_decided
        notify_leave_decided(lr)
        # The manager who approved it planned around those days. Tell them too.
        if was_approved:
            from core.notifications import notify_approved_leave_cancelled
            notify_approved_leave_cancelled(lr)
    except Exception:   # noqa: BLE001
        pass
    return Response({'id': str(lr.id), 'status': lr.status,
                     'was_approved': was_approved})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_leave_requests(request):
    """GET /hris/api/leave-requests/mine/ — the caller's OWN leave requests with
    their current status + any approver decision note, newest first. Lets an
    employee see where each application stands (bug 3af04928)."""
    denied = _gate(request, capability='view_self')
    if denied is not None:
        return denied
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'count': 0, 'requests': []})
    qs = (LeaveRequest.objects
          .filter(profile=profile)
          .select_related('leave_type', 'approver')
          .order_by('-created_at'))
    rows = [{
        'id':            str(lr.id),
        'leave_type':    lr.leave_type.name if lr.leave_type_id else '',
        'leave_code':    lr.leave_type.code if lr.leave_type_id else '',
        'start_date':    str(lr.start_date),
        'end_date':      str(lr.end_date),
        'days':          float(lr.days or 0),
        'start_day_type': lr.start_day_type,
        'end_day_type':   lr.end_day_type,
        'day_breakdown':  lr.day_breakdown(),
        'status':        lr.status,
        'status_label':  lr.get_status_display(),
        'reason':        lr.reason or '',
        'decision_notes': lr.decision_notes or '',
        'approver':      (lr.approver.get_full_name() or lr.approver.username) if lr.approver_id else '',
        'decided_at':    lr.decided_at.isoformat() if lr.decided_at else None,
        'created_at':    lr.created_at.isoformat(),
        # The requester may withdraw their own request only while it is still
        # pending (self-cancel of approved leave is an approver/HR action).
        # Mirrors cancel_leave exactly: pending/draft always, approved only
        # while it has not started yet (CFO 2026-08-08).
        'can_cancel':    (lr.status in (LeaveRequest.Status.PENDING, LeaveRequest.Status.DRAFT)
                          or (lr.status == LeaveRequest.Status.APPROVED
                              and bool(lr.start_date)
                              and lr.start_date > timezone.localdate())),
    } for lr in qs[:100]]
    return Response({'count': len(rows), 'requests': rows})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_leave_decisions(request):
    """GET /hris/api/leave-requests/decisions/ — leave requests THIS approver has
    already decided (approved/refused), newest first: the approver's own history
    of past decisions with the reason they recorded (bug 3af04928)."""
    gate = _gate(request, capability='approve_team_leave')
    if gate is not None:
        return gate
    qs = (LeaveRequest.objects
          .filter(approver=request.user,
                  status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.REFUSED])
          .select_related('profile__employee', 'leave_type')
          .order_by('-decided_at'))
    from core.mixins import apply_company_scope
    qs = apply_company_scope(request, qs, 'profile__employee__company_id')
    rows = [{
        'id':            str(lr.id),
        'employee':      lr.profile.employee.full_name,
        'department':    lr.profile.employee.department or '',
        'leave_type':    lr.leave_type.name if lr.leave_type_id else '',
        'leave_code':    lr.leave_type.code if lr.leave_type_id else '',
        'start_date':    str(lr.start_date),
        'end_date':      str(lr.end_date),
        'days':          float(lr.days or 0),
        'start_day_type': lr.start_day_type,
        'end_day_type':   lr.end_day_type,
        'day_breakdown':  lr.day_breakdown(),
        'status':        lr.status,
        'status_label':  lr.get_status_display(),
        'decision_notes': lr.decision_notes or '',
        'decided_at':    lr.decided_at.isoformat() if lr.decided_at else None,
    } for lr in qs[:100]]
    return Response({'count': len(rows), 'decisions': rows})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_upload_status(request):
    """GET /hris/api/leave-uploads/status/ — persistent status for the HR bulk
    upload tiles so the 'done' banner survives a page refresh (bug ecfc3f5a):
    opening balances = last upload date + who + coverage; approvers = how many
    active staff have an approver assigned."""
    gate = _gate(request, capability='manage_leave_admin')
    if gate is not None:
        return gate
    from core.mixins import apply_company_scope
    from hris.models import LeaveOpeningBalance
    ob_qs = apply_company_scope(request, LeaveOpeningBalance.objects.all(),
                                'profile__employee__company_id')
    latest = ob_qs.select_related('uploaded_by').order_by('-created_at').first()
    opening = {
        'last_uploaded_at':  latest.created_at.isoformat() if latest else None,
        'by':                ((latest.uploaded_by.get_full_name() or latest.uploaded_by.username)
                              if (latest and latest.uploaded_by_id) else ''),
        'employees_covered': ob_qs.values('profile_id').distinct().count(),
        'rows':              ob_qs.count(),
    }
    prof_qs = apply_company_scope(request, HRISProfile.objects.all(), 'employee__company_id')
    approvers = {
        'assigned': prof_qs.filter(manager__isnull=False).count(),
        'total':    prof_qs.count(),
    }
    return Response({'opening_balances': opening, 'approvers': approvers})


# 2 — Employee self-service profile PATCH -----------------------------------

def _active_contract(emp) -> dict:
    """The employee's current active contract window, for the profile.

    Returns {start, end, permanent, label}. CFO/Unami directive 2026-07-23:
    the profile should show contract duration so HR (and the employee) can see
    it at a glance and it can back the staff-loan check without a separate
    upload. Empty strings when no active contract is on file.
    """
    out = {'start': '', 'end': '', 'permanent': False, 'label': ''}
    if emp is None:
        return out
    try:
        from payroll.contract_models import EmploymentContract
        from django.db.models import Q
        from django.utils import timezone as _tz
        today = _tz.localdate()
        c = (EmploymentContract.objects
             .filter(employee=emp, status=EmploymentContract.Status.ACTIVE,
                     start_date__lte=today)
             .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
             .order_by('-start_date').first())
        if c is None:
            return out
        out['start'] = c.start_date.isoformat() if c.start_date else ''
        if c.end_date:
            out['end'] = c.end_date.isoformat()
            out['label'] = (f"{c.start_date:%d %b %Y} → {c.end_date:%d %b %Y}"
                            if c.start_date else f"until {c.end_date:%d %b %Y}")
        else:
            out['permanent'] = True
            out['label'] = (f"Permanent (since {c.start_date:%d %b %Y})"
                            if c.start_date else "Permanent")
    except Exception:  # noqa: BLE001 — never break the profile on a contract read
        pass
    return out


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def me_profile(request):
    """GET/PATCH /hris/api/me/

    GET   — return the caller's own HRIS profile (subset safe to share).
    PATCH — update bank_account_no, bank_branch, bank_name, phone,
            emergency_contact_name, emergency_contact_phone.
            Any change to bank_* fields flags a maker-checker pending
            entry (advisory only for now — full workflow ships in v2).
    """
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'detail': 'No HRIS profile.'}, status=404)
    emp = profile.employee

    if request.method == 'GET':
        contract = _active_contract(emp)
        return Response({
            'employee_number':         emp.employee_number,
            'full_name':               emp.full_name,
            'email':                   emp.email,
            'phone':                   emp.phone,
            'department':              emp.department,
            'job_title':               emp.job_title,
            'bank_name':               emp.bank_name,
            'bank_account_no':         emp.bank_account_no,
            'bank_branch':             emp.bank_branch,
            'national_id':             '***' if emp.national_id else '',
            'qualifications':          getattr(emp, 'qualifications', '') or '',
            'hire_date':               emp.hire_date.isoformat() if emp.hire_date else '',
            'contract_start':          contract['start'],
            'contract_end':            contract['end'],
            'contract_permanent':      contract['permanent'],
            'contract_duration':       contract['label'],
        })

    SELF_EDITABLE = {
        'phone', 'bank_name', 'bank_account_no', 'bank_branch',
        'qualifications',
    }
    BANK_FIELDS = {'bank_name', 'bank_account_no', 'bank_branch'}
    payload = request.data or {}
    changed = []
    pending_bank_change = False
    for field, value in payload.items():
        if field not in SELF_EDITABLE:
            continue
        # Coerce to string — Employee bank fields are all CharField.
        new_val = '' if value is None else str(value)
        if getattr(emp, field) != new_val:
            setattr(emp, field, new_val)
            changed.append(field)
            if field in BANK_FIELDS:
                pending_bank_change = True
    if changed:
        emp.save(update_fields=changed)
    return Response({
        'updated_fields':       changed,
        'pending_bank_review':  pending_bank_change,
        'note': ('Bank-detail changes flagged for HR/Finance maker-checker review.'
                 if pending_bank_change else ''),
    })


# 2c — My disciplinary record (item 4c) -------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_disciplinary(request):
    """GET /hris/api/my-disciplinary/ — the caller's OWN disciplinary actions.

    Item 4c (Unami / CFO directive 2026-07-23): an employee can see the
    disciplinary actions on THEIR OWN file. Strictly:
      • only cases that have been ISSUED (a final outcome already given to them);
        pending / rejected internal cases stay hidden,
      • only the caller's own record (subject_employee == caller), never anyone
        else's.
    Natural justice (CFO directive 2026-08-11) widened this by exactly one
    status: a case AWAITING THE CALLER'S OWN RESPONSE is also shown, because an
    employee cannot answer an allegation they are not allowed to read. Still
    strictly their own record, and only the fields they need in order to answer:
      • the allegation, the category being considered, the incident date, and
        their deadline,
      • never who raised it, never HR's notes or decision, never any evidence
        file, and never any other employee's case.
    HR / CFO keep the full Disciplinary board unchanged.
    """
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'count': 0, 'cases': [], 'detail': 'No HRIS profile.'})
    emp = profile.employee
    # A profile with no employee row must never fall through to an unfiltered
    # queryset — an identity gate needs a positive match, never a default.
    if emp is None or getattr(emp, 'id', None) is None:
        return Response({'count': 0, 'cases': [], 'detail': 'No employee record.'})
    try:
        from .disciplinary_models import DisciplinaryCase
    except ImportError:
        return Response({'count': 0, 'cases': []})
    VISIBLE = (DisciplinaryCase.Status.ISSUED, DisciplinaryCase.Status.PENDING_RESPONSE)
    qs = (DisciplinaryCase.objects
          .filter(subject_employee_id=emp.id, status__in=VISIBLE)
          .order_by('-issued_at', '-created_at')[:50])
    out = [{
        'id':              str(c.id),
        'status':          c.status,
        'category':        c.category,
        'category_label':  c.get_category_display(),
        'incident_date':   c.incident_date.isoformat() if c.incident_date else None,
        'issued_at':       c.issued_at.isoformat() if c.issued_at else None,
        'allegation':      c.allegation,
        'proposed_action': c.proposed_action,
        # The response step. `can_respond` is what the UI shows the box on.
        'awaiting_my_response': c.status == DisciplinaryCase.Status.PENDING_RESPONSE,
        'response_deadline':     c.response_deadline.isoformat() if c.response_deadline else None,
        'deadline_passed':       c.deadline_passed,
        'my_response':           c.employee_response,
        'my_responded_at':       (c.employee_responded_at.isoformat()
                                  if c.employee_responded_at else None),
        # Also open on an ISSUED case that carries an inquiry — a warning decided
        # before this stage existed, where the employee is invited to answer
        # afterwards. Their words go on the file; the outcome does not change.
        'can_respond': (not c.employee_responded_at
                        and (c.status == DisciplinaryCase.Status.PENDING_RESPONSE
                             or (c.status == DisciplinaryCase.Status.ISSUED
                                 and c.inquiry_issued_at is not None))),
        'response_invited_after_decision': (c.status == DisciplinaryCase.Status.ISSUED
                                            and c.inquiry_issued_at is not None),
    } for c in qs]
    return Response({'count': len(out), 'cases': out})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def my_disciplinary_respond(request, case_id):
    """POST /hris/api/my-disciplinary/<uuid:case_id>/respond/ — the employee's own
    explanation, recorded on the case record (CFO directive 2026-08-11).

    The authority check is disciplinary_service.is_subject(), which matches on
    the employee primary key only. This endpoint can therefore never be used to
    touch another person's case: a case id belonging to someone else fails the
    subject check and returns 403 — the same answer as a case that does not
    exist, so it cannot be used to probe for other people's cases either.
    """
    from .disciplinary_models import DisciplinaryCase
    from . import disciplinary_notify as disc_notify
    from . import disciplinary_service as disc_svc

    case = DisciplinaryCase.objects.filter(id=case_id).first()
    if case is None or not disc_svc.is_subject(case, request.user):
        return Response({'detail': 'No such case on your record.'}, status=403)
    try:
        case = disc_svc.record_response(
            case.id, request.user, response=request.data.get('response') or '')
    except DjangoValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    try:
        disc_notify.notify_response_recorded(case)
    except Exception:    # noqa: BLE001
        pass             # the response is saved; a mail failure must not lose it
    return Response({
        'id':              str(case.id),
        'status':          case.status,
        'my_response':     case.employee_response,
        'my_responded_at': case.employee_responded_at.isoformat(),
        'detail':          'Your response has been recorded and sent to HR.',
    })


# 2b — My talent snapshot -----------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_talent(request):
    """GET /hris/api/my-talent/ — the caller's OWN talent snapshot for the
    profile page: 9-box placement, latest Development Dialogue rating, a skills
    summary, and the last review with an overdue flag.

    Read-only and own-record-only (resolved via _profile_for + the dialogue's
    own owner-email key, the same self-service key the cockpit uses). It only
    SURFACES data that already lives in the talent modules so an employee sees
    where they stand without opening five screens (Oprah Mogomotsi feature
    request, 2026-08-13). Every block is null when there is no record — the card
    never invents a figure. IDP has no server model yet, so it links to the plan
    rather than showing a made-up goal count.
    """
    from hris.talent_cockpit_models import DevelopmentDialogue
    from hris.talent_views import _box_from_dialogue, _latest_review
    from hris.skills_views import PROFICIENT_LEVEL

    profile = _profile_for(request.user)
    emp = profile.employee if profile is not None else None
    email = (getattr(request.user, 'email', '') or '').strip()

    # 9-box + latest dialogue — matched on the dialogue's OWN owner email (its
    # self-service key), falling back to the payroll link.
    dlg = None
    if email:
        dlg = (DevelopmentDialogue.objects
               .filter(email__iexact=email, is_current=True)
               .order_by('-updated_at').first())
    if dlg is None and emp is not None:
        dlg = (DevelopmentDialogue.objects
               .filter(employee=emp, is_current=True)
               .order_by('-updated_at').first())

    ninebox = dialogue = None
    if dlg is not None:
        box, perf, pot = _box_from_dialogue(dlg)
        ninebox = {'label': box['l'], 'box': box['n'], 'colour': box['c'],
                   'text_colour': box['t'], 'advice': box['a'],
                   'performance': perf, 'potential': pot}
        dialogue = {'rating': dlg.rating or '',
                    'overall': float(dlg.overall) if dlg.overall is not None else None,
                    'period': dlg.period or '',
                    'date': dlg.updated_at.date().isoformat() if dlg.updated_at else ''}

    # Skills — the caller's own EmployeeSkill rows (level 1-5; 3+ = proficient).
    items = []
    if profile is not None:
        for r in profile.skills.select_related('skill').order_by('-level', 'skill__name'):
            items.append({'name': r.skill.name,
                          'category': getattr(r.skill, 'category', '') or '',
                          'level': r.level,
                          'proficient': r.level >= PROFICIENT_LEVEL})
    skills = {'total': len(items),
              'proficient': sum(1 for i in items if i['proficient']),
              'items': items[:12]}

    # Next review — the caller's latest submitted/finalised review, flagged
    # overdue when it is more than a year old (a fresh cycle is due).
    review = None
    if profile is not None:
        pr = _latest_review(profile)
        if pr is not None and pr.review_date:
            days = (timezone.localdate() - pr.review_date).days
            review = {'date': pr.review_date.isoformat(), 'period': pr.period or '',
                      'status': pr.status,
                      'rating': float(pr.overall_rating) if pr.overall_rating is not None else None,
                      'overdue': days > 365}

    return Response({
        'has_profile': profile is not None,
        'ninebox':  ninebox,     # null if no dialogue on record
        'dialogue': dialogue,    # null if no dialogue on record
        'skills':   skills,
        'review':   review,      # null if no review on record
        'idp':      {'available': False, 'link': '/hris/idp'},
    })


# 3 — My payslips ------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_payslips(request):
    """GET /hris/api/my-payslips/  — the caller's own payslip history."""
    profile = _profile_for(request.user)
    if profile is None:
        return Response({'payslips': [], 'detail': 'No HRIS profile.'}, status=200)
    emp = profile.employee
    try:
        from payroll.models import Payslip
    except ImportError:
        return Response({'payslips': [], 'detail': 'Payroll module not installed.'})
    qs = (Payslip.objects.filter(employee=emp)
          .select_related('period', 'company')
          .order_by('-period__end_date')[:24])
    # From 2026-07 a payslip only reaches the employee once the CFO has signed
    # that company's month off (CFO directive 2026-07-26). No sign-off yet =>
    # no download link and a plain reason. Earlier months are unaffected.
    from payroll.signoff_service import release_blocked_reason
    out = []
    for ps in qs:
        hold = release_blocked_reason(ps)
        out.append({
            'id':           str(ps.id),
            'period':       ps.period.period_name,
            'period_end':   str(ps.period.end_date),
            'status':       ps.status,
            'gross_amount': str(ps.gross_amount),
            'paye_amount':  str(ps.paye_amount),
            'net_amount':   str(ps.net_amount),
            'hold_reason':  hold or '',
            'pdf_url':      '' if hold else f'/api/v1/payslips/{ps.id}/pdf/',
        })
    return Response({'count': len(out), 'payslips': out})


# 4 — Peer recognition (Kudos) -----------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def recognition_feed(request):
    """GET/POST /hris/api/kudos/

    GET  — last 50 public kudos (or private kudos addressed to the caller).
    POST — body {receiver: <profile uuid OR employee_number>,
                 value_demonstrated: integrity|excellence|ownership|
                                     teamwork|innovation|customer,
                 message: "...", points: 1..5, is_public: bool}
    """
    # Self-service tier, not the HRIS whitelist (Motlatsi, 2026-09-10). This
    # feed is on /my-omni — EVERY staff member's home page — and on
    # /hris/rewards, both with a send form. Gated with no capability it fell to
    # the privileged tier, so everyone outside the five-person HRIS whitelist
    # got 403 on their own home screen and the tile read as broken.
    #
    # Recognition is peer-to-peer by design (hris/models.py): the read below is
    # already filtered to public kudos plus ones addressed to the caller AND
    # entity-scoped, and the write below forces sender to the caller's own
    # profile. So the self-scoping the self-service tier relies on is already
    # enforced here — this grants no sight of anything the feed would not
    # already have shown a whitelisted colleague.
    gate = _gate(request, capability='view_self')
    if gate is not None:
        return gate

    if request.method == 'GET':
        my_profile = _profile_for(request.user)
        qs = Recognition.objects.select_related(
            'sender__employee', 'receiver__employee'
        ).order_by('-created_at')
        if my_profile is None:
            qs = qs.filter(is_public=True)
        else:
            from django.db.models import Q
            qs = qs.filter(Q(is_public=True) | Q(receiver=my_profile))
        # Entity scope (CFO 2026-06-16) — don't surface other entities' names.
        from core.mixins import apply_company_scope
        qs = apply_company_scope(request, qs, 'receiver__employee__company_id')
        rows = []
        for k in qs[:50]:
            rows.append({
                'id':                 str(k.id),
                'sender':             k.sender.employee.full_name,
                'receiver':           k.receiver.employee.full_name,
                'value_demonstrated': k.value_demonstrated,
                'message':            k.message,
                'points':             k.points,
                'is_public':          k.is_public,
                'created_at':         k.created_at.isoformat(),
            })
        return Response({'count': len(rows), 'kudos': rows})

    # POST
    sender = _profile_for(request.user)
    body = request.data or {}
    receiver_ref = (body.get('receiver') or '').strip()
    value        = (body.get('value_demonstrated') or '').strip().lower()
    message      = (body.get('message') or '').strip()
    try:
        points = int(body.get('points') or 1)
    except (TypeError, ValueError):
        points = 1
    is_public = bool(body.get('is_public', True))

    if not (receiver_ref and value and message):
        return Response({'detail': 'receiver, value_demonstrated, message required.'}, status=400)
    if value not in [v[0] for v in Recognition.Value.choices]:
        return Response({'detail': f'Unknown value {value!r}.'}, status=400)
    if points < 1 or points > 5:
        return Response({'detail': 'points must be 1..5.'}, status=400)

    # Resolve receiver — UUID, employee_number, or full_name
    receiver = HRISProfile.objects.filter(pk=receiver_ref).first()
    if receiver is None:
        receiver = HRISProfile.objects.filter(employee__employee_number=receiver_ref).first()
    if receiver is None:
        receiver = HRISProfile.objects.filter(employee__full_name__iexact=receiver_ref).first()
    if receiver is None:
        return Response({'detail': f'Receiver not found: {receiver_ref!r}.'}, status=404)
    # Self-nomination — caught BEFORE the sender-profile gate so the message is
    # meaningful (BUG c2219e99: a self-kudos was returning the unrelated
    # "Senders must have an HRIS profile"). Self = same profile, or the receiver's
    # linked login is the current user.
    recv_user_id = getattr(getattr(receiver.employee, 'user', None), 'id', None)
    if (sender is not None and receiver.pk == sender.pk) or \
       (recv_user_id and recv_user_id == getattr(request.user, 'id', None)):
        return Response({'detail': 'You cannot send a kudos to yourself.'}, status=400)
    if sender is None:
        return Response({'detail': 'You need an HR profile linked to your login to send kudos. '
                                   'Ask HR to link your account.'}, status=403)

    k = Recognition.objects.create(
        sender=sender, receiver=receiver,
        value_demonstrated=value, message=message[:500],
        points=points, is_public=is_public,
    )
    return Response({
        'id':         str(k.id),
        'sender':     sender.employee.full_name,
        'receiver':   receiver.employee.full_name,
        'value':      value,
        'points':     points,
        'is_public':  is_public,
        'created_at': k.created_at.isoformat(),
    }, status=status.HTTP_201_CREATED)


# 7 — Onboarding journey + 30-60-90 checklist --------------------------------

@api_view(['GET', 'POST', 'PATCH'])
@permission_classes([IsAuthenticated])
def onboarding_journey(request):
    """GET/POST/PATCH /hris/api/onboarding/

    GET  ?employee_id=<int>  — list tasks for one new hire (no id = mine).
    POST {employee_id, title, category, due_date, owner_user_id} — create.
    PATCH {id, status, notes} — update an existing task.

    The 30-60-90 manager checklist auto-creates as three tasks
    (category check30/check60/check90) when a new Employee is onboarded
    via the bulk endpoint. Here we just serve / mutate the rows.
    """
    gate = _gate(request)
    if gate is not None:
        return gate

    try:
        from hris.models import OnboardingTask
    except Exception:
        return Response({'detail': 'OnboardingTask model not installed.'}, status=503)
    from payroll.models import Employee

    if request.method == 'GET':
        emp_id = request.query_params.get('employee_id') or ''
        qs = OnboardingTask.objects.select_related('employee').order_by('due_date', 'created_at')
        if emp_id:
            qs = qs.filter(employee_id=emp_id)
        else:
            qs = qs.filter(owner_user_id=request.user.id)
        # Entity scope (CFO 2026-06-16) — clamp to caller's granted companies.
        from core.mixins import apply_company_scope
        qs = apply_company_scope(request, qs, 'employee__company_id')
        rows = [{
            'id':         str(t.id),
            'employee_id': t.employee_id,
            'employee':   t.employee.full_name if t.employee_id else '',
            'title':      t.title,
            'category':   t.category,
            'due_date':   t.due_date.isoformat() if t.due_date else '',
            'status':     t.status,
            'notes':      t.notes,
        } for t in qs[:200]]
        return Response({'count': len(rows), 'tasks': rows})

    if request.method == 'POST':
        body = request.data or {}
        emp = Employee.objects.filter(pk=body.get('employee_id')).first()
        if emp is None:
            return Response({'detail': 'employee_id required + must exist.'}, status=400)
        title = (body.get('title') or '').strip()
        if not title:
            return Response({'detail': 'title required.'}, status=400)
        cat = (body.get('category') or 'other').strip().lower()
        valid = [c[0] for c in OnboardingTask.Category.choices]
        if cat not in valid:
            return Response({'detail': f'category must be one of {valid}.'}, status=400)
        due = body.get('due_date') or None
        if due:
            try:
                due = _dt.date.fromisoformat(str(due))
            except ValueError:
                return Response({'detail': 'due_date must be YYYY-MM-DD.'}, status=400)
        owner_id = body.get('owner_user_id') or None
        t = OnboardingTask.objects.create(
            employee=emp,
            owner_user_id=owner_id,
            title=title[:160],
            category=cat,
            due_date=due,
            notes=(body.get('notes') or '').strip(),
        )
        return Response({'id': str(t.id), 'title': t.title, 'category': t.category}, status=201)

    # PATCH
    body = request.data or {}
    tid = (body.get('id') or '').strip()
    if not tid:
        return Response({'detail': 'id required.'}, status=400)
    t = OnboardingTask.objects.filter(pk=tid).first()
    if t is None:
        return Response({'detail': 'Task not found.'}, status=404)
    fields = []
    new_status = (body.get('status') or '').strip().lower()
    if new_status in [s[0] for s in OnboardingTask.Status.choices]:
        t.status = new_status; fields.append('status')
    if 'notes' in body:
        t.notes = (body.get('notes') or '').strip()
        fields.append('notes')
    if fields:
        t.save(update_fields=fields + ['updated_at'])
    return Response({'id': str(t.id), 'status': t.status, 'notes': t.notes})


# 6 — Manager command-center inbox -------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def manager_inbox(request):
    """GET /hris/api/inbox/
    Aggregates everything a manager / approver should action:
      * pending_leave   — LeaveRequests awaiting decision
      * kudos_recent    — last 10 kudos received by the caller
      * onboarding_open — open OnboardingTasks owned by caller (if module
                          present)
    Returns simple counts + sample rows so a single network call powers
    the command-center dashboard tile.
    """
    gate = _gate(request)
    if gate is not None:
        return gate

    out = {
        'pending_leave':    [],
        'kudos_recent':     [],
        'onboarding_open':  [],
    }

    # Pending leave queue
    from core.mixins import apply_company_scope
    pending_qs = (LeaveRequest.objects
                  .filter(status=LeaveRequest.Status.PENDING)
                  .exclude(profile__employee__user_id=request.user.id)
                  .select_related('profile__employee', 'leave_type')
                  .order_by('-created_at'))
    # Entity scope (CFO 2026-06-16) — only this caller's entities' requests.
    pending_qs = apply_company_scope(request, pending_qs, 'profile__employee__company_id')[:10]
    for lr in pending_qs:
        out['pending_leave'].append({
            'id':         str(lr.id),
            'employee':   lr.profile.employee.full_name,
            'leave_code': lr.leave_type.code if lr.leave_type_id else '',
            'start_date': str(lr.start_date),
            'end_date':   str(lr.end_date),
            'days':       float(lr.days or 0),
        })

    # Kudos received by caller (last 10)
    my_profile = _profile_for(request.user)
    if my_profile is not None:
        kqs = (Recognition.objects.filter(receiver=my_profile)
               .select_related('sender__employee')
               .order_by('-created_at')[:10])
        for k in kqs:
            out['kudos_recent'].append({
                'id':       str(k.id),
                'sender':   k.sender.employee.full_name,
                'value':    k.value_demonstrated,
                'message':  k.message[:160],
                'points':   k.points,
                'when':     k.created_at.isoformat(),
            })

    # Onboarding tasks (gracefully handle module absence)
    try:
        from hris.models import OnboardingTask
        otasks = (OnboardingTask.objects
                  .filter(status='open',
                          owner_user_id=request.user.id)
                  .select_related('employee')
                  .order_by('due_date')[:15])
        for t in otasks:
            out['onboarding_open'].append({
                'id':       str(t.id),
                'employee': t.employee.full_name if t.employee_id else '',
                'task':     t.title,
                'category': t.category,
                'due_date': t.due_date.isoformat() if t.due_date else '',
            })
    except Exception:
        # Model not migrated yet — leave list empty.
        pass

    out['counts'] = {
        'pending_leave':    len(out['pending_leave']),
        'kudos_recent':     len(out['kudos_recent']),
        'onboarding_open':  len(out['onboarding_open']),
    }
    return Response(out)


# 5 — 9-Box talent calibration ------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def calibrate_talent(request):
    """POST /hris/api/calibrate-talent/
    Body: {"profile_id": <uuid>, "talent_segment": "<segment>", "rationale": "..."}
    Updates HRISProfile.talent_segment.

    Restricted to HRIS-unlocked users with admin / HR / Finance access
    (same gate as the existing assessment endpoint).
    """
    gate = _gate(request)
    if gate is not None:
        return gate
    body = request.data or {}
    pid       = (body.get('profile_id') or '').strip()
    new_seg   = (body.get('talent_segment') or '').strip().lower()
    rationale = (body.get('rationale') or '').strip()
    if not pid or not new_seg:
        return Response({'detail': 'profile_id + talent_segment required.'}, status=400)
    valid = [s[0] for s in HRISProfile.TalentSegment.choices]
    if new_seg not in valid:
        return Response({'detail': f'talent_segment must be one of {valid}.'}, status=400)

    pqs = HRISProfile.objects.filter(pk=pid)
    # Entity scope (CFO 2026-06-16): can't calibrate a profile outside grant.
    from core.mixins import scoped_company_ids
    _ids = scoped_company_ids(request)
    if _ids is not None:
        pqs = pqs.filter(employee__company_id__in=_ids) if _ids else pqs.none()
    profile = pqs.first()
    if profile is None:
        return Response({'detail': 'Profile not found.'}, status=404)
    old = profile.talent_segment
    profile.talent_segment = new_seg
    profile.save(
        update_fields=['talent_segment', 'updated_at'],
        audit_user=request.user,
        audit_description=f'Calibrated {old!r} → {new_seg!r}. {rationale[:160]}',
    )
    return Response({
        'profile_id':           str(profile.id),
        'employee':             profile.employee.full_name,
        'talent_segment_old':   old,
        'talent_segment_new':   new_seg,
        'calibrated_by':        request.user.username,
        'rationale':            rationale,
    })


# ---------------------------------------------------------------------------
# HRIS-002 (HR Mogomotsi, 2026-06-10) - Leave Report
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_report(request):
    """GET /hris/api/leave-report/

    Per-employee x leave-type balance report for managers/HR:
      opening_balance + accrued - taken - encashed = closing_balance

    Query params:
      company=<uuid|code>      multi-entity scope (resolve_company_id_param)
      date_from / date_to      YYYY-MM-DD, default = current leave year
      leave_type=a,b           comma list of CoS codes; missing/'all' = all 7
      department=<name>        case-insensitive exact match, default all
      employee=<uuid>          narrow to one payroll.Employee

    Access: capability 'view_team' (mgr and above - never ess). Roles
    without 'view_all' see only their direct reports (+ themselves).

    Semantics mirror leave_balances (CoS Section 7): leave year = calendar
    year of date_from; annual accrues entitlement x months/12 with accrual
    clamped at the current month; flat types are available-on-need
    (accrued = null, rendered as em-dash). 'Taken' counts APPROVED requests
    only (HRIS-002 spec) - unlike leave_balances.used which adds PENDING.
    """
    from core.mixins import resolve_company_id_param

    gate = _gate(request, capability='view_team')
    if gate is not None:
        return gate

    today = timezone.localdate()

    def _parse_date(name, default):
        raw = (request.query_params.get(name) or '').strip()
        if not raw:
            return default, None
        try:
            return _dt.date.fromisoformat(raw), None
        except ValueError:
            return None, Response(
                {'detail': f'{name} must be YYYY-MM-DD.'}, status=400)

    date_from, err = _parse_date('date_from', _dt.date(today.year, 1, 1))
    if err is not None:
        return err
    date_to, err = _parse_date('date_to', _dt.date(today.year, 12, 31))
    if err is not None:
        return err
    if date_to < date_from:
        return Response({'detail': 'date_to is before date_from.'}, status=400)

    # Clamp the window to the leave year of date_from (no cross-year ledger).
    year_start = _dt.date(date_from.year, 1, 1)
    year_end = _dt.date(date_from.year, 12, 31)
    clamped = date_to > year_end
    if clamped:
        date_to = year_end

    leave_rules = get_leave_rules()
    raw_types = (request.query_params.get('leave_type') or '').strip().lower()
    if raw_types and raw_types != 'all':
        codes = [c.strip() for c in raw_types.split(',') if c.strip()]
        bad = [c for c in codes if c not in leave_rules]
        if bad:
            return Response(
                {'detail': f'Unknown leave_type: {", ".join(bad)}.'}, status=400)
    else:
        codes = list(leave_rules.keys())

    department = (request.query_params.get('department') or '').strip()
    employee_param = (request.query_params.get('employee') or '').strip()

    profiles = HRISProfile.objects.select_related('employee')

    # QA accounts are not staff. They exist so the test harness drives real code
    # paths, but they must never pad a list a human reads (Oprah 2026-08-10).
    profiles = profiles.exclude(employee__is_test_record=True)

    # Entity scope (CFO 2026-06-16): clamp to the caller's UserCompanyAccess
    # grant. A user restricted to ADRisk can't widen to another entity even
    # with view_all; unrestricted users keep the consolidated view.
    from core.mixins import apply_company_scope
    company_id = resolve_company_id_param(request)
    profiles = apply_company_scope(request, profiles, 'employee__company_id')

    role = hris_role(request.user)
    caps = ROLE_CAPABILITIES.get(role, set())
    if 'view_all' not in caps:
        # Manager tier: direct reports + self only. The User↔Employee link
        # is payroll.Employee.user (related_name='employee_record').
        emp_rec = getattr(request.user, 'employee_record', None)
        my_emp_id = getattr(emp_rec, 'id', None)
        if not my_emp_id:
            return Response({'detail': 'No employee record linked to your account.'},
                            status=403)
        from django.db.models import Q
        profiles = profiles.filter(Q(manager_id=my_emp_id) | Q(employee_id=my_emp_id))

    if department:
        profiles = profiles.filter(employee__department__iexact=fold_legacy(department) or department)
    if employee_param:
        import uuid as _uuid
        try:
            emp_uuid = _uuid.UUID(employee_param)
        except ValueError:
            return Response({'detail': 'employee must be a valid id.'}, status=400)
        profiles = profiles.filter(employee_id=emp_uuid)

    profiles = list(profiles.order_by('employee__full_name'))
    profile_ids = [p.id for p in profiles]

    # One query for the whole leave year; bucket in Python.
    taken_before: dict[tuple, float] = {}
    taken_in: dict[tuple, float] = {}
    reqs = (LeaveRequest.objects
            .filter(profile_id__in=profile_ids,
                    status=LeaveRequest.Status.APPROVED,
                    start_date__gte=year_start, start_date__lte=date_to)
            .select_related('leave_type'))
    for lr in reqs:
        code = (lr.leave_type.code or lr.leave_type.name or '').lower().strip()
        key = (lr.profile_id, code)
        bucket = taken_in if lr.start_date >= date_from else taken_before
        bucket[key] = bucket.get(key, 0.0) + float(lr.days or 0)

    # Annual accrual months (CoS Section 7.5.1, monthly accrual).
    #
    # THE LAST *COMPLETED* MONTH, not today's month number. This was the third
    # place the same accrual was derived — the balance engine and the apply-time
    # gate were corrected for bug 60fcbd4a while this report, which HR actually
    # reads and uploads from, still credited the running month on its first day.
    # On 10 August it said 8 months where the other two said 7. For a calendar
    # leave year the count of completed months IS the number of the last
    # completed month, so it drops straight into the same arithmetic below.
    months_before = date_from.month - 1
    accrual_end_month = date_to.month
    if date_from.year == today.year:
        from hris.leave_balance import completed_months
        accrual_end_month = min(accrual_end_month, completed_months(today))
    # Same never-round-up rule as the balance engine — one helper, one truth, or
    # this report drifts from the balances screen again (H36).
    from hris.leave_balance import (COS_ANNUAL_ENTITLEMENTS,
                                    COS_ANNUAL_ENTITLEMENTS_TEXT, days_out)
    months_in_period = max(0, accrual_end_month - date_from.month + 1)

    def _accrual_months(first_month, hire_date, term_date=None):
        """Annual accrual months for ONE employee, and the month accrual starts from.

        Bug b013d4ec (reported 6 Aug 2026): the Accrued column showed two
        flat values company-wide — 3.5 for everyone with an uploaded opening balance
        and 14 for everyone without.

        Those figures are NOT wrong, and this deliberately does not change them.
        Each row balances: opening + accrued - taken = closing. What is wrong is
        that the column is not comparable, because each row counts a DIFFERENT
        number of months and the screen never said so:

          employee A   opening 0.0   accrued 14.0   counted from 1 Jan  (8 months)
          employee B   opening 0.0   accrued  3.5   counted from 30 Jun (2 months)

        Identical hire date, identical opening balance on screen, six months of
        accrual apart — because B has an HR-uploaded row stating 0 days as at
        30 Jun and A has no uploaded row at all. Every annual upload shares that
        one as-at date, which is why so many people show the identical 3.5.

        So the fix is to make each row state the date its accrual is counted from
        (`accrued_from`), rather than to quietly restate 90 people's leave.
        Changing those balances is an HR decision, not a reporting one.

        The one genuine miscalculation fixed here: accrual ignored the employee's
        own joining month, so somebody who started mid-period was credited from the
        period start. hire_date is missing on 80 of 173 profiles; when it is absent
        the result is exactly what the old code produced.
        """
        first = first_month
        if hire_date:
            if hire_date.year > date_from.year:
                return 0, None, True           # not employed in this leave year yet
            if hire_date.year == date_from.year:
                first = max(first, hire_date.month)
        # Nobody accrues after their last day of service. Oprah's spot-check
        # (2026-08-10) found leavers still gaining days every month. The three she
        # named are a DATA gap — HR has not recorded a leaving date, so they still
        # read as active — but the code gap is real for the eleven who ARE marked
        # terminated, and it was only hidden before because accrual sat frozen.
        last_month = accrual_end_month
        if term_date and term_date.year == date_from.year:
            last_month = min(last_month, term_date.month)
        elif term_date and term_date.year < date_from.year:
            return 0, None, bool(hire_date)    # left before this leave year began
        months = max(0, last_month - first + 1)
        start = _dt.date(date_from.year, min(first, 12), 1) if months > 0 else None
        # DeepSeek review, 6 Aug 2026: with no hire_date on record, `accrued_from`
        # would state a start month as fact for the 80 of 173 profiles that have
        # none — presenting an assumption as data, which is the exact failure this
        # change exists to stop. Flag it so the screen can mark it as assumed.
        return months, start, bool(hire_date)

    # HR's UPLOADED figures win over the formula. This report is the page HR uploads opening
    # balances FROM — the upload tile sits on it — and until now it never read them back: the
    # columns were computed purely from CoS rules x months. So HR uploaded corrected balances,
    # looked at this table, and saw nothing change. That is bug c2888ba7, and fixing the
    # balance query alone would not have changed a single number on this screen.
    #
    # An uploaded row is the truth AS AT its own date, so it becomes the anchor: the opening is
    # taken from it, and accrual and leave taken are counted from that date forward. Rows say
    # where their figure came from (`source`, `as_at`) so nobody has to guess.
    from hris.models import LeaveOpeningBalance
    anchors = {}
    for ob in (LeaveOpeningBalance.objects
               .filter(profile__in=profiles, as_at_date__lte=date_to)
               .order_by('profile_id', 'leave_type_code', '-as_at_date', '-created_at')):
        anchors.setdefault((ob.profile_id, (ob.leave_type_code or '').lower().strip()), ob)

    taken_since_anchor = {}
    if anchors:
        from hris.models import LeaveRequest as _LR
        for lr in (_LR.objects
                   .filter(profile__in=profiles, status=_LR.Status.APPROVED,
                           start_date__lte=date_to)
                   .select_related('leave_type')):
            c = (lr.leave_type.code or lr.leave_type.name or '').lower().strip()
            ob = anchors.get((lr.profile_id, c))
            if ob is not None and lr.start_date >= ob.as_at_date:
                k = (lr.profile_id, c)
                taken_since_anchor[k] = taken_since_anchor.get(k, 0.0) + float(lr.days or 0)

    # Leave encashment (bug 0329c5a0, 2026-08-20). This
    # report computed opening + accrued - taken and had no encashment term at
    # all, so days already cashed out still read as available: on prod that was
    # 53.0 days over-stated across five people, two of them already PAID. The
    # employee's own balance (hris/leave_balance.py) has reserved them since
    # 2026-07-21, which is why the same encashment looked correct on one screen
    # and missing on the other.
    #
    # Same rules as leave_balance.balances_for_profile, so the two screens
    # cannot drift again:
    #   * OPEN_STATUSES reserve (every state except REJECTED) — days stay held
    #     through approval AND after payment; only a rejection releases them.
    #   * Against an uploaded anchor, only encashments raised ON OR AFTER the
    #     anchor date count — earlier ones are already inside HR's figure and
    #     deducting them again would rob the employee twice.
    # Bounded by date_to so a prior-year report is not restated by a later
    # encashment (this report is year-scoped; the balance engine is not).
    encashed_map: dict[tuple, float] = {}
    if profiles:
        from hris.leave_encash_models import LeaveEncashment
        for e in LeaveEncashment.objects.filter(
                profile__in=profiles,
                status__in=LeaveEncashment.OPEN_STATUSES):
            c = (e.leave_type_code or '').lower().strip()
            raised = e.created_at.date()
            if raised > date_to:
                continue
            ob_e = anchors.get((e.profile_id, c))
            if ob_e is not None and raised < ob_e.as_at_date:
                continue
            k = (e.profile_id, c)
            encashed_map[k] = encashed_map.get(k, 0.0) + float(e.days or 0)

    rows = []
    active_employees = set()
    total_taken = 0.0
    for p in profiles:
        emp = p.employee
        for code in codes:
            rule = leave_rules[code]
            ent = float(rule['days'])
            before = taken_before.get((p.id, code), 0.0)
            taken = taken_in.get((p.id, code), 0.0)
            source, as_at_str = 'cos_formula', None
            accrued_from, from_known = None, False
            accrued_note = None
            entitlement_check = None
            hire_date = getattr(emp, 'hire_date', None)
            term_date = getattr(emp, 'termination_date', None)
            ob = anchors.get((p.id, code))
            if ob is not None:
                # Anchored on HR's uploaded position.
                source = 'hr_upload'
                as_at_str = ob.as_at_date.isoformat()
                opening = days_out(float(ob.opening_balance_days))
                taken = days_out(taken_since_anchor.get((p.id, code), 0.0))
                if code == 'annual':
                    # Accrual runs from the month AFTER the anchor — the anchor is the
                    # truth as at its own date — or from the joining month if later.
                    first_month = (ob.as_at_date.month + 1
                                   if ob.as_at_date.year == date_to.year
                                   else date_from.month)
                    months_since, accrued_from, from_known = _accrual_months(
                        first_month, hire_date, term_date)
                    # The start date here comes from HR's uploaded anchor, not from a
                    # period default, so it is FACT even when hire_date is missing.
                    # Leaving it as bool(hire_date) labelled these rows "(assumed)" —
                    # wrong twice over, and on exactly the rows this bug was about.
                    from_known = True
                    # Bug 3c45e603: filter the report to a period that ENDS on or
                    # before the anchor date (e.g. 01–30 Jun, anchor 30 Jun) and every
                    # anchored row shows a bare 0.0 while unanchored rows show 1.8.
                    # That 0.0 is right — an opening balance dated 30 Jun already
                    # contains June, so nothing has accrued since — but unlabelled it
                    # reads as "the accrual engine is ignoring these employees", which
                    # is how it was reported (and raised as an audit observation).
                    if months_since == 0:
                        accrued_note = (f'opening balance is dated '
                                        f'{ob.as_at_date:%d %b} — nothing accrues after it '
                                        f'within this period')
                    # THE EMPLOYEE'S OWN ENTITLEMENT, not the policy default.
                    # Oprah Mogomotsi's spot-check (2026-08-10) found every anchored
                    # row accruing a flat 1.8 days a month — 21/12 — because this
                    # line used the CoS default while the uploaded anchor sitting
                    # right here carries the real figure. On prod that is 18 days for
                    # 51 people (over-credited 0.3/month) and 25 for 8 (under-credited
                    # 0.3/month). Her diagnosis was the un-corrected grade
                    # entitlements; the data says otherwise — this report was
                    # ignoring the uploads. Same fallback as leave_balance: a
                    # stored 0 means the column was left blank.
                    #
                    # 11 Aug: "the uploads are right" was true of 64 rows, not all
                    # 90 — see the entitlement_check below before trusting this.
                    ent_row = (float(ob.entitlement_days) if ob.entitlement_days
                               else ent)
                    accrued = days_out(ent_row * months_since / 12.0)
                    # The uploaded entitlement column is NOT trustworthy on every
                    # row, so say so on the row instead of printing a confident
                    # wrong rate. The June tracker was an aggregate ledger parsed
                    # off-server, and on 26 of the 90 uploaded annual rows what
                    # landed in `entitlement_days` is the year's TOTAL credit
                    # (brought-forward + earned), not the contractual entitlement:
                    # 53.00, 52.50, 46.00, 34.50 … alongside an `accrued_days` of
                    # a clean 18.00. Dividing a total credit by twelve is not a
                    # monthly rate. HR has to restate the column; until then this
                    # marks the row for review rather than inventing a figure.
                    if ent_row not in COS_ANNUAL_ENTITLEMENTS:
                        entitlement_check = (
                            f'entitlement of {ent_row:g} days is not a Conditions '
                            f'of Service figure '
                            f'({COS_ANNUAL_ENTITLEMENTS_TEXT} days) — HR to '
                            f'confirm; the monthly rate on this row cannot be '
                            f'relied on')
                else:
                    accrued = None
            elif code == 'annual':
                opening = days_out(ent * months_before / 12.0 - before)
                months_acc, accrued_from, from_known = _accrual_months(
                    date_from.month, hire_date, term_date)
                accrued = days_out(ent * months_acc / 12.0)
            else:
                opening = days_out(ent - before)
                accrued = None
            encashed = days_out(encashed_map.get((p.id, code), 0.0))
            closing = days_out(opening + (accrued or 0.0) - taken - encashed)
            if taken > 0:
                active_employees.add(p.id)
                total_taken += taken
            rows.append({
                'employee_id':     str(emp.id),
                'employee_name':   emp.full_name,
                'department':      emp.department or '',
                'leave_type':      code.capitalize() + ' Leave',
                'leave_type_code': code,
                'opening_balance': opening,
                'accrued':         accrued,
                'taken':           days_out(taken),
                # Days cashed out and therefore no longer available to book.
                'encashed':        encashed,
                'closing_balance': closing,
                # Where the figure came from, so an uploaded correction is visibly an
                # uploaded correction and not a formula nobody can reconcile.
                'source':          source,
                'as_at':           as_at_str,
                # The date this row's accrual is counted FROM. Without it the column
                # is unreadable: 3.5 and 14.0 look like a discrepancy when they are
                # simply 2 months and 8 months. Null for flat leave types.
                'accrued_from':    accrued_from.isoformat() if accrued_from else None,
                # False = no hire date on record, so the start month is the period
                # default, not this employee's actual joining month. Shown as "assumed".
                'accrued_from_known': from_known,
                # Why an annual accrual is zero, when it is zero for a reason the
                # screen would not otherwise show. None when there is nothing to say.
                'accrued_note':    accrued_note,
                # Set when this row's uploaded entitlement is not a Conditions of
                # Service figure, so HR can see WHICH rows to restate instead of
                # re-auditing all ninety by hand. None = the entitlement is sound.
                'entitlement_check': entitlement_check,
            })

    headcount = len(active_employees)
    return Response({
        'date_from':  str(date_from),
        'date_to':    str(date_to),
        'leave_year': date_from.year,
        'clamped':    clamped,
        'count':      len(rows),
        'rows':       rows,
        'summary': {
            'headcount_active':     headcount,
            'total_days_taken':     days_out(total_taken),
            'avg_days_per_employee': days_out(total_taken / headcount) if headcount else 0.0,
        },
    })
