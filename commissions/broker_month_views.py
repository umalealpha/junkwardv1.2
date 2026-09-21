"""commissions/broker_month_views.py — C4 status, C5 close month, C7 compliance.

Every endpoint here answers to the Broker Commission - Full Access role
(CanUseBrokerModule, board item [C8]) and nobody else — server-enforced. The
CFO decided 19-Sep-2026 that "Close month" is a Full Access button, and C7 says
the same of the Compliance field.
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from commissions import broker_status
from commissions.broker_views import CanUseBrokerModule, _clean_period
from commissions.models import Broker, BrokerCompliance


def _refusal(exc: broker_status.StatusRefused) -> Response:
    # 503 = we could not look; 409 = we looked and will not state an answer.
    return Response({'refused': True, 'detail': exc.reason, 'feed': exc.feed},
                    status=503 if exc.unreachable else 409)


def _period(raw, default_now: bool = True):
    period = _clean_period(raw)
    if period == '' and default_now:
        period = timezone.localdate().strftime('%Y-%m')
    return period or None


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_status_view(request, pk):
    """C4 — ?period=YYYY-MM (default this Gaborone month)."""
    try:
        broker = Broker.objects.get(pk=pk)
    except Broker.DoesNotExist:
        return Response({'detail': 'No such broker.'}, status=404)
    period = _period(request.query_params.get('period'))
    if period is None:
        return Response({'detail': 'Month must look like 2026-08.'}, status=400)
    try:
        return Response(broker_status.status_by_broker(broker, period))
    except broker_status.StatusRefused as exc:
        return _refusal(exc)


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_close_month(request):
    """C5 — Finance closes a finished month. Idempotent."""
    period = _period(request.data.get('period'), default_now=False)
    if period is None:
        return Response({'detail': 'Say which month to close, e.g. 2026-08.'}, status=400)
    try:
        return Response(broker_status.close_month(period, user=request.user))
    except broker_status.StatusRefused as exc:
        return _refusal(exc)


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanUseBrokerModule])
def broker_set_compliance(request, pk):
    """C7 — set the manual Compliance field for one broker and month."""
    try:
        broker = Broker.objects.get(pk=pk)
    except Broker.DoesNotExist:
        return Response({'detail': 'No such broker.'}, status=404)
    period = _period(request.data.get('period'), default_now=False)
    if period is None:
        return Response({'detail': 'Month must look like 2026-08.'}, status=400)
    text = str(request.data.get('compliance') or '').strip()
    if len(text) > 200:
        return Response({'detail': 'Keep Compliance to 200 characters.'}, status=400)
    obj, _ = BrokerCompliance.objects.update_or_create(
        broker=broker, period=period,
        defaults={'compliance': text, 'updated_by': request.user})
    return Response({'broker_id': str(broker.id), 'period': obj.period,
                     'compliance': obj.compliance})
