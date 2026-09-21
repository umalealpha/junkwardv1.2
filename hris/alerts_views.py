"""
hris/alerts_views.py — HRISAlert API.

  GET  /api/v1/hris/alerts/                              list open alerts
  GET  /api/v1/hris/alerts/?state=acknowledged&kind=…    filter
  POST /api/v1/hris/alerts/<id>/acknowledge/             acknowledge
  POST /api/v1/hris/alerts/<id>/dismiss/                 dismiss
  POST /api/v1/hris/alerts/<id>/resolve/                 mark resolved
  POST /api/v1/hris/alerts/generate/                     trigger generator
"""
from __future__ import annotations

from django.core.management import call_command
from django.utils import timezone
from rest_framework import status as drf_status, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.models import HRISAlert

from rest_framework import serializers


class HRISAlertSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source='get_kind_display', read_only=True)
    severity_label = serializers.CharField(source='get_severity_display', read_only=True)
    state_label = serializers.CharField(source='get_state_display', read_only=True)
    employee_name = serializers.CharField(source='employee.full_name',
                                          read_only=True, default=None)

    class Meta:
        model = HRISAlert
        fields = (
            'id', 'kind', 'kind_label', 'severity', 'severity_label',
            'state', 'state_label',
            'target_kind', 'target_id',
            'title', 'detail', 'due_date',
            'employee', 'employee_name', 'profile',
            'created_at', 'acknowledged_at', 'acknowledged_by', 'resolved_at',
        )
        read_only_fields = (
            'id', 'created_at', 'acknowledged_at', 'acknowledged_by',
            'resolved_at',
        )


class HRISAlertViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HRISAlertSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = HRISAlert.objects.all().order_by('-created_at')
        state = self.request.query_params.get('state')
        kind  = self.request.query_params.get('kind')
        if state:
            qs = qs.filter(state=state)
        if kind:
            qs = qs.filter(kind=kind)
        # Entity scope (CFO 2026-06-16): a scoped user only sees alerts about
        # employees in their granted entities (employee-less system alerts are
        # hidden from scoped users; unrestricted users still see everything).
        from core.mixins import scoped_company_ids
        ids = scoped_company_ids(self.request)
        if ids is not None:
            qs = qs.filter(employee__company_id__in=ids) if ids else qs.none()
        return qs


def _action(request, alert_id, new_state):
    try:
        a = HRISAlert.objects.get(pk=alert_id)
    except HRISAlert.DoesNotExist:
        return Response({'detail': 'not found'}, status=drf_status.HTTP_404_NOT_FOUND)
    a.state = new_state
    user = request.user
    if new_state == HRISAlert.STATE_ACK:
        a.acknowledged_at = timezone.now()
        a.acknowledged_by = user.username if user.is_authenticated else ''
    elif new_state == HRISAlert.STATE_RESOLVED:
        a.resolved_at = timezone.now()
    a.save()
    return Response(HRISAlertSerializer(a).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def acknowledge(request, alert_id):
    return _action(request, alert_id, HRISAlert.STATE_ACK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def dismiss(request, alert_id):
    return _action(request, alert_id, HRISAlert.STATE_DISMISSED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resolve(request, alert_id):
    return _action(request, alert_id, HRISAlert.STATE_RESOLVED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def generate_now(request):
    try:
        call_command('generate_hris_alerts')
    except Exception as e:
        return Response({'detail': f'generator failed: {e!r}'},
                        status=drf_status.HTTP_500_INTERNAL_SERVER_ERROR)
    return Response({'detail': 'ok',
                     'total': HRISAlert.objects.count(),
                     'open':  HRISAlert.objects.filter(state='open').count()})
