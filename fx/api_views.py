"""fx/api_views.py — DRF endpoints for FX revaluation."""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from ledger.models import FiscalPeriod

from . import services
from .models import FXRevaluation
from .serializers import (
    FXRevaluationDetailSerializer,
    FXRevaluationListSerializer,
)


class FXRevaluationViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = FXRevaluation.objects.select_related(
        'period', 'company', 'journal_entry', 'created_by',
    ).prefetch_related('lines__account', 'lines__currency_code')
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        return FXRevaluationListSerializer if self.action == 'list' else FXRevaluationDetailSerializer

    @action(detail=False, methods=['post'])
    def run(self, request):
        """Run FX revaluation for a period.

        body: {period_id, company_id?, dry_run?}
        """
        period_id = request.data.get('period_id')
        company_id = request.data.get('company_id')
        dry_run    = bool(request.data.get('dry_run', False))

        if not period_id:
            return Response({'detail': 'period_id is required.'}, status=400)

        period = get_object_or_404(FiscalPeriod, pk=period_id)

        company = None
        if company_id:
            from core.models import Company
            company = get_object_or_404(Company, pk=company_id)

        try:
            reval = services.run_fx_revaluation(
                period, request.user, company=company, dry_run=dry_run,
            )
        except DjangoValidationError as e:
            return Response({'detail': e.messages if hasattr(e, 'messages') else str(e)}, status=400)

        return Response(FXRevaluationDetailSerializer(reval).data)
