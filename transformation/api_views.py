"""The Transformation Board API.

GET  /api/v1/transformation/board/        the whole board
GET  /api/v1/transformation/history/      the trend, for the animated line
POST /api/v1/transformation/initiatives/<code>/progress/   move a step

Reads are fast because the nightly pulse stores the computed board; ?live=1
recomputes on the spot for when the CFO wants to see a change land immediately.
"""
from __future__ import annotations

import datetime as _dt

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from transformation import assign as assign_svc
from transformation import pulse
from transformation.models import Initiative, InitiativeUpdate, PulseSnapshot
from transformation.permissions import CanViewTransformationBoard


@api_view(['GET'])
@permission_classes([CanViewTransformationBoard])
def board(request):
    """Today's board. Served from the stored snapshot unless ?live=1."""
    live = request.query_params.get('live') in ('1', 'true', 'yes')
    snapshot = PulseSnapshot.objects.order_by('-taken_for').first()

    if live or not snapshot:
        data = pulse.build_board()
        data['as_of'] = timezone.localdate().isoformat()
        data['source'] = 'computed now'
        data['ai'] = {
            'narrative': snapshot.narrative if snapshot else '',
            'source': snapshot.narrative_source if snapshot else '',
            'judge': snapshot.judge_verdict if snapshot else '',
            'judge_source': snapshot.judge_source if snapshot else '',
            'note': '' if snapshot else 'No pulse has run yet — the morning note '
                                        'appears after the first run.',
        }
    else:
        data = dict(snapshot.payload or {})
        data['as_of'] = snapshot.taken_for.isoformat()
        data['source'] = 'this morning’s pulse'
        stale = (timezone.localdate() - snapshot.taken_for).days
        if stale > 1:
            data['stale_days'] = stale

    return Response(data)


@api_view(['GET'])
@permission_classes([CanViewTransformationBoard])
def history(request):
    """The last 90 pulses — the line the screen animates."""
    rows = (PulseSnapshot.objects.order_by('-taken_for')
            .values('taken_for', 'overall_percent', 'days_remaining',
                    'staff_cost_now', 'staff_cost_target', 'headcount_now',
                    'headcount_target', 'cost_of_delay_bwp')[:90])
    return Response({'points': [{
        'date': r['taken_for'].isoformat(),
        'overall_percent': r['overall_percent'],
        'days_remaining': r['days_remaining'],
        'staff_cost_now': float(r['staff_cost_now']),
        'staff_cost_target': float(r['staff_cost_target']),
        'headcount_now': r['headcount_now'],
        'headcount_target': r['headcount_target'],
        'cost_of_delay': float(r['cost_of_delay_bwp']),
    } for r in reversed(list(rows))]})


@api_view(['POST'])
@permission_classes([CanViewTransformationBoard])
def set_progress(request, code: str):
    """Move a step. The board is only honest if it is easy to keep current."""
    item = Initiative.objects.filter(code=code.upper()).first()
    if not item:
        return Response({'detail': f'No step with code {code}.'},
                        status=status.HTTP_404_NOT_FOUND)

    percent = request.data.get('percent', None)
    new_status = (request.data.get('status') or '').strip()
    note = (request.data.get('note') or '').strip()[:300]
    blocked_on = request.data.get('blocked_on', None)

    if percent is not None:
        try:
            percent = int(percent)
        except (TypeError, ValueError):
            return Response({'detail': 'percent must be a whole number 0-100.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not 0 <= percent <= 100:
            return Response({'detail': 'percent must be between 0 and 100.'},
                            status=status.HTTP_400_BAD_REQUEST)
        item.percent = percent
        # 100% and "not done" cannot both be true.
        if percent >= 100 and not new_status:
            new_status = Initiative.Status.DONE

    if new_status:
        valid = {c for c, _ in Initiative.Status.choices}
        if new_status not in valid:
            return Response({'detail': f'status must be one of {sorted(valid)}.'},
                            status=status.HTTP_400_BAD_REQUEST)
        item.status = new_status
        if new_status == Initiative.Status.BLOCKED and not item.blocked_since:
            item.blocked_since = timezone.localdate()
        if new_status != Initiative.Status.BLOCKED:
            item.blocked_since = None

    if blocked_on is not None:
        item.blocked_on = str(blocked_on)[:160]

    item.save()

    actor = getattr(request.user, 'get_full_name', lambda: '')() or \
        getattr(request.user, 'username', '')
    InitiativeUpdate.objects.create(
        initiative=item, percent=item.percent, status=item.status,
        note=note, actor_name=actor)

    return Response({
        'code': item.code, 'percent': item.percent, 'status': item.status,
        'blocked_on': item.blocked_on,
    })


@api_view(['GET'])
@permission_classes([CanViewTransformationBoard])
def people(request):
    """Who can be put on a step — active Omni logins only."""
    return Response({'people': assign_svc.assignable_people()})


@api_view(['POST', 'DELETE'])
@permission_classes([CanViewTransformationBoard])
def assign(request, code: str):
    """Put a person on a step (POST) or take them off (DELETE).

    POST creates or updates their OmniTask so the deadline is real and the
    person is chased by Omni's existing reminders.
    """
    item = Initiative.objects.filter(code=code.upper()).first()
    if not item:
        return Response({'detail': f'No step with code {code}.'},
                        status=status.HTTP_404_NOT_FOUND)

    email = (request.data.get('email') or '').strip()
    if not email:
        return Response({'detail': 'email is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        removed = assign_svc.unassign(item, email)
        if not removed:
            return Response({'detail': f'{email} is not on {item.code}.'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({'code': item.code,
                         'assignments': assign_svc.assignments_for(item)})

    row, error = assign_svc.assign(
        item, email,
        due_date=request.data.get('due_date'),
        role=(request.data.get('role') or '').strip(),
        is_owner=bool(request.data.get('is_owner')),
        assigner=request.user,
        note=(request.data.get('note') or '').strip(),
    )
    if error:
        return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'code': item.code,
        'assigned': {'name': row.assignee_name, 'email': row.assignee_email,
                     'due_date': row.due_date.isoformat() if row.due_date else None,
                     'task_id': str(row.task_id) if row.task_id else None},
        'assignments': assign_svc.assignments_for(item),
    })
