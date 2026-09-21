"""
hris/exec_signoff_views.py — the in-app side of the long-overdue-task gate
(CFO 2026-08-07).

Three small endpoints:
  GET  /hris/api/exec-signoffs/            — the CEO/CFO's pending queue
  POST /hris/api/exec-signoffs/<pk>/decide/ — sign or decline in-app
  GET  /hris/api/my-overdue-tasks/          — my own late work, so the apply
                                              screens can warn me BEFORE I fill
                                              in the form rather than after.

The one-click email page (hris.exec_signoff_actions) is the same decision by a
different door; both go through hris.exec_signoff_service.decide.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris import exec_signoff_service as svc
from hris import overdue_gate
from hris.exec_signoff_models import ExecSignoff


def _row(so: ExecSignoff) -> dict:
    return {
        'id': str(so.id),
        'module': so.module,
        'module_label': so.get_module_display(),
        'object_id': str(so.object_id),
        'applicant': so.applicant_name,
        'reason': so.reason,
        'overdue_count': so.overdue_count,
        'overdue_tasks': (so.overdue_snapshot or {}).get('tasks', []),
        'status': so.status,
        'status_label': so.get_status_display(),
        'created_at': so.created_at.isoformat(),
        'decided_at': so.decided_at.isoformat() if so.decided_at else None,
        'decided_by': ((so.decided_by.get_full_name() or so.decided_by.username)
                       if so.decided_by_id else ''),
        'decision_notes': so.decision_notes,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def exec_signoff_list(request):
    """The CEO/CFO queue. Anyone else sees only their OWN raised records, so a
    person can check where their application is stuck without being able to
    read the whole company's."""
    mine_only = not svc.user_can_sign(request.user)
    qs = ExecSignoff.objects.select_related('applicant', 'decided_by')
    if mine_only:
        qs = qs.filter(applicant=request.user)
    state = (request.query_params.get('status') or 'pending').lower()
    if state in dict(ExecSignoff.Status.choices):
        qs = qs.filter(status=state)
    rows = [_row(so) for so in qs[:200]]
    return Response({'count': len(rows), 'can_sign': not mine_only, 'signoffs': rows})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def exec_signoff_decide(request, pk):
    """Body: {"decision": "approve"|"decline", "notes": "..."}"""
    so = ExecSignoff.objects.filter(pk=pk).first()
    if so is None:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    # Checked against THIS record — a CFO-only countersignature (discretionary
    # leave) must refuse the CEO, so the record has to be loaded first.
    if not svc.user_can_sign(request.user, so):
        return Response({'detail': ('Only the CFO can sign this off.' if so.cfo_only
                                    else 'Only the CEO or CFO can sign this off.')},
                        status=status.HTTP_403_FORBIDDEN)
    if not so.is_pending:
        return Response({'detail': 'This one has already been decided.'},
                        status=status.HTTP_409_CONFLICT)

    decision = (request.data.get('decision') or '').strip().lower()
    if decision not in ('approve', 'decline'):
        return Response({'detail': 'decision must be approve|decline.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        svc.decide(so, request.user, decision == 'approve',
                   (request.data.get('notes') or '').strip())
    except svc.SignoffRefused as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
    if so.status == ExecSignoff.Status.DECLINED:
        from hris.exec_signoff_actions import _refuse_underlying
        _refuse_underlying(so)
    return Response(_row(so))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_overdue_tasks(request):
    """My own long-overdue work, so the leave / loan / incentive forms can say
    up front that an executive signature will be needed."""
    return Response(overdue_gate.overdue_summary(request.user))
