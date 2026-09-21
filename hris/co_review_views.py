"""
hris/co_review_views.py — API for the joint 50/50 monthly rating (CFO 2026-07-26).

Only the two named people may rate: the employee's line manager and their
co_manager. Nobody else, HR included, can put a score in — a rating is a personal
judgement and must be attributable. HR/exec can READ the blend.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from hris.co_review_models import (AdditionalReviewer, CoRating, RaterRole,
                                   combined_score)
from hris.models import HRISProfile
from hris.manager_return_service import (additional_review_profiles, prev_period)
from hris.performance_views import _feature_off, _resolve_employee


def _role_for(me, profile) -> str | None:
    """Which rater slot this caller occupies for this employee, if any."""
    if me is None:
        return None
    if profile.manager_id == me.id:
        return RaterRole.LINE
    if profile.co_manager_id == me.id:
        return RaterRole.CO
    if AdditionalReviewer.objects.filter(profile=profile, reviewer_id=me.id).exists():
        return RaterRole.ADDITIONAL
    return None


def _period(request):
    src = request.query_params if request.method == 'GET' else (request.data or {})
    y, m = src.get('year'), src.get('month')
    if y and m:
        try:
            y, m = int(y), int(m)
            if 2020 <= y <= 2100 and 1 <= m <= 12:
                return y, m
        except (TypeError, ValueError):
            pass
    return prev_period()


def _row(profile, year, month, my_role, me_user_id=None):
    emp = profile.employee
    blend = combined_score(profile, year, month)
    # The caller's own additional entry (if they are an operations reviewer), so the
    # form can pre-fill their previous score/comment.
    my_add = None
    if my_role == RaterRole.ADDITIONAL and me_user_id is not None:
        my_add = next((a for a in blend.get('additional', [])
                       if a['rater_id'] == me_user_id), None)
    return {
        'profile_id': str(profile.id),
        'employee_id': str(emp.id),
        'name': emp.full_name,
        'job_title': emp.job_title or '',
        'line_manager': profile.manager.full_name if profile.manager else None,
        'co_reviewer': profile.co_manager.full_name if profile.co_manager else None,
        'my_role': my_role,
        'my_additional_score': my_add['score'] if my_add else None,
        'my_additional_comment': my_add['comment'] if my_add else '',
        # Only surface the OTHER rater's number once mine is in, so the second
        # person forms their own view instead of anchoring on the first score. An
        # additional (operations) reviewer sits outside the blend, so there is no
        # blend to anchor — they may see it as oversight.
        'other_visible': bool(
            (my_role == RaterRole.LINE and blend['line_score'] is not None) or
            (my_role == RaterRole.CO and blend['co_score'] is not None) or
            my_role == RaterRole.ADDITIONAL or
            my_role is None),
        **blend,
    }


def _redact(row):
    """Hide the other rater's score until this caller has entered their own."""
    if row['other_visible']:
        return row
    r = dict(row)
    if r['my_role'] == RaterRole.LINE:
        r['co_score'], r['co_comment'] = None, ''
    elif r['my_role'] == RaterRole.CO:
        r['line_score'], r['line_comment'] = None, ''
    r['combined'], r['spread'], r['disputed'] = None, None, False
    return r


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_ratings(request):
    """GET /hris/api/co-review/ — everyone I must rate this month, in two groups:
    my own direct reports, and the people I co-review."""
    off = _feature_off()
    if off:
        return off
    me = _resolve_employee(request.user)
    if me is None:
        return Response({'detail': 'Your login is not linked to an employee record.'},
                        status=403)
    year, month = _period(request)

    base = (HRISProfile.objects.exclude(employee=None)
            .select_related('employee', 'manager', 'co_manager'))
    from payroll.models import Employee
    base = base.exclude(employee__status=Employee.Status.TERMINATED)

    mine, shared = [], []
    for p in base.filter(manager=me).order_by('employee__full_name'):
        row = _redact(_row(p, year, month, RaterRole.LINE))
        # A person I line-manage AND who has a co-reviewer is a SHARED rating.
        (shared if p.co_manager_id else mine).append(row)
    for p in base.filter(co_manager=me).exclude(manager=me).order_by('employee__full_name'):
        shared.append(_redact(_row(p, year, month, RaterRole.CO)))

    # People I review as an operations reviewer only — never those I already line-
    # manage or co-review (they are shown above, in the blend).
    additional = []
    for p in (additional_review_profiles(me)
              .exclude(manager=me).exclude(co_manager=me).order_by('employee__full_name')):
        additional.append(_row(p, year, month, RaterRole.ADDITIONAL, me_user_id=request.user.id))

    return Response({'year': year, 'month': month,
                     'sole': mine, 'shared': shared, 'additional': additional,
                     'sole_count': len(mine), 'shared_count': len(shared),
                     'additional_count': len(additional)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def rate(request):
    """POST /hris/api/co-review/rate/ {profile_id, score, comment, year, month}"""
    off = _feature_off()
    if off:
        return off
    me = _resolve_employee(request.user)
    profile = (HRISProfile.objects.filter(pk=(request.data or {}).get('profile_id'))
               .select_related('employee', 'manager', 'co_manager').first())
    if profile is None:
        return Response({'detail': 'Employee not found.'}, status=404)

    role = _role_for(me, profile)
    if role is None:
        return Response({'detail': 'Only this person\'s manager, co-reviewer or an '
                                   'assigned additional reviewer can score them.'},
                        status=403)
    # Nobody scores themselves, whatever the org chart says.
    if me is not None and profile.employee_id == me.id:
        return Response({'detail': 'You cannot score yourself.'}, status=403)

    year, month = _period(request)
    raw = (request.data or {}).get('score')
    try:
        score = int(raw)
    except (TypeError, ValueError):
        return Response({'detail': 'Enter a score between 0 and 100.'}, status=400)

    with transaction.atomic():
        existing = CoRating.objects.select_for_update().filter(
            profile=profile, period_year=year, period_month=month, rater=request.user
        ).first()
        if existing and existing.is_locked:
            return Response({'detail': 'This rating is signed off and locked.'}, status=409)
        obj = existing or CoRating(
            profile=profile, period_year=year, period_month=month,
            rater=request.user, rater_role=role)
        obj.rater_role = role
        obj.score = score
        obj.comment = ((request.data or {}).get('comment') or '').strip()
        try:
            obj.clean()
        except ValidationError as e:
            return Response({'detail': 'Could not save.',
                             'errors': e.message_dict if hasattr(e, 'message_dict') else e.messages},
                            status=400)
        obj.save()

    profile.refresh_from_db()
    return Response(_row(profile, year, month, role, me_user_id=request.user.id))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def blend(request):
    """GET /hris/api/co-review/blend/?employee=<id> — the combined view.

    For the employee themselves, their line manager, their co-reviewer, or HR/exec.
    This is the CFO's bench view: who is actually good, per BOTH raters.
    """
    off = _feature_off()
    if off:
        return off
    from payroll.models import Employee
    me = _resolve_employee(request.user)
    emp_id = request.query_params.get('employee')
    profile = (HRISProfile.objects
               .filter(employee_id=emp_id if emp_id else getattr(me, 'id', None))
               .select_related('employee', 'manager', 'co_manager').first())
    if profile is None:
        return Response({'detail': 'Employee not found.'}, status=404)

    is_self = bool(me and profile.employee_id == me.id)
    is_rater = _role_for(me, profile) is not None
    from core.mixins import apply_company_scope
    hr_in_scope = (user_can_access_hris(request.user)
                   and apply_company_scope(
                       request, Employee.objects.filter(pk=profile.employee_id),
                       'company_id').exists())
    if not (is_self or is_rater or hr_in_scope):
        return Response({'detail': 'Not available for your role.'}, status=403)

    year, month = _period(request)
    row = _row(profile, year, month, _role_for(me, profile))
    # The subject sees the blend, never who scored what — that is the raters'.
    if is_self and not is_rater and not hr_in_scope:
        for k in ('line_score', 'co_score', 'line_comment', 'co_comment',
                  'spread', 'disputed'):
            row[k] = None if 'score' in k or k == 'spread' else ('' if 'comment' in k else False)
        # Additional (operations) reviewers' individual scores/comments are their
        # own — the subject must never see them (they carry rater_id + comment).
        row['additional'] = []
    return Response(row)
