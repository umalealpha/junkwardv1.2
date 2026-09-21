"""integrations/api_views.py"""
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import IntegrationEvent
from .permissions import IntegrationEventAccess
from .serializers import IntegrationEventCreateSerializer, IntegrationEventSerializer
from .services import EventProcessor


class IntegrationEventViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    # SECURITY FIX (2026-08-25, Manus retest P0): this viewset declared NO
    # permission_classes, so it fell back to the project default IsAuthenticated
    # and any signed-in staffer could POST an event — which runs EventProcessor
    # synchronously and raises a real invoice / vendor bill / credit note under
    # the first superuser's name. See integrations/permissions.py for the split
    # (create = service keys only; list/retrieve/retry = finance admins).
    permission_classes = [IntegrationEventAccess]

    queryset = IntegrationEvent.objects.all().order_by('-received_at')
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields   = ['event_type', 'source_system', 'status']
    ordering_fields = ['received_at', 'processed_at', 'status', 'event_type']

    def get_serializer_class(self):
        if self.action == 'create':
            return IntegrationEventCreateSerializer
        return IntegrationEventSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        src = self.request.query_params.get('source_system')
        if src:
            qs = qs.filter(source_system=src)
        et = self.request.query_params.get('event_type')
        if et:
            qs = qs.filter(event_type=et)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def _key_label(self) -> str:
        """Label of the API key that authenticated this request ('' if none)."""
        api_key = getattr(self.request, 'auth', None)
        return str(getattr(api_key, 'label', '') or '')[:120]

    def perform_create(self, serializer):
        event = serializer.save(received_via=self._key_label())
        # Process synchronously (replace with Celery task later)
        EventProcessor().run(event)

    def create(self, request, *args, **kwargs):
        # Idempotency (2026-08-25): processing an event CREATES an accounting
        # record, so a redelivered push must not book the same revenue twice.
        # Return the ORIGINAL event with 200 rather than 201, so the sender can
        # tell a replay from a first delivery.
        #
        # This runs BEFORE is_valid() on purpose. The model's UniqueConstraint
        # makes DRF generate a UniqueValidator for the field, so validation
        # rejects a replay with a 400 "already exists" — which is a redelivery
        # failing rather than succeeding idempotently, and would have made the
        # guard below dead code. Caught by
        # test_same_idempotency_key_returns_the_original_event.
        key = str((request.data or {}).get('idempotency_key') or '').strip()
        if key:
            existing = IntegrationEvent.objects.filter(idempotency_key=key).first()
            if existing is not None:
                out = IntegrationEventSerializer(existing, context={'request': request})
                return Response(out.data, status=status.HTTP_200_OK)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        # Re-fetch to get updated status/result after processing
        event = IntegrationEvent.objects.get(pk=serializer.instance.pk)
        out   = IntegrationEventSerializer(event, context={'request': request})
        headers = self.get_success_headers(serializer.data)
        return Response(out.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=['post'], url_path='retry')
    def retry(self, request, pk=None):
        """Re-run the processor on a failed event."""
        event = self.get_object()
        if event.status not in (
            IntegrationEvent.Status.FAILED,
            IntegrationEvent.Status.RECEIVED,
        ):
            return Response(
                {'error': f"Cannot retry event with status '{event.status}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        EventProcessor().run(event)
        event.refresh_from_db()
        return Response(IntegrationEventSerializer(event, context={'request': request}).data)
