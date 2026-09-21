"""exceptions/api_views.py — DRF endpoints for the exception engine."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import Exception as ExceptionModel
from .serializers import (
    ExceptionCreateSerializer,
    ExceptionDetailSerializer,
    ExceptionListSerializer,
)


def _err(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': detail}, status=code)


class ExceptionViewSet(viewsets.ModelViewSet):
    queryset = ExceptionModel.objects.select_related(
        'acknowledged_by', 'resolved_by', 'dismissed_by', 'created_by',
    ).order_by('-created_at')
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'post', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'list':
            return ExceptionListSerializer
        if self.action == 'create':
            return ExceptionCreateSerializer
        return ExceptionDetailSerializer

    def get_queryset(self):
        qs = self.queryset
        params = self.request.query_params

        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('exception_type'):
            qs = qs.filter(exception_type=params['exception_type'])
        if params.get('severity'):
            qs = qs.filter(severity=params['severity'])
        if params.get('source_app'):
            qs = qs.filter(source_app=params['source_app'])
        if params.get('search'):
            term = params['search']
            qs = qs.filter(title__icontains=term) | qs.filter(source_label__icontains=term)
        if params.get('open_only') in ('1', 'true', 'yes'):
            qs = qs.filter(status__in=[
                ExceptionModel.Status.OPEN,
                ExceptionModel.Status.ACKNOWLEDGED,
                ExceptionModel.Status.IN_PROGRESS,
            ])
        return qs

    @action(detail=False, methods=['get'])
    def counts(self, request):
        """Return a tally of open exceptions by severity — used by the
        sidebar/topbar badge."""
        qs = self.get_queryset().filter(status__in=[
            ExceptionModel.Status.OPEN,
            ExceptionModel.Status.ACKNOWLEDGED,
            ExceptionModel.Status.IN_PROGRESS,
        ])
        out = {
            'total':    qs.count(),
            'critical': qs.filter(severity=ExceptionModel.Severity.CRITICAL).count(),
            'high':     qs.filter(severity=ExceptionModel.Severity.HIGH).count(),
            'medium':   qs.filter(severity=ExceptionModel.Severity.MEDIUM).count(),
            'low':      qs.filter(severity=ExceptionModel.Severity.LOW).count(),
        }
        return Response(out)

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        exc = self.get_object()
        try:
            services.acknowledge_exception(exc, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(ExceptionDetailSerializer(exc).data)

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        exc   = self.get_object()
        notes = request.data.get('notes', '')
        try:
            services.resolve_exception(exc, request.user, notes)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(ExceptionDetailSerializer(exc).data)

    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        exc    = self.get_object()
        reason = request.data.get('reason', '')
        try:
            services.dismiss_exception(exc, request.user, reason)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(ExceptionDetailSerializer(exc).data)

    @action(detail=True, methods=['post'], url_path='retry-linker')
    def retry_linker(self, request, pk=None):
        """Manually re-fire the Linker webhook for this exception."""
        exc = self.get_object()
        ok = services.notify_via_linker(exc)
        exc.refresh_from_db()
        return Response({
            'success': ok,
            'response': exc.linker_response,
            'notified_at': exc.linker_notified_at,
        })
