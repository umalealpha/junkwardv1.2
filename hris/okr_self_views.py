"""
hris/okr_self_views.py — "my objectives", for every employee.

Unami, 30 Jul 2026: *"The objectives can they appear when the employee logs in
with an update"* and *"Team should add their sub-objectives (how will they
achieve)"*.

Why this file exists at all: the OKR tree (`hris/okr_tree_views.py`) sits behind
`_deny_if_not_whitelisted` — the HRIS whitelist plus the HRIS password unlock. That
is correct for a view that shows the whole company's goals, but it means an
ordinary employee could never see their own objectives, let alone update them. So
the self-service surface is separate, gated on the `view_own_assessment` capability
that already governs "my dialogue", and every query here is clamped to the caller:

  * they see COMPANY objectives, their own DEPARTMENT's objectives, and their own
    individual ones — never a colleague's individual goals,
  * they may add an INITIATIVE ("how will they achieve") under an objective they
    can see, owned by themselves,
  * they may check in against a Key Result they own,
  * nothing here can touch `score_h1` / `score_h2` — the appraisal scores that feed
    the 9-box grid stay HR's, so self-reported progress can never inflate a rating.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.models import HRISProfile, OKR
from hris.okr_models import KeyResult, OKRCheckIn

MAX_NAME = 250
MAX_NOTE = 4000


def _gate(request):
    """Self-service gate — the same capability that guards "my dialogue"."""
    from hris.talent_cockpit_views import _gate as cockpit_gate
    return cockpit_gate(request, capability='view_own_assessment')


def _me(user):
    """The caller's HRIS profile, by employee link then by email."""
    emp = getattr(user, 'employee_record', None)
    if emp is not None:
        prof = HRISProfile.objects.filter(employee=emp).select_related('employee').first()
        if prof:
            return prof
    email = (getattr(user, 'email', '') or '').strip().lower()
    if not email:
        return None
    return (HRISProfile.objects
            .filter(employee__email__iexact=email)
            .select_related('employee').first())


def _my_department(prof) -> str:
    return ((getattr(getattr(prof, 'employee', None), 'department', '') or '').strip())


def _visible_objectives(prof, period: str = ''):
    """Company + my department + my own individual objectives. Nothing else.

    Department matching is on the department NAME because OKR.scope='department'
    rows carry no profile (they belong to no single person) — the name is the only
    link available. Blank department on either side matches nothing, so a person
    with no department set does not accidentally inherit another team's goals.
    """
    qs = OKR.objects.select_related('profile__employee').prefetch_related('key_results')
    if period:
        qs = qs.filter(period=period)
    dept = _my_department(prof)
    mine = qs.filter(profile=prof, scope=OKR.Scope.INDIVIDUAL)
    company = qs.filter(scope=OKR.Scope.COMPANY)
    if dept:
        # EXACT team match only. A substring match ("IT" inside "quality",
        # "claims" inside any objective mentioning claims) showed an employee most
        # of the company's departmental goals (Fable, 2026-07-31).
        dept_q = qs.filter(scope=OKR.Scope.DEPARTMENT)
        dept_ids = [o.pk for o in dept_q
                    if _dept_of_objective(o).strip().lower() == dept.strip().lower()]
        dept_rows = qs.filter(pk__in=dept_ids)
    else:
        dept_rows = qs.none()
    return (company | dept_rows | mine).distinct()


def _dept_of_objective(okr) -> str:
    """A department objective's team name.

    Department objectives have no profile, so the team is recorded in
    `OKR.target` today (the create endpoint puts the free-text team there when the
    scope is 'department'). Falls back to the objective name so an older row still
    matches something rather than disappearing from every employee's view.
    """
    return ((okr.target or '') or (okr.name or '')).strip()


def _kr_dict(kr, me_id=None):
    return {
        'id': str(kr.pk),
        'kind': kr.kind,
        'name': kr.name,
        'description': kr.description,
        'direction': kr.direction,
        'unit': kr.unit,
        'start_value': None if kr.start_value is None else float(kr.start_value),
        'target_value': None if kr.target_value is None else float(kr.target_value),
        'current_value': None if kr.current_value is None else float(kr.current_value),
        'attainment_pct': kr.attainment_pct,
        'is_done': kr.is_done,
        'due_date': kr.due_date.isoformat() if kr.due_date else None,
        'owner': (getattr(getattr(kr.owner, 'employee', None), 'full_name', '') or ''),
        'is_mine': bool(me_id and kr.owner_id == me_id),
        'weight_pct': float(kr.weight_pct or 0),
    }


def _objective_dict(okr, me_id=None):
    krs = list(okr.key_results.all())
    measured = [k.attainment_pct for k in krs if k.attainment_pct is not None]
    return {
        'id': str(okr.pk),
        'name': okr.name,
        'description': okr.description,
        'scope': okr.scope,
        'period': okr.period,
        'target': okr.target,
        'weight_pct': float(okr.weight_pct or 0),
        'owner': (getattr(getattr(okr.profile, 'employee', None), 'full_name', '')
                  or ('Company' if okr.scope == OKR.Scope.COMPANY else 'Department')),
        'is_mine': bool(me_id and okr.profile_id == me_id),
        # Progress from CHECK-INS only. The appraisal scores (score_h1/h2) are
        # deliberately not mixed in here — see the module docstring.
        'progress_pct': (round(sum(measured) / len(measured), 1) if measured else None),
        'key_results': [_kr_dict(k, me_id) for k in krs if k.kind == KeyResult.Kind.KEY_RESULT],
        'initiatives': [_kr_dict(k, me_id) for k in krs if k.kind == KeyResult.Kind.INITIATIVE],
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_okrs(request):
    """GET /hris/api/okr/mine/ — what this employee should see on login."""
    denied = _gate(request)
    if denied is not None:
        return denied
    prof = _me(request.user)
    if prof is None:
        return Response({'objectives': [], 'department': '', 'periods': [],
                         'detail': 'No employee record is linked to your account yet.'})
    period = (request.query_params.get('period') or '').strip()
    all_visible = _visible_objectives(prof)
    periods = sorted({o.period for o in all_visible if o.period}, reverse=True)
    if not period and periods:
        period = periods[0]
    rows = [o for o in all_visible if not period or o.period == period]
    order = {OKR.Scope.COMPANY: 0, OKR.Scope.DEPARTMENT: 1, OKR.Scope.INDIVIDUAL: 2}
    rows.sort(key=lambda o: (order.get(o.scope, 9), o.name.lower()))
    return Response({
        'department': _my_department(prof),
        'period': period,
        'periods': periods,
        'objectives': [_objective_dict(o, prof.pk) for o in rows],
    })


def _num_or_none(raw, field):
    if raw in (None, ''):
        return None, None
    try:
        return Decimal(str(raw)), None
    except (InvalidOperation, ValueError):
        return None, Response({'detail': f'{field} must be a number.'},
                              status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_my_sub_objective(request):
    """POST /hris/api/okr/mine/sub-objective/ — "how will I achieve it".

    The employee's own INITIATIVE under an objective they can already see. Owned by
    them, always: an employee cannot create work for a colleague from here.
    """
    denied = _gate(request)
    if denied is not None:
        return denied
    prof = _me(request.user)
    if prof is None:
        return Response({'detail': 'No employee record is linked to your account yet.'},
                        status=status.HTTP_400_BAD_REQUEST)
    data = request.data or {}
    name = str(data.get('name') or '').strip()
    if not name:
        return Response({'detail': 'name is required.'}, status=status.HTTP_400_BAD_REQUEST)
    if len(name) > MAX_NAME:
        return Response({'detail': f'name must be at most {MAX_NAME} characters.'},
                        status=status.HTTP_400_BAD_REQUEST)

    objective_id = data.get('objective_id')
    visible = {str(o.pk): o for o in _visible_objectives(prof)}
    objective = visible.get(str(objective_id))
    if objective is None:
        return Response({'detail': 'Objective not found, or not one of yours.'},
                        status=status.HTTP_404_NOT_FOUND)

    # Self-service adds INITIATIVES only — "what will I do to get there". Letting
    # an employee add a KEY RESULT here let them mint a 100%-attained measure under
    # a COMPANY objective and move its progress for the whole business
    # (Fable, 2026-07-31). Key Results stay with whoever owns the objective.
    kind = str(data.get('kind') or KeyResult.Kind.INITIATIVE).strip().lower()
    if kind != KeyResult.Kind.INITIATIVE:
        return Response({'detail': 'You can add an initiative (how you will achieve it). '
                                   'Key Results are set by the objective owner.'},
                        status=status.HTTP_400_BAD_REQUEST)
    direction = str(data.get('direction') or KeyResult.Direction.INCREASE).strip().lower()
    if direction not in dict(KeyResult.Direction.choices):
        return Response({'detail': 'direction must be increase, decrease or done.'},
                        status=status.HTTP_400_BAD_REQUEST)

    start, err = _num_or_none(data.get('start_value'), 'start_value')
    if err is not None:
        return err
    target, err = _num_or_none(data.get('target_value'), 'target_value')
    if err is not None:
        return err

    kr = KeyResult.objects.create(
        objective=objective, kind=kind, name=name,
        description=str(data.get('description') or '')[:MAX_NOTE],
        direction=direction, unit=str(data.get('unit') or '')[:30],
        start_value=start, target_value=target, current_value=start,
        owner=prof)
    return Response({'key_result': _kr_dict(kr, prof.pk)}, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def check_in(request):
    """POST /hris/api/okr/mine/check-in/ — progress on a Key Result I own.

    Appends to the history AND moves `current_value` so the objective's rollup
    reflects it immediately. Only the owner may check in: progress is a first-person
    statement, and letting anyone update anyone's number destroys its meaning.
    """
    denied = _gate(request)
    if denied is not None:
        return denied
    prof = _me(request.user)
    if prof is None:
        return Response({'detail': 'No employee record is linked to your account yet.'},
                        status=status.HTTP_400_BAD_REQUEST)
    data = request.data or {}
    kr = (KeyResult.objects
          .filter(pk=str(data.get('key_result_id') or '').strip() or None)
          .select_related('objective').first())
    if kr is None:
        return Response({'detail': 'Key result not found.'}, status=status.HTTP_404_NOT_FOUND)
    if kr.owner_id != prof.pk:
        return Response({'detail': 'You can only check in on your own key results.'},
                        status=status.HTTP_403_FORBIDDEN)

    confidence = str(data.get('confidence') or OKRCheckIn.Confidence.ON_TRACK).strip().lower()
    if confidence not in dict(OKRCheckIn.Confidence.choices):
        return Response({'detail': 'confidence must be on_track, at_risk or off.'},
                        status=status.HTTP_400_BAD_REQUEST)
    value, err = _num_or_none(data.get('value'), 'value')
    if err is not None:
        return err
    note = str(data.get('note') or '')[:MAX_NOTE]
    done = data.get('is_done')

    ci = OKRCheckIn.objects.create(
        key_result=kr, author=prof,
        author_email=(getattr(request.user, 'email', '') or '')[:200],
        value=value, confidence=confidence, note=note)
    changed = []
    if value is not None:
        kr.current_value = value
        changed.append('current_value')
    if isinstance(done, bool):
        kr.is_done = done
        changed.append('is_done')
    if changed:
        kr.save(update_fields=changed + ['updated_at'] if hasattr(kr, 'updated_at') else changed)
    return Response({'check_in': {'id': str(ci.pk), 'confidence': ci.confidence,
                                  'value': None if ci.value is None else float(ci.value),
                                  'note': ci.note},
                     'key_result': _kr_dict(kr, prof.pk)},
                    status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def key_result_history(request, kr_id):
    """GET /hris/api/okr/mine/history/<kr_id>/ — check-in history for one KR.

    Visible if the Key Result hangs off an objective the caller can see, so a team
    member can read the trail on their department's goals, not only their own.
    """
    denied = _gate(request)
    if denied is not None:
        return denied
    prof = _me(request.user)
    if prof is None:
        return Response({'check_ins': []})
    kr = KeyResult.objects.filter(pk=kr_id).select_related('objective').first()
    if kr is None:
        return Response({'detail': 'Key result not found.'}, status=status.HTTP_404_NOT_FOUND)
    visible_ids = {str(o.pk) for o in _visible_objectives(prof)}
    if str(kr.objective_id) not in visible_ids:
        return Response({'detail': 'Not one of yours.'}, status=status.HTTP_403_FORBIDDEN)
    rows = [{
        'id': str(c.pk),
        'at': c.created_at.isoformat() if c.created_at else None,
        'by': (getattr(getattr(c.author, 'employee', None), 'full_name', '')
               or c.author_email or '—'),
        'value': None if c.value is None else float(c.value),
        'confidence': c.confidence,
        'note': c.note,
    } for c in kr.check_ins.all()[:100]]
    return Response({'key_result': _kr_dict(kr, prof.pk), 'check_ins': rows})
