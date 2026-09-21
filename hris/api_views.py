"""
hris/api_views.py

JSON endpoints for the HRIS single-page app. Returns employee + profile
data in the same shape the HTML expects (matching the legacy EMPLOYEES
hardcoded array), so the HTML can replace the static array with a
fetch() call.

Auth: requires Django session or DRF Token (matches REST_FRAMEWORK
DEFAULT_AUTHENTICATION_CLASSES). The new Next.js /hris page sends the
session cookie / Bearer token; the legacy hris.html SPA must establish
a Django session via /admin/login/ or /api-token-auth/ before these
endpoints will return data. Previously open — closed 2026-05-13 after
audit found employee PII reachable without authentication.
"""
from __future__ import annotations
from hris.departments import fold_legacy

import json
from decimal import Decimal
from pathlib import Path

from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris, can_view_compensation
from core.hris_unlock import is_hris_unlocked
from hris.models import Grade, HRISProfile, OKR, PerformanceReview
from django.utils import timezone


def _deny_if_not_whitelisted(request):
    """Returns a 403/401 Response if the user can't see HRIS right now.

    Two layered checks (CFO directive 2026-05-18):
      403  user is not on the HRIS whitelist (Prathap / Arun / Kago /
            Pako / Unami + superuser / administrator).
      401  user is whitelisted but has not entered the HRIS password yet
            (or the unlock has expired). Body carries
            `requires_unlock: true` so the frontend modal triggers.
    """
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
    return None

# Fallback employee directory — used when HRISProfile is empty so the new
# Next.js Analytics page shows the same data as the legacy hris.html (which
# hardcodes the array). When HR seeds the real models, this disappears.
_SEED_PATH = Path(__file__).parent / 'data' / 'employees_seed.json'
try:
    with _SEED_PATH.open() as _f:
        _EMPLOYEE_SEED = json.load(_f)
except (FileNotFoundError, json.JSONDecodeError):
    _EMPLOYEE_SEED = []


def _safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _age(dob):
    """Whole years from a date_of_birth to today, or 0 if unset/invalid.
    BUG 7dedf09f (Oprah 2026-06-26): the employee record hardcoded age=0, so HR
    Analytics showed Avg Age 0 and empty age brackets. Now computed from
    HRISProfile.date_of_birth (the field exists; this just reads it)."""
    if not dob:
        return 0
    today = timezone.localdate()
    try:
        return max(0, today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)))
    except (AttributeError, TypeError, ValueError):
        return 0


def _profile_to_dict(profile: HRISProfile, show_comp: bool = True) -> dict:
    """Shape one HRIS profile into the HTML's expected EMPLOYEES record.

    `show_comp=False` (the read-only hr_viewer tier) blanks the grade-band
    salary so a viewer without compensation rights never receives pay figures —
    the value is withheld server-side, not merely hidden in the UI."""
    emp = profile.employee
    grade = profile.grade
    pr = (PerformanceReview.objects
          .filter(profile=profile)
          .order_by('-period')
          .first())
    okrs = list(OKR.objects.filter(profile=profile).order_by('-period', '-weight_pct'))

    gender_map = {'M': 'Male', 'F': 'Female', 'O': 'Other'}

    return {
        'id':       _safe_int(emp.external_ref.split(':')[-1] if ':' in (emp.external_ref or '') else emp.id),
        'pid':      str(profile.pk),
        'eid':      str(emp.pk),   # Employee UUID — target for HRIS amendments

        'segment':  profile.talent_segment or '',
        'nm':       emp.full_name,
        # Email + employee number let the Amendments picker search by them and
        # show them on the confirm card, so two people with the same name can
        # be told apart (CFO file 2 Medium, 18-Sep-2026). Same rows, same scope.
        'email':    emp.email or '',
        'en':       emp.employee_number or '',
        'ps':       emp.job_title or '',
        'dp':       emp.department or '',
        # Bug 99d77cf5: Company has no `short_code` field (it's `code`), so the
        # hasattr() guard was always False and every row fell back to 'ADIC'.
        # HRIS-005 (2026-06-18): company=NULL means UNASSIGNED (M365 roster
        # imports awaiting an entity) — label it honestly, never phantom-'ADIC',
        # or the consolidated view over-counts ADIC and contradicts the filter.
        'company':  (emp.company.code if emp.company_id else 'Unassigned'),
        'grade':    grade.code if grade else '',
        'salary':   (float(grade.midpoint) if grade else 0.0) if show_comp else None,
        'hired':    emp.hire_date.isoformat() if emp.hire_date else '',
        'mg':       profile.manager.full_name if profile.manager else '',
        # Manager's Employee UUID — lets the amendments UI preselect/set the
        # "reports to" relationship (bugs f05470e2 / 6b12dde5).
        'mid':      str(profile.manager_id) if profile.manager_id else '',
        'img':      profile.initials or ''.join(p[:1] for p in emp.full_name.split()[:2]).upper(),
        'gn':       gender_map.get(profile.gender, ''),
        'age':      _age(profile.date_of_birth),   # BUG 7dedf09f — was hardcoded 0
        'loc':      profile.location or '',
        'tribe':    profile.demographic_marker or '',
        'grossActual': 0,         # populated by payroll engine in Phase 2
        'payeActual':  0,
        'netActual':   0,
        'comp':     pr.competency_scores if pr else {},
        'vals':     pr.values_scores if pr else [],
        'pot':      pr.potential_scores if pr else [],
        'okrs':     [
            {
                'nm': o.name,
                'w': float(o.weight_pct),
                's1': float(o.score_h1 or 0),
                's2': float(o.score_h2 or 0),
            }
            for o in okrs
        ],
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def employees(request):
    """Returns the live employee directory in HRIS HTML format.

    Requires authentication AND whitelist membership — employee names,
    salaries, ages and demographic markers are DPA-regulated and the
    CFO restricts the audience to a 5-person whitelist plus admins.
    """
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied
    from payroll.models import Employee
    profiles = (HRISProfile.objects
                .select_related('employee', 'employee__company', 'grade', 'manager')
                .exclude(employee__is_archived=True)
                .exclude(employee__status=Employee.Status.TERMINATED)
                .order_by('employee__full_name'))
    # Terminated Employee Archive (Oprah Mogomotsi, 2026-08-24): this endpoint
    # feeds BOTH the People Directory and the HRIS home "Humans" headcount tile
    # (frontend counts employees.length). A directory + a live headcount are a
    # "who works here now" view, so a leaver belongs in neither — exclude both
    # archived staff (the manual HR action) AND anyone whose status is TERMINATED
    # but who has not been archived yet (e.g. a final-settlement leaver, or an
    # old cross-company transfer row kept for payslip history). The exclusion is
    # group-wide by construction: is_archived / status are row-level flags on the
    # shared payroll.Employee, so every entity (RSA…VCM) is covered at once.
    # CFO directive 2026-05-18 multi-entity isolation audit: scope to the
    # company picked in the topbar (apiFetch auto-injects ?company=<id>).
    # 2026-05-19: accept Company.code too via shared resolver helper.
    # Entity scope (CFO 2026-06-16): a user restricted via UserCompanyAccess
    # (e.g. ADRisk-only) is clamped to their granted companies server-side,
    # whatever ?company= they send. Unrestricted users (CFO/admin) keep the
    # consolidated view.
    # Entity isolation (CFO 2026-06-16; HRIS-006 fix 2026-06-18): EVERY caller —
    # HR-tier included — is clamped to their UserCompanyAccess grant via
    # apply_company_scope. The genuinely-unrestricted bucket (CFO / admin, or a
    # whitelisted HR user with NO explicit per-entity grant) keeps the
    # consolidated view incl. the company=NULL M365 imports and honours the
    # topbar entity selection; an entity-restricted user (e.g. ADRG-only) sees
    # ONLY their entity and can never reach another's staff (e.g. ADIC).
    # The HRIS-004 HR-tier branch BYPASSED this clamp and leaked ADIC employees
    # to an ADRG-scoped HR_MANAGER (Lakshmi) — removed.
    from core.mixins import resolve_company_id_param, apply_company_scope, scoped_company_ids
    company_id = resolve_company_id_param(request)
    profiles = apply_company_scope(request, profiles, 'employee__company_id')

    show_comp = can_view_compensation(request.user)
    data = [_profile_to_dict(p, show_comp=show_comp) for p in profiles]
    if show_comp and not data and not company_id and scoped_company_ids(request) is None:
        # Seed fallback ONLY for the genuinely-unrestricted consolidated view
        # (so the legacy snapshot renders before HR populates HRISProfile).
        # A scoped user with no rows must see zero, never the seed.
        data = _EMPLOYEE_SEED
    return Response({'count': len(data), 'employees': data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def grades(request):
    """Returns the pay-grade catalogue in HRIS HTML format."""
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied
    show_comp = can_view_compensation(request.user)
    qs = Grade.objects.filter(is_active=True).order_by('-level', 'code')
    data = [
        {
            'code': g.code,
            'name': g.name,
            'level': g.level,
            'spread': g.spread,
            # Grade midpoint is a pay figure — withheld from a viewer without
            # compensation rights (the read-only hr_viewer tier).
            'midpoint': float(g.midpoint) if show_comp else None,
        }
        for g in qs
    ]
    return Response({'count': len(data), 'grades': data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def onboard_employee(request):
    """Create a new staff member end-to-end so they appear across omni.

    Fills the gap where a payroll-only add (or an M365 sync) leaves a person
    without the HRIS extension row or a login link, so they show in one section
    but not the others (the two Health interns, 2026-08-28). Given a person's
    details this creates/ensures, in one action:
      * payroll.Employee        — the person record
      * hris.HRISProfile        — the talent / leave / profile extension
      * Employee.user link       — to an existing login with the same email, if
                                   one exists (else it self-links on first
                                   Microsoft sign-in, which matches by email)
    Idempotent by email: if an Employee already exists it is *completed*
    (missing HRIS row / user link / blank fields filled) rather than
    duplicated. HRIS-whitelisted + unlocked only (same gate as the rest of HRIS).
    """
    denied = _deny_if_not_whitelisted(request)
    if denied is not None:
        return denied

    import uuid
    from payroll.models import Employee
    from core.models import Company
    from django.contrib.auth import get_user_model

    data = request.data or {}
    full_name = (data.get('full_name') or '').strip()
    email = (data.get('email') or '').strip().lower()
    if not full_name:
        return Response({'detail': 'Full name is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if not email:
        return Response({'detail': 'Work email is required — it links the person to their login.'},
                        status=status.HTTP_400_BAD_REQUEST)

    department = (data.get('department') or '').strip()[:100]
    job_title  = (data.get('job_title') or '').strip()[:100]
    phone      = (data.get('phone') or '').strip()[:50]
    hire_date  = (data.get('hire_date') or None) or None

    company = None
    cid = data.get('company_id')
    if cid:
        company = Company.objects.filter(id=cid).first()
        if company is None:
            return Response({'detail': 'That entity was not found.'}, status=status.HTTP_400_BAD_REQUEST)

    User = get_user_model()
    existing = Employee.objects.filter(email__iexact=email).first()
    created = False
    if existing is None:
        emp = Employee.objects.create(
            employee_number='NEW-' + uuid.uuid4().hex[:12],
            full_name=full_name,
            email=email,
            department=department,
            job_title=job_title,
            phone=phone,
            hire_date=hire_date,
            company=company,
            status=Employee.Status.ACTIVE,
            external_ref='omni-onboard',
        )
        created = True
    else:
        # Complete the record without clobbering anything already captured.
        emp = existing
        changed = []
        if not emp.job_title and job_title:  emp.job_title = job_title;   changed.append('job_title')
        if not emp.department and department: emp.department = fold_legacy(department) or department; changed.append('department')
        if not emp.company_id and company:   emp.company = company;       changed.append('company')
        if not emp.hire_date and hire_date:  emp.hire_date = hire_date;   changed.append('hire_date')
        if not emp.phone and phone:          emp.phone = phone;           changed.append('phone')
        if changed:
            emp.save(update_fields=changed)

    HRISProfile.objects.get_or_create(
        employee=emp,
        defaults={'talent_segment': HRISProfile.TalentSegment.NEW_HIRE},
    )

    # Link an existing login (same email). If none yet, the login self-links on
    # first Microsoft sign-in, so we do not create one here.
    if not emp.user_id:
        u = User.objects.filter(email__iexact=email).first()
        if u:
            emp.user = u
            emp.save(update_fields=['user'])

    return Response(
        {
            'employee_id': str(emp.id),
            'name': emp.full_name,
            'created': created,
            'completed_existing': not created,
            'login_linked': bool(emp.user_id),
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )
