"""
payroll/backfill_views.py — the two endpoints behind the Finance "release an
earlier month" screen.

  GET  /api/v1/payroll/backlog/?period=2026-06&kind=incentive
  POST /api/v1/payroll/backlog/release/   {period, kind, employee_ids: [...]}

Both are gated on the SAME rule that already decides who may apply a payroll
amendment batch (`payroll.amendment_views._can_apply_batch`): superuser, the
Financial Controller or Finance Manager by title, or a CFO-named applier. A new
permission was not invented for this — releasing a backlog line into a batch is
the same act of authority as applying one, and a second, subtly different
allowlist is how two gates drift apart.

The preview is read-only. The release is the only write, and it is refused
outright for any line that has already been through payroll or was settled by
hand — see `payroll.backfill_service`.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from payroll import backfill_service
from payroll.amendment_views import _can_apply_batch

REFUSED = ('Only Finance may open or release the payroll backlog — the '
           'Financial Controller, the Finance Manager, or someone the CFO has '
           'named as a payroll-batch applier.')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def backlog_preview(request):
    if not _can_apply_batch(request.user):
        return Response({'detail': REFUSED}, status=403)

    period = (request.query_params.get('period') or '').strip()
    kind = (request.query_params.get('kind') or '').strip().lower()
    if not period:
        return Response({'detail': 'Say which month to look at, e.g. 2026-06.'},
                        status=400)
    try:
        return Response(backfill_service.preview(source_period=period, kind=kind))
    except backfill_service.ReleaseRefused as refused:
        return Response({'detail': str(refused)}, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def backlog_release(request):
    if not _can_apply_batch(request.user):
        return Response({'detail': REFUSED}, status=403)

    body = request.data or {}
    period = (body.get('period') or '').strip()
    kind = (body.get('kind') or '').strip().lower()
    employee_ids = body.get('employee_ids') or []
    if not period:
        return Response({'detail': 'Say which month is being released.'}, status=400)
    if not isinstance(employee_ids, list):
        return Response({'detail': 'employee_ids must be a list.'}, status=400)

    try:
        out = backfill_service.release(source_period=period, kind=kind,
                                       employee_ids=employee_ids,
                                       user=request.user)
    except backfill_service.ReleaseRefused as refused:
        return Response({'detail': str(refused)}, status=400)
    return Response(out)
