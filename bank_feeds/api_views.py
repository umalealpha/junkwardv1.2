"""bank_feeds/api_views.py"""

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import BankFeedConfig, BankFeedRun
from .serializers import BankFeedConfigSerializer, BankFeedRunSerializer


class BankFeedConfigViewSet(viewsets.ModelViewSet):
    queryset = BankFeedConfig.objects.select_related('bank_account')
    serializer_class = BankFeedConfigSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(audit_user=self.request.user)

    @action(detail=True, methods=['post'])
    def run_now(self, request, pk=None):
        """Trigger a manual pull. Always creates a BankFeedRun row."""
        cfg = self.get_object()
        run = services.run_feed(cfg, triggered_by=request.user)
        return Response(BankFeedRunSerializer(run).data)


class BankFeedRunViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = BankFeedRun.objects.select_related('config', 'triggered_by')
    serializer_class = BankFeedRunSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        cfg = self.request.query_params.get('config')
        if cfg:
            qs = qs.filter(config_id=cfg)
        return qs
