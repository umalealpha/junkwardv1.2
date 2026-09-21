"""
hris/manager_return_views.py — API for the Monthly Manager Return (CFO 2026-07-26).

Access model, deliberately narrow (no new permission layer — reuses the perf
module's employee resolution and the HRIS access gate):
  * a people-manager sees and edits ONLY their own return;
  * their own manager sees it once submitted, and clears it;
  * HR / exec (user_can_access_hris) see the league table + any return, read-only
    unless they are the named reviewer.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from core.mixins import apply_company_scope
from hris.manager_return_models import (
    ManagerMonthlyReturn, ReturnStatus, CLEAR_BY_DAY, SUBMIT_BY_DAY,
)
from hris.manager_return_service import (
    additional_review_profiles, build_own_pack, build_team_pack, clear,
    co_review_profiles, get_or_create_draft, prev_period, resolve_reviewer,
    save_draft, send_back, submit, team_profiles,
)
from hris.performance_views import _feature_off, _resolve_employee

_BOOL_FIELDS = ('sales_target_met', 'work_finished_on_time',
                'dashboard_cleared_on_time', 'overstaffed', 'fy27_aligned')
_TEXT_FIELDS = ('leave_action', 'sla_explanation', 'work_on_time_comment',
                'tasks_comment', 'overstaffed_comment', 'fy27_actions',
                'innovation', 'prior_outcome', 'next_month_commitment')


def _coerce(payload: dict) -> dict:
    """Turn form JSON into model-typed values. Absent key = leave unchanged;
    explicit null on a tri-state bool = 'not answered yet' (a real state here,
    which is why these are BooleanField(null=True), not plain booleans)."""
    out = {}
    for f in _TEXT_FIELDS:
        if f in payload:
            out[f] = (payload[f] or '').strip()
    for f in _BOOL_FIELDS:
        if f in payload:
            v = payload[f]
            out[f] = None if v is None or v == '' else bool(v)
    if 'sla_breaches' in payload:
        v = payload['sla_breaches']
        if v is None or v == '':
            out['sla_breaches'] = None
        else:
            try:
                out['sla_breaches'] = max(0, int(v))
            except (TypeError, ValueError):
                raise ValidationError({'sla_breaches': 'Enter a whole number.'})
    if 'new_sales_amount' in payload:
        v = payload['new_sales_amount']
        if v is None or v == '':
            out['new_sales_amount'] = None
        else:
            try:
                out['new_sales_amount'] = Decimal(str(v))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError({'new_sales_amount': 'Enter an amount in pula, e.g. 125000.00'})
    return out


def _co_reviewed_rows(me, year: int, month: int) -> list[dict]:
    """People `me` co-reviews but does not line-manage. Listed separately and
    clearly labelled, so a shared rating is never mistaken for line management."""
    from hris.co_review_models import combined_score
    out = []
    for p in co_review_profiles(me):
        blend = combined_score(p, year, month)
        out.append({
            'employee_id': str(p.employee.id),
            'profile_id': str(p.id),
            'name': p.employee.full_name,
            'job_title': p.employee.job_title or '',
            'line_manager': p.manager.full_name if p.manager else None,
            'my_share': int(p.co_manager_weight or 0),
            'line_share': 100 - int(p.co_manager_weight or 0),
            **{k: v for k, v in blend.items()
               if k in ('line_score', 'co_score', 'combined', 'complete',
                        'waiting_on', 'spread', 'disputed')},
        })
    return out


def _additional_reviewed_rows(me, year: int, month: int) -> list[dict]:
    """People `me` reviews as an operations reviewer only — not line-managed, not in
    the blend. They enter their own advisory score/comment (CFO 2026-08-12)."""
    from hris.co_review_models import RaterRole, combined_score
    out = []
    for p in additional_review_profiles(me):
        blend = combined_score(p, year, month)
        my = next((a for a in blend.get('additional', [])
                   if a['rater_id'] == me.user_id), None) if getattr(me, 'user_id', None) else None
        out.append({
            'employee_id': str(p.employee.id),
            'profile_id': str(p.id),
            'name': p.employee.full_name,
            'job_title': p.employee.job_title or '',
            'line_manager': p.manager.full_name if p.manager else None,
            'my_role': RaterRole.ADDITIONAL,
            'my_additional_score': my['score'] if my else None,
            'my_additional_comment': my['comment'] if my else '',
            'combined': blend.get('combined'),
            'complete': blend.get('complete'),
        })
    return out


def _shown_keys(ret: ManagerMonthlyReturn) -> set[str]:
    """Question keys actually shown to this manager — the write whitelist for
    dept_answers. Anything else in the payload is ignored, not stored."""
    return {k for k, _label in ret._shown_extra_questions()}


def _serialize(ret: ManagerMonthlyReturn, live_pack=None, live_own=None) -> dict:
    """A submitted return shows its FROZEN snapshot; a draft shows live figures
    so the manager sees today's truth while filling it in."""
    frozen = ret.status in (ReturnStatus.SUBMITTED, ReturnStatus.CLEARED)
    rows = ret.team_snapshot if frozen else ((live_pack or {}).get('rows') or [])
    own = ret.own_snapshot if frozen else (live_own or {})
    hc_equiv = (float(ret.headcount_equivalent) if ret.headcount_equivalent is not None
                else (live_pack or {}).get('headcount_equivalent'))
    return {
        'id': str(ret.id),
        'manager': ret.manager.full_name,
        'period_year': ret.period_year,
        'period_month': ret.period_month,
        'status': ret.status,
        'status_label': ret.get_status_display(),
        'is_locked': ret.is_locked,
        'frozen': frozen,
        'submit_due': ret.submit_due.isoformat(),
        'clear_due': ret.clear_due.isoformat(),
        'submit_by_day': SUBMIT_BY_DAY,
        'clear_by_day': CLEAR_BY_DAY,
        # facts
        'team': rows,
        'own': own,
        'headcount': len(rows),
        'headcount_equivalent': hc_equiv,
        'offenders': ((live_pack or {}).get('offenders')
                      if not frozen else
                      [r['name'] for r in rows
                       if r.get('unexplained_days') or r.get('unanswered_days')]),
        # answers
        'leave_action': ret.leave_action,
        'sla_breaches': ret.sla_breaches,
        'sla_explanation': ret.sla_explanation,
        'sales_target_met': ret.sales_target_met,
        'new_sales_amount': (str(ret.new_sales_amount)
                             if ret.new_sales_amount is not None else None),
        'work_finished_on_time': ret.work_finished_on_time,
        'work_on_time_comment': ret.work_on_time_comment,
        'dashboard_cleared_on_time': ret.dashboard_cleared_on_time,
        'tasks_comment': ret.tasks_comment,
        'overstaffed': ret.overstaffed,
        'overstaffed_comment': ret.overstaffed_comment,
        'fy27_aligned': ret.fy27_aligned,
        'fy27_actions': ret.fy27_actions,
        'innovation': ret.innovation,
        # role-specific half
        'question_spec': ret.question_spec or {},
        'dept_answers': ret.dept_answers or {},
        'prior_commitments': ret.prior_commitments,
        'prior_outcome': ret.prior_outcome,
        'next_month_commitment': ret.next_month_commitment,
        # review
        'submitted_at': ret.submitted_at,
        'submitted_to': ret.submitted_to.full_name if ret.submitted_to else None,
        'reviewer_verdict': ret.reviewer_verdict,
        'reviewer_notes': ret.reviewer_notes,
        'cleared_at': ret.cleared_at,
        'missing': ret.missing_for_submit(),
    }


def _period(request):
    y = request.query_params.get('year') or request.data.get('year') if hasattr(request, 'data') else None
    m = request.query_params.get('month') or (request.data.get('month') if hasattr(request, 'data') else None)
    if y and m:
        try:
            y, m = int(y), int(m)
            if 2020 <= y <= 2100 and 1 <= m <= 12:
                return y, m
        except (TypeError, ValueError):
            pass
    return prev_period()


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def my_return(request):
    """GET  /hris/api/manager-return/       — my return for the period (creates a draft)
    POST /hris/api/manager-return/        — save my answers  {..., submit: true}
    """
    off = _feature_off()
    if off:
        return off
    me = _resolve_employee(request.user)
    if me is None:
        return Response({'detail': 'Your login is not linked to an employee record.'}, status=403)
    # A co-reviewer OR operations reviewer with no line reports of their own must
    # still reach the page to enter their rating — otherwise it never arrives.
    if not team_profiles(me, raiser_user=request.user).exists():
        has_co = co_review_profiles(me).exists()
        has_add = additional_review_profiles(me).exists()
        if not has_co and not has_add:
            return Response({'detail': 'You have no direct reports, so there is no return to file.',
                             'no_team': True}, status=200)
        return Response({'detail': 'You have no direct reports, but you review other people.',
                         'no_team': True, 'co_review_only': True,
                         'co_reviewed': _co_reviewed_rows(me, *_period(request)),
                         'additional_reviewed': _additional_reviewed_rows(me, *_period(request))},
                        status=200)

    year, month = _period(request)
    ret = get_or_create_draft(me, year, month)

    if request.method == 'POST':
        payload = request.data or {}
        try:
            save_draft(ret, _coerce(payload),
                       extra_answers=payload.get('dept_answers') or {},
                       allowed_extra_keys=_shown_keys(ret))
            if payload.get('submit'):
                submit(ret)
        except ValidationError as e:
            return Response({'detail': 'Could not save.',
                             'errors': e.message_dict if hasattr(e, 'message_dict') else e.messages},
                            status=400)
        ret.refresh_from_db()

    frozen = ret.status in (ReturnStatus.SUBMITTED, ReturnStatus.CLEARED)
    pack = None if frozen else build_team_pack(me, year, month, raiser_user=request.user)
    own = None if frozen else build_own_pack(me, year, month)
    payload = _serialize(ret, pack, own)
    payload['co_reviewed'] = _co_reviewed_rows(me, year, month)
    payload['additional_reviewed'] = _additional_reviewed_rows(me, year, month)
    return Response(payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def review_queue(request):
    """GET /hris/api/manager-return/review/ — returns waiting on ME to clear."""
    off = _feature_off()
    if off:
        return off
    me = _resolve_employee(request.user)
    if me is None:
        return Response({'returns': []})
    qs = (ManagerMonthlyReturn.objects
          .filter(submitted_to=me, status=ReturnStatus.SUBMITTED)
          .select_related('manager')
          .order_by('period_year', 'period_month', 'manager__full_name'))
    return Response({'returns': [_serialize(r) for r in qs]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def review_decide(request, pk):
    """POST /hris/api/manager-return/<id>/decide/  {action: clear|send_back,
    verdict, notes}"""
    off = _feature_off()
    if off:
        return off
    ret = ManagerMonthlyReturn.objects.filter(pk=pk).select_related('manager').first()
    if ret is None:
        return Response({'detail': 'Return not found.'}, status=404)
    me = _resolve_employee(request.user)
    # The reporting line IS the authority, and it is deliberately NOT company-
    # clamped: Alpha Direct has real cross-entity lines (the CFO sits in ADIC and
    # manages Tshephang in Veritas; Charmaine is RSA and reports to Keetile in
    # ADIC). Clamping this by company would lock a legitimate manager out of their
    # own report's return. `submitted_to == me` is already a tight per-record gate.
    is_reviewer = bool(me and ret.submitted_to_id == me.id)
    # The HR/exec FALLBACK is different — it is a role, not a named relationship,
    # so it MUST be entity-clamped or an HR user pinned to one company could clear
    # another entity's return (DeepSeek round 2, CRITICAL).
    is_fallback = bool(
        ret.submitted_to_id is None
        and user_can_access_hris(request.user)
        and apply_company_scope(
            request,
            type(ret.manager).objects.filter(pk=ret.manager_id),
            'company_id').exists())
    if not (is_reviewer or is_fallback):
        return Response({'detail': 'Only this person\'s manager can clear their return.'},
                        status=403)

    action = (request.data or {}).get('action') or 'clear'
    notes = (request.data or {}).get('notes') or ''
    try:
        if action == 'send_back':
            send_back(ret, request.user, notes)
        else:
            clear(ret, request.user, (request.data or {}).get('verdict') or '', notes)
    except ValidationError as e:
        return Response({'detail': 'Could not save.',
                         'errors': e.message_dict if hasattr(e, 'message_dict') else e.messages},
                        status=400)
    ret.refresh_from_db()
    return Response(_serialize(ret))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def dialogue_rollup(request):
    """GET /hris/api/manager-return/rollup/?employee=<id>&year=<y>

    The year's monthly returns for one manager, condensed — this is how the
    monthly returns FEED the Development Dialogue (CFO 2026-07-26). Twelve short
    monthly returns become the evidence base for the big annual conversation, so
    the DD is not written from memory.

    Visible to: the person themselves, their manager, and HR/exec.
    """
    off = _feature_off()
    if off:
        return off
    from payroll.models import Employee
    from hris.models import HRISProfile

    me = _resolve_employee(request.user)
    emp_id = request.query_params.get('employee')
    if emp_id:
        target = Employee.objects.filter(pk=emp_id).first()
    else:
        target = me
    if target is None:
        return Response({'detail': 'Employee not found.'}, status=404)

    is_self = bool(me and me.id == target.id)
    prof = HRISProfile.objects.filter(employee=target).first()
    is_their_manager = bool(me and prof and prof.manager_id == me.id)
    # HR/exec reach is entity-clamped: an HR user pinned to one company cannot
    # pull another entity's employee by guessing an id (same invariant as league).
    hr_in_scope = (user_can_access_hris(request.user)
                   and apply_company_scope(
                       request, Employee.objects.filter(pk=target.pk), 'company_id').exists())
    if not (is_self or is_their_manager or hr_in_scope):
        return Response({'detail': 'Not available for your role.'}, status=403)

    try:
        year = int(request.query_params.get('year') or prev_period()[0])
    except (TypeError, ValueError):
        year = prev_period()[0]

    qs = (ManagerMonthlyReturn.objects
          .filter(manager=target, period_year=year)
          .order_by('period_month'))
    months, filed, on_time, verdicts = [], 0, 0, {}
    for r in qs:
        if r.submitted_at:
            filed += 1
            if r.submitted_at.date() <= r.submit_due:
                on_time += 1
        if r.reviewer_verdict:
            verdicts[r.reviewer_verdict] = verdicts.get(r.reviewer_verdict, 0) + 1
        months.append({
            'month': r.period_month,
            'status': r.status,
            'filed_on_time': (r.submitted_at.date() <= r.submit_due
                              if r.submitted_at else None),
            'verdict': r.reviewer_verdict,
            'headcount_equivalent': (float(r.headcount_equivalent)
                                     if r.headcount_equivalent is not None else None),
            'innovation': r.innovation,
            'commitment': r.next_month_commitment,
            'commitment_outcome': r.prior_outcome,
            'overstaffed': r.overstaffed,
            'reviewer_notes': r.reviewer_notes,
        })
    return Response({
        'employee': target.full_name,
        'year': year,
        'months': months,
        'filed': filed,
        'filed_on_time': on_time,
        'verdicts': verdicts,
        # The one-line summary the Development Dialogue can quote directly.
        'summary': (f'Filed {filed} of 12 monthly returns, {on_time} on time.'
                    + (f' Verdicts: '
                       + ', '.join(f'{k.replace("_", " ")} x{v}' for k, v in verdicts.items())
                       if verdicts else '')),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def league(request):
    """GET /hris/api/manager-return/league/ — who filed on time, who did not.
    The 10th deadline means nothing unless late filers are visible (CFO)."""
    off = _feature_off()
    if off:
        return off
    if not user_can_access_hris(request.user):
        return Response({'detail': 'Not available for your role.'}, status=403)

    year, month = _period(request)
    from hris.models import HRISProfile
    from payroll.models import Employee

    # Entity isolation (omni's standing invariant, and what the sibling
    # perf_monthly_views does): an HR user pinned to one company must NOT see
    # another entity's managers. Clamp BOTH the manager list and the returns.
    scoped_profiles = apply_company_scope(
        request, HRISProfile.objects.exclude(manager=None), 'employee__company_id')
    mgr_ids = scoped_profiles.values_list('manager', flat=True).distinct()
    managers = apply_company_scope(
        request,
        Employee.objects.filter(pk__in=list(mgr_ids))
        .exclude(status=Employee.Status.TERMINATED),
        'company_id').order_by('full_name')
    rows = []
    for mgr in managers:
        ret = ManagerMonthlyReturn.objects.filter(
            manager=mgr, period_year=year, period_month=month).first()
        n_reports = team_profiles(mgr).count()
        filed_on_time = None
        if ret and ret.submitted_at:
            filed_on_time = ret.submitted_at.date() <= ret.submit_due
        rows.append({
            'manager': mgr.full_name,
            'reports': n_reports,
            'status': ret.status if ret else 'not_started',
            'submitted_at': ret.submitted_at if ret else None,
            'filed_on_time': filed_on_time,
            'verdict': ret.reviewer_verdict if ret else '',
            'headcount_equivalent': (float(ret.headcount_equivalent)
                                     if ret and ret.headcount_equivalent is not None else None),
        })
    return Response({'year': year, 'month': month, 'managers': rows,
                     'filed': sum(1 for r in rows if r['status'] in
                                  (ReturnStatus.SUBMITTED, ReturnStatus.CLEARED)),
                     'total': len(rows)})
