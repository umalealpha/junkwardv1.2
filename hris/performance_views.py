"""
hris/performance_views.py — ELRA monthly performance check-in + PIP API.

DORMANT until settings.ELRA_PERF_ENABLED is True (the DPIA / counsel gate — this
is employee personal data). Every endpoint is HRIS-whitelist + unlock gated AND
entity-scoped (a caller sees only their granted entity's records — the same
clamp as HRIS-006). Signed-off check-ins are read-only (addendum-only).
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import apply_company_scope
from hris.api_views import _deny_if_not_whitelisted
from hris.models import HRISProfile
from hris.performance_feedback_models import (
    LOW_RATINGS, MonthlyCheckIn, PerformanceImprovementPlan,
)


def _feature_off():
    """403 until the module is switched on post-DPIA. No PII can flow before then."""
    if not getattr(settings, 'ELRA_PERF_ENABLED', False):
        return Response(
            {'detail': 'The ELRA performance module is not yet enabled (pending DPIA / sign-off).'},
            status=status.HTTP_403_FORBIDDEN)
    return None


# ── Access model (CFO directive 2026-07-05) ──────────────────────────────────
# Monthly performance feedback is recorded for ALL staff, but the FINAL DATA is
# visible only to a tight audience:
#   • FULL tier  — CEO, CFO, COO, the HR team and Unami: every record (still
#                  clamped to their UserCompanyAccess entity grant, as elsewhere).
#     This is exactly the existing HRIS whitelist (user_can_access_hris), which
#     already covers Prathap/Arun (CEO)/Kago/Pako/Unami/Dorothy + superusers;
#     the COO (arjuniyer) is a superuser and is also on the whitelist.
#   • MANAGER tier — a line manager sees ONLY their own direct reports'
#                    check-ins (HRISProfile.manager == that manager's Employee).
#   • Everyone else — 403.
def _resolve_employee(user):
    """Map a Django auth user to their payroll.Employee (by user link, then email)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    from payroll.models import Employee
    emp = Employee.objects.filter(user=user).first()
    if emp:
        return emp
    email = (getattr(user, 'email', '') or '').strip()
    if email:
        return Employee.objects.filter(email__iexact=email).first()
    return None


def _direct_report_profile_ids(user):
    """HRISProfile ids this user manages — line-managed AND co-managed.

    co_manager matters: auto-posting and the push-back notification both resolve
    through co_manager, so without it a co-manager received a HIGH task telling
    them to answer an employee and then got a 404 on that very check-in — only HR
    could ever clear it (Fable round 2, 2026-08-27). Mirrors
    perf_monthly_views._scoped_profiles and manager_feedback_actions._reports,
    which have always resolved both.
    """
    from django.db.models import Q
    emp = _resolve_employee(user)
    if emp is None:
        return []
    return list(HRISProfile.objects
                .filter(Q(manager=emp) | Q(co_manager=emp))
                .values_list('pk', flat=True))


def _perf_scope(request):
    """Return (tier, queryset|None). tier in {'full','manager',None}.

    'full'    → all check-ins, entity-scoped (exec / HR whitelist).
    'manager' → only the caller's direct-reports' check-ins.
    None      → caller may not view any performance data (→ 403 upstream).
    """
    from core.hris_access import user_can_access_hris
    base = (MonthlyCheckIn.objects
            .select_related('profile', 'profile__employee', 'profile__employee__company'))
    # Resolve the caller's own Employee by user-link OR email (same robust
    # resolver managers use) — NOT the user FK alone, so email/legacy-linked
    # staff (e.g. ADRisk) can see + sign their own check-ins (CFO 2026-07-14).
    me_emp = _resolve_employee(request.user)
    # Explicit self view (mobile "My check-ins"): the caller's OWN records only,
    # even if they are also a manager/HR. ?mine=1 forces it.
    if str(request.query_params.get('mine', '')).lower() in ('1', 'true', 'yes'):
        return 'self', (base.filter(profile__employee=me_emp) if me_emp else base.none())
    if user_can_access_hris(request.user):
        return 'full', apply_company_scope(request, base, 'profile__employee__company_id')
    report_ids = _direct_report_profile_ids(request.user)
    if report_ids:
        return 'manager', base.filter(profile_id__in=report_ids)
    # Ordinary employee — their own check-ins (read + sign their side).
    if me_emp:
        own = base.filter(profile__employee=me_emp)
        if own.exists():
            return 'self', own
    return None, None


def _overdue_rating_block(profile, rating, year, month):
    """Response|None — refuse a rating the person's overdue work does not allow.

    CFO 2026-08-07: long-overdue tasks are an automatic negative. A written
    line in the feedback is easy to ignore, so the rating is capped too. The
    same rule runs on the one-click email form (hris.manager_feedback_actions),
    so neither screen can be used to dodge the other.
    """
    from hris import perf_panel
    if not rating:
        return None
    emp = getattr(profile, 'employee', None)
    if emp is None:
        return None
    panel = perf_panel.employee_month_panel(emp, year, month)
    if not perf_panel.rating_blocked(panel, rating):
        return None
    return Response(
        {'detail': perf_panel.rating_cap_message(panel),
         'rating_cap': perf_panel.CAP_RATING,
         'overdue_tasks': panel.get('overdue_tasks', [])},
        status=status.HTTP_400_BAD_REQUEST)


def _perf_deny():
    return Response(
        {'detail': 'Performance data is restricted to the employee’s manager, HR, and the executive team.'},
        status=status.HTTP_403_FORBIDDEN)


class MonthlyCheckInSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='profile.employee.full_name', read_only=True)
    entity = serializers.SerializerMethodField()

    class Meta:
        model = MonthlyCheckIn
        fields = [
            'id', 'profile', 'employee_name', 'entity', 'reviewer',
            'period_month', 'period_year', 'conversation_date', 'employment_status',
            'overall_rating', 'objectives', 'improvement_actions',
            'evidence', 'strengths', 'concerns', 'support_provided',
            'manager_comments', 'employee_response', 'employee_decision',
            'employee_ack', 'employee_ack_at',
            # Without these the screens could not even SHOW that a month was
            # posted by Omni or that a request is outstanding, so the push-back
            # valve was invisible to every UI (Fable round 2, 2026-08-27).
            'auto_posted', 'auto_posted_at',
            'employee_requested_comments', 'employee_requested_at',
            'manager_followup', 'manager_followup_at',
            'recurring_issue', 'consecutive_low_count', 'warning_recommended',
            'pip_triggered', 'referred_to_hr', 'follow_up_date',
            'manager_signed_at', 'employee_signed_at', 'is_locked', 'retention_until',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            # written only by the auto-post command and the push-back service,
            # never by a client PATCH
            'auto_posted', 'auto_posted_at', 'employee_requested_comments',
            'employee_requested_at', 'manager_followup_at',
            # manager_followup too: the dedicated branch fires only on a TRUTHY
            # value, so PATCH {"manager_followup": ""} fell through to ser.save()
            # and erased a stored answer while skipping the 50-char rule, the flag
            # clear and the timestamp. answer_request writes the model directly,
            # so nothing legitimate is blocked (Fable round 3).
            'manager_followup',
            'consecutive_low_count', 'warning_recommended', 'pip_triggered',
            'is_locked', 'retention_until', 'created_at', 'updated_at',
            # Employee-side fields are NEVER writable through the serializer — a
            # manager/HR must not be able to forge the employee's sign-off. The
            # employee sets them via the dedicated self-service path in
            # monthly_checkin_detail (CFO 2026-07-14 security fix).
            'employee_response', 'employee_decision', 'employee_ack',
            'employee_ack_at', 'employee_signed_at',
        ]

    def get_entity(self, obj):
        c = obj.profile.employee.company
        return c.code if c else 'Unassigned'

    def validate(self, attrs):
        # ELRA evidentiary guard at the API layer (model.clean() also enforces it).
        rating = attrs.get('overall_rating', getattr(self.instance, 'overall_rating', None))
        evidence = attrs.get('evidence', getattr(self.instance, 'evidence', '') or '')
        concerns = attrs.get('concerns', getattr(self.instance, 'concerns', '') or '')
        if rating in LOW_RATINGS:
            if not (evidence or '').strip():
                raise serializers.ValidationError(
                    {'evidence': 'Evidence is required for a Below / Significantly-below rating.'})
            if not (concerns or '').strip():
                raise serializers.ValidationError(
                    {'concerns': 'Areas of concern are required for a Below / Significantly-below rating.'})
        return attrs

    def validate_objectives(self, value):
        """objectives is a JSONField; force a list of {description,target,result}
        dicts so a bad client can't store a scalar/string that breaks the
        register render (which does objectives.length / .map)."""
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('Objectives must be a list.')
        clean = []
        for i, o in enumerate(value):
            if not isinstance(o, dict):
                raise serializers.ValidationError(f'Objective #{i + 1} must be an object.')
            clean.append({
                'description': str(o.get('description', '')),
                'target':      str(o.get('target', '')),
                'result':      str(o.get('result', '')),
            })
        return clean


class PIPSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='profile.employee.full_name', read_only=True)

    class Meta:
        model = PerformanceImprovementPlan
        fields = ['id', 'profile', 'employee_name', 'opened_from_checkin', 'reason',
                  'objectives', 'support_plan', 'start_date', 'review_date', 'end_date',
                  'status', 'outcome', 'opened_by', 'hr_acknowledged',
                  'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def monthly_checkins(request):
    off = _feature_off()
    if off:
        return off
    tier, qs = _perf_scope(request)
    if tier is None:
        return _perf_deny()

    if request.method == 'GET':
        return Response({'count': qs.count(), 'tier': tier,
                         'checkins': MonthlyCheckInSerializer(qs, many=True).data})

    # POST — create. An employee (self tier) may view + sign their own, never create.
    if tier == 'self':
        return Response({'detail': 'You can view and sign your own check-ins, but not create them.'},
                        status=status.HTTP_403_FORBIDDEN)
    ser = MonthlyCheckInSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    prof = ser.validated_data['profile']
    if tier == 'full':
        # Target must be within the caller's entity grant.
        in_scope = apply_company_scope(
            request, HRISProfile.objects.filter(pk=prof.pk), 'employee__company_id')
        if not in_scope.exists():
            return Response({'detail': 'That employee is outside your entity scope.'},
                            status=status.HTTP_403_FORBIDDEN)
    else:  # manager tier — may only record for a direct report
        if prof.pk not in set(_direct_report_profile_ids(request.user)):
            return Response({'detail': 'You can only record check-ins for your own direct reports.'},
                            status=status.HTTP_403_FORBIDDEN)
    capped = _overdue_rating_block(
        prof, ser.validated_data.get('overall_rating'),
        ser.validated_data.get('period_year'), ser.validated_data.get('period_month'))
    if capped is not None:
        return capped
    obj = ser.save(reviewer=request.user if request.user.is_authenticated else None)
    return Response(MonthlyCheckInSerializer(obj).data, status=status.HTTP_201_CREATED)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def monthly_checkin_detail(request, pk):
    off = _feature_off()
    if off:
        return off
    tier, qs = _perf_scope(request)
    if tier is None:
        return _perf_deny()
    obj = qs.filter(pk=pk).first()
    if obj is None:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    if request.method == 'GET':
        return Response(MonthlyCheckInSerializer(obj).data)
    # PATCH — blocked once signed-off (addendum-only).
    if obj.is_locked:
        return Response(
            {'detail': 'This check-in is signed-off and locked. Add an addendum instead.'},
            status=status.HTTP_409_CONFLICT)

    # Self tier: the employee may ONLY respond + sign their own side. The
    # signature time is stamped server-side (never trusted from the client) so
    # it cannot be forged or back-dated (CFO 2026-07-14 security fix).
    if tier == 'self':
        resp = request.data.get('employee_response')
        if resp is not None:
            obj.employee_response = str(resp)[:5000]
        # The employee's verdict on the feedback (CFO 2026-08-18): Accept /
        # Partially accept / Decline. On partial or decline they MUST reply to
        # the manager with a substantive reason — at least 50 words.
        decision = (request.data.get('employee_decision') or '').strip().lower()
        if decision:
            valid = {c.value for c in MonthlyCheckIn.EmployeeDecision}
            if decision not in valid:
                return Response({'detail': 'Choose Accept, Partially accept, or Do not accept.'},
                                status=status.HTTP_400_BAD_REQUEST)
            if decision in (MonthlyCheckIn.EmployeeDecision.PARTIAL,
                            MonthlyCheckIn.EmployeeDecision.DECLINE):
                words = len((obj.employee_response or '').split())
                if words < 50:
                    return Response(
                        {'detail': 'If you partially accept or do not accept, please explain to your '
                                   f'manager in at least 50 words why (you wrote {words}).'},
                        status=status.HTTP_400_BAD_REQUEST)
            obj.employee_decision = decision
        # CFO 2026-08-26: "the employee doesn't accept the feedback and rejects
        # and requests the manager to put additional comments so it's going to be
        # fair." Declining already existed; this is the half that makes the
        # manager ANSWER it. Without this the whole module had no caller at all
        # (Fable, 2026-08-26).
        if request.data.get('request_manager_comments'):
            from hris import feedback_pushback
            try:
                feedback_pushback.request_comments(
                    obj, reason=str(request.data.get('employee_response') or ''),
                    user=request.user)
            except DjangoValidationError as exc:
                msgs = getattr(exc, 'messages', None)
                return Response({'detail': msgs[0] if msgs else str(exc)},
                                status=status.HTTP_400_BAD_REQUEST)
            obj.refresh_from_db()
            return Response(MonthlyCheckInSerializer(obj).data)

        wants_sign = bool(request.data.get('employee_signed_at')
                          or request.data.get('sign')
                          or request.data.get('employee_ack'))
        if wants_sign:
            now = timezone.now()
            if not obj.employee_signed_at:
                obj.employee_signed_at = now
            obj.employee_ack = True
            if not obj.employee_ack_at:
                obj.employee_ack_at = now
            # The employee signing IS them closing their own loop. Without this
            # the valve wedged shut permanently and it was reachable from the
            # shipped UI: manager signs → employee asks for comments → employee
            # then signs → both signed → the record locks → the manager's answer
            # hits the 409 above and can NEVER land, so the employee waits
            # forever and the manager's task can never be satisfied
            # (Fable round 3, 2026-08-27).
            obj.employee_requested_comments = False
        obj.save()   # model.save() locks the record once both sides have signed
        # Tell the manager the employee has responded (best-effort — a mail
        # failure must never block the employee's own sign-off).
        if decision:
            try:
                from hris.performance_feedback_notify import notify_manager_of_response
                notify_manager_of_response(obj)
            except Exception:   # noqa: BLE001
                pass
        return Response(MonthlyCheckInSerializer(obj).data)

    # The manager answering the employee's request for comments. Only this
    # clears the flag — ignoring the email is not enough (CFO 2026-08-26).
    if request.data.get('manager_followup'):
        from hris import feedback_pushback
        try:
            feedback_pushback.answer_request(
                obj, comments=str(request.data.get('manager_followup') or ''),
                user=request.user)
        except DjangoValidationError as exc:
            msgs = getattr(exc, 'messages', None)
            return Response({'detail': msgs[0] if msgs else str(exc)},
                            status=status.HTTP_400_BAD_REQUEST)
        obj.refresh_from_db()
        return Response(MonthlyCheckInSerializer(obj).data)

    # Manager / HR: may edit the appraisal, but the employee-side fields are
    # read-only on the serializer, so this can never forge the employee sign-off.
    ser = MonthlyCheckInSerializer(obj, data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    capped = _overdue_rating_block(
        obj.profile, ser.validated_data.get('overall_rating'),
        obj.period_year, obj.period_month)
    if capped is not None:
        return capped
    ser.save()
    return Response(MonthlyCheckInSerializer(obj).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pips(request):
    off = _feature_off()
    if off:
        return off
    from core.hris_access import user_can_access_hris
    base = PerformanceImprovementPlan.objects.select_related('profile__employee__company')
    if user_can_access_hris(request.user):
        qs = apply_company_scope(request, base, 'profile__employee__company_id')
    else:
        report_ids = _direct_report_profile_ids(request.user)
        if not report_ids:
            return _perf_deny()
        qs = base.filter(profile_id__in=report_ids)
    return Response({'count': qs.count(), 'pips': PIPSerializer(qs, many=True).data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reportable_employees(request):
    """Employees the caller may record a check-in for — the perf page's picker.

    Keeps line managers off the whitelist-only /hris/api/employees/ endpoint:
      • full tier (exec / HR) → everyone in their entity scope,
      • manager tier          → their direct reports only,
      • else                  → 403.
    """
    off = _feature_off()
    if off:
        return off
    from core.hris_access import user_can_access_hris
    profs = HRISProfile.objects.select_related('employee', 'employee__company')
    if user_can_access_hris(request.user):
        profs = apply_company_scope(request, profs, 'employee__company_id')
    else:
        report_ids = _direct_report_profile_ids(request.user)
        if not report_ids:
            return _perf_deny()
        profs = profs.filter(pk__in=report_ids)
    data = [{
        'pid': str(p.pk),
        'nm': p.employee.full_name,
        'company': (p.employee.company.code if p.employee.company_id else 'Unassigned'),
    } for p in profs.order_by('employee__full_name')]
    return Response({'count': len(data), 'employees': data})
