"""
hris/perf_monthly_views.py — the MONTHLY manager-feedback layer (CFO 2026-07-20).

Sits on top of the existing ELRA MonthlyCheckIn record. Gives a manager, per
direct report: the decision panel (Time Doctor hours, absence, leave, sick, task
on-time), their standing targets with the month's actual, a 3-month trend, this
month's feedback status, and a pre-filled draft. Plus target management (HR/full)
and a C-suite manager league table. Reuses the perf module's feature gate + scope
helpers verbatim — no second access model.
"""
from __future__ import annotations

import datetime as dt

from django.utils import timezone
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from core.mixins import apply_company_scope
from hris.models import HRISProfile
from hris.performance_feedback_models import MonthlyCheckIn
from hris.performance_target_models import PerformanceTarget, PerformanceTargetResult
from hris.performance_views import _feature_off, _resolve_employee


# ── who still owes monthly feedback (CFO 2026-09-07, idea #8) ────────────────
# Read by the Omni QC agent on the CFO's Mac (scoped 'qc-bot' key) so it can
# nudge managers on Telegram with tappable names. Mirrors the cycle command:
# period = previous month; a report is owed when no MonthlyCheckIn exists for it.
# Team = the same people the one-click page shows (manager OR co-manager).
def _qc_bot_or_cfo(request) -> bool:
    scopes = getattr(getattr(request, 'auth', None), 'allowed_scopes', None) or []
    if 'qc-bot' in scopes or 'admin' in scopes:
        return True
    u = request.user
    p = getattr(u, 'profile', None)
    return bool(u.is_superuser or (p and (p.is_administrator or p.title == 'cfo')))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def owed_feedback(request):
    if not _qc_bot_or_cfo(request):
        return Response({'detail': 'CFO or the QC agent only.'}, status=status.HTTP_403_FORBIDDEN)
    from payroll.models import Employee
    from hris.manager_feedback_actions import _reports, action_url
    today = timezone.localdate()
    if request.query_params.get('year') and request.query_params.get('month'):
        year, month = int(request.query_params['year']), int(request.query_params['month'])
    else:
        first = today.replace(day=1) - dt.timedelta(days=1)
        year, month = first.year, first.month
    mgr_ids = set(HRISProfile.objects.exclude(manager=None).values_list('manager', flat=True))
    mgr_ids |= set(HRISProfile.objects.exclude(co_manager=None).values_list('co_manager', flat=True))
    out = []
    for mgr in Employee.objects.filter(pk__in=list(mgr_ids)).exclude(status=Employee.Status.TERMINATED):
        owed = [p for p in _reports(mgr)
                if not MonthlyCheckIn.objects.filter(profile=p, period_year=year, period_month=month).exists()]
        if not owed:
            continue
        one_click = action_url(mgr, year, month)
        out.append({
            # some employee records carry no email; the login does
            'email': ((mgr.email or getattr(getattr(mgr, 'user', None), 'email', '') or '')).lower(),
            'name': mgr.full_name,
            'one_click': one_click,
            'owed': [{'name': p.employee.full_name, 'url': f'{one_click}?profile={p.pk}'} for p in owed if p.employee],
        })
    return Response({'period': f'{year}-{month:02d}', 'managers': out})
from hris import perf_panel

_MIN_YEAR, _MAX_YEAR = 2020, 2100


def _clean_period(year, month, default):
    """Clamp to sane bounds; None on invalid so callers can 400."""
    try:
        y, m = int(year), int(month)
    except (TypeError, ValueError):
        return default
    if not (_MIN_YEAR <= y <= _MAX_YEAR) or not (1 <= m <= 12):
        return None
    return y, m


def _period(request):
    """(year, month) from query params, default current month; clamped."""
    today = timezone.localdate()
    got = _clean_period(request.query_params.get('year') or today.year,
                        request.query_params.get('month') or today.month,
                        (today.year, today.month))
    return got or (today.year, today.month)


def _scoped_profiles(request):
    """HRISProfile queryset the caller may touch, entity-clamped like the sibling
    perf module (an entity-pinned HR user must NOT see other entities)."""
    base = (HRISProfile.objects.select_related('employee', 'employee__company')
            .exclude(employee=None))
    return apply_company_scope(request, base, 'employee__company_id')


def _profile_in_scope(request, profile) -> bool:
    return _scoped_profiles(request).filter(pk=profile.pk).exists()


def _reports_for(request):
    """(tier, [HRISProfile]) the caller may give monthly feedback on.

    manager → own direct reports AND anyone they co-review. full (HR/exec) →
    entity-clamped; defaults to the caller's own reports, or a specific manager's
    team via ?manager=<employee_id> (C-suite drill-down). This keeps every request
    bounded (no all-employee fan-out) AND honours the entity trust boundary.

    CO-REVIEWERS COUNT. `co_manager` exists so a second person who works with
    someone daily can also rate them without changing the reporting line, and
    co-review, manager returns and roster flags all honour it — this page was the
    only manager surface that did not, so a co-reviewer opened it and saw an
    employee list missing the very people they were asked to comment on."""
    from payroll.models import Employee
    me = _resolve_employee(request.user)
    if user_can_access_hris(request.user):
        base = _scoped_profiles(request).exclude(employee__status=Employee.Status.TERMINATED)
        mgr_id = request.query_params.get('manager')
        if mgr_id:
            base = base.filter(Q(manager_id=mgr_id) | Q(co_manager_id=mgr_id))
        elif me is not None:
            base = base.filter(Q(manager=me) | Q(co_manager=me))
        else:
            base = base.none()   # C-suite with no reports + no drill → league only
        return 'full', list(base.distinct().order_by('employee__full_name'))
    if me is not None:
        from payroll.models import Employee as E
        reports = list(HRISProfile.objects.select_related('employee')
                       .filter(Q(manager=me) | Q(co_manager=me))
                       .exclude(employee__status=E.Status.TERMINATED)
                       .distinct()
                       .order_by('employee__full_name'))
        if reports:
            return 'manager', reports
    return None, []


def _has_checkin(profile, year, month):
    return MonthlyCheckIn.objects.filter(
        profile=profile, period_year=year, period_month=month).first()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def monthly_feedback(request):
    """Manager (or HR/exec) monthly feedback worklist for the period."""
    off = _feature_off()
    if off:
        return off
    tier, reports = _reports_for(request)
    if tier is None:
        return Response({'detail': 'No direct reports and no HR access.'},
                        status=status.HTTP_403_FORBIDDEN)
    year, month = _period(request)
    out = []
    for prof in reports:
        emp = prof.employee
        if emp is None:
            continue
        panel = perf_panel.employee_month_panel(emp, year, month)
        targets = perf_panel.target_lines(prof, year, month)
        ci = _has_checkin(prof, year, month)
        out.append({
            'profile_id': str(prof.pk),
            'name': emp.full_name,
            'position': getattr(emp, 'job_title', '') or '',
            'panel': panel,
            'targets': targets,
            'trend': perf_panel.trend(prof, year, month, months=3),
            'feedback_given': ci is not None,
            'checkin_id': str(ci.id) if ci else None,
            'checkin_locked': bool(ci.is_locked) if ci else False,
            'draft': None if ci else perf_panel.draft_feedback(panel, targets),
        })
    return Response({
        'tier': tier,
        'period': {'year': year, 'month': month},
        'employees': out,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def manager_league(request):
    """C-suite league table: which managers gave feedback this month, which didn't."""
    off = _feature_off()
    if off:
        return off
    if not user_can_access_hris(request.user):
        return Response({'detail': 'HR / executive access only.'},
                        status=status.HTTP_403_FORBIDDEN)
    year, month = _period(request)
    from payroll.models import Employee

    # entity-clamped: a pinned HR user's league covers only their entities.
    scoped = _scoped_profiles(request).exclude(employee__status=Employee.Status.TERMINATED)
    mgr_ids = scoped.exclude(manager=None).values_list('manager', flat=True).distinct()
    rows = []
    for mgr in Employee.objects.filter(pk__in=list(mgr_ids)):
        report_profiles = scoped.filter(manager=mgr)
        total = report_profiles.count()
        if not total:
            continue
        period_rows = MonthlyCheckIn.objects.filter(
            profile__in=report_profiles, period_year=year, period_month=month)
        # An auto-posted month is Omni doing the manager's job, NOT the manager
        # doing it. Counting it as "given" made every silent manager read 100%
        # complete from the 7th — the automation erasing the very delinquency
        # this table exists to show (Fable, 2026-08-26).
        auto = period_rows.filter(auto_posted=True).count()
        given = period_rows.filter(auto_posted=False).count()
        rows.append({
            'manager': mgr.full_name,
            'manager_id': str(mgr.pk),
            'reports': total,
            'given': given,
            'auto_posted': auto,
            'outstanding': max(total - given, 0),
            'complete': given >= total and total > 0,
        })
    # worst offenders first (most outstanding), then by name
    rows.sort(key=lambda r: (-r['outstanding'], r['manager']))
    return Response({'period': {'year': year, 'month': month}, 'managers': rows})


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def targets(request):
    """List (scoped) or create (HR/full) standing performance targets."""
    off = _feature_off()
    if off:
        return off
    if request.method == 'GET':
        tier, reports = _reports_for(request)
        if tier is None:
            return Response({'detail': 'No access.'}, status=status.HTTP_403_FORBIDDEN)
        prof_ids = [p.pk for p in reports]
        qs = (PerformanceTarget.objects.filter(profile_id__in=prof_ids)
              .select_related('profile', 'profile__employee'))
        return Response({'targets': [_target_json(t) for t in qs]})

    # POST — create. HR/exec only (they own the JD → target mapping).
    if not user_can_access_hris(request.user):
        return Response({'detail': 'HR / executive access only.'},
                        status=status.HTTP_403_FORBIDDEN)
    body = request.data if isinstance(request.data, dict) else {}
    try:
        prof = HRISProfile.objects.get(pk=body.get('profile'))
    except (HRISProfile.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'Unknown employee profile.'}, status=status.HTTP_400_BAD_REQUEST)
    if not _profile_in_scope(request, prof):
        return Response({'detail': 'Employee is outside your entity access.'},
                        status=status.HTTP_403_FORBIDDEN)
    metric = (body.get('metric') or '').strip()
    if not metric:
        return Response({'detail': 'A metric name is required.'}, status=status.HTTP_400_BAD_REQUEST)
    tgt = PerformanceTarget.objects.create(
        profile=prof,
        metric=metric[:200],
        target_value=_dec(body.get('target_value')),
        unit=_choice(body.get('unit'), PerformanceTarget.Unit, PerformanceTarget.Unit.BWP),
        cadence=_choice(body.get('cadence'), PerformanceTarget.Cadence, PerformanceTarget.Cadence.MONTHLY),
        source=_choice(body.get('source'), PerformanceTarget.Source, PerformanceTarget.Source.MANUAL),
        note=(body.get('note') or '').strip(),
        created_by=request.user,
    )
    return Response(_target_json(tgt), status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def target_detail(request, pk):
    """Edit / deactivate a target (HR/full only)."""
    off = _feature_off()
    if off:
        return off
    if not user_can_access_hris(request.user):
        return Response({'detail': 'HR / executive access only.'},
                        status=status.HTTP_403_FORBIDDEN)
    try:
        tgt = PerformanceTarget.objects.select_related('profile', 'profile__employee').get(pk=pk)
    except (PerformanceTarget.DoesNotExist, ValueError):
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if not _profile_in_scope(request, tgt.profile):
        return Response({'detail': 'Outside your entity access.'}, status=status.HTTP_403_FORBIDDEN)
    if request.method == 'DELETE':
        tgt.active = False
        tgt.save(update_fields=['active', 'updated_at'])
        return Response(status=status.HTTP_204_NO_CONTENT)
    body = request.data if isinstance(request.data, dict) else {}
    for f in ('metric', 'note'):
        if f in body:
            setattr(tgt, f, (body.get(f) or '').strip()[:200 if f == 'metric' else 100000])
    if 'target_value' in body:
        tgt.target_value = _dec(body.get('target_value'))
    if 'unit' in body:
        tgt.unit = _choice(body.get('unit'), PerformanceTarget.Unit, tgt.unit)
    if 'source' in body:
        tgt.source = _choice(body.get('source'), PerformanceTarget.Source, tgt.source)
    if 'active' in body:
        tgt.active = bool(body.get('active'))
    tgt.save()
    return Response(_target_json(tgt))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_target(request):
    """Record a target's actual + achieved for a month (manager for own report, or HR)."""
    off = _feature_off()
    if off:
        return off
    body = request.data if isinstance(request.data, dict) else {}
    try:
        tgt = PerformanceTarget.objects.select_related('profile').get(pk=body.get('target_id'))
    except (PerformanceTarget.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'Unknown target.'}, status=status.HTTP_400_BAD_REQUEST)

    # permission: HR/full (entity-scoped), or the manager of this target's employee
    me = _resolve_employee(request.user)
    is_hr = user_can_access_hris(request.user) and _profile_in_scope(request, tgt.profile)
    is_mgr = me is not None and tgt.profile.manager_id == getattr(me, 'pk', None)
    if not (is_hr or is_mgr):
        return Response({'detail': 'Not your report.'}, status=status.HTTP_403_FORBIDDEN)

    if body.get('year') or body.get('month'):
        got = _clean_period(body.get('year'), body.get('month'), None)
        if got is None:
            return Response({'detail': 'Invalid year / month.'}, status=status.HTTP_400_BAD_REQUEST)
        year, month = got
    else:
        year, month = _period(request)
    from hris.perf_target_source import is_achieved, pull_actual
    # Health targets: re-pull the authoritative VAT split. Manual: the manager's
    # figure is the VAT-exclusive (net) actual; VAT/incl unknown.
    excl = vat = incl = None
    source_used = body.get('source_used') or 'manual'
    if tgt.source == PerformanceTarget.Source.HEALTH_QUOTES:
        bd, src = pull_actual(tgt, year, month)
        if bd:
            excl, vat, incl = bd['excl'], bd['vat'], bd['incl']
            source_used = src or 'health_quotes'
    if excl is None:
        excl = _dec(body.get('actual_value')) if body.get('actual_value') not in (None, '') else None
    achieved = body.get('achieved')
    if achieved is None and excl is not None:
        achieved = is_achieved(tgt, excl)

    ci = _has_checkin(tgt.profile, year, month)
    res, _created = PerformanceTargetResult.objects.update_or_create(
        target=tgt, period_year=year, period_month=month,
        defaults={
            'profile': tgt.profile,
            'target_value': tgt.target_value,
            'actual_value': excl, 'actual_excl': excl, 'actual_vat': vat, 'actual_incl': incl,
            'achieved': None if achieved is None else bool(achieved),
            'source_used': source_used,
            'note': (body.get('note') or '').strip(),
            'checkin': ci,
            'recorded_by': request.user,
        })
    return Response({
        'target_id': str(tgt.id),
        'period': {'year': year, 'month': month},
        'actual_value': None if res.actual_value is None else float(res.actual_value),
        'actual_excl': None if res.actual_excl is None else float(res.actual_excl),
        'actual_vat': None if res.actual_vat is None else float(res.actual_vat),
        'actual_incl': None if res.actual_incl is None else float(res.actual_incl),
        'achieved': res.achieved,
    }, status=status.HTTP_200_OK)


# ── helpers ──────────────────────────────────────────────────────────────────
def _target_json(t):
    return {
        'id': str(t.id),
        'profile_id': str(t.profile_id),
        'name': getattr(getattr(t.profile, 'employee', None), 'full_name', ''),
        'metric': t.metric,
        'target_value': float(t.target_value),
        'unit': t.unit,
        'cadence': t.cadence,
        'source': t.source,
        'active': t.active,
        'note': t.note,
    }


def _dec(v):
    from decimal import Decimal, InvalidOperation
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')
    return d if d.is_finite() else Decimal('0')   # reject inf/nan (would DataError on save)


def _choice(v, enum, default):
    v = str(v or '').strip()   # tolerate non-string JSON (e.g. {"unit": 5})
    return v if v in enum.values else default
