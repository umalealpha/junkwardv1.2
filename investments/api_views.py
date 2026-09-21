"""investments/api_views.py"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import services
from .models import Investment, InvestmentTransaction
from .serializers import InvestmentSerializer, InvestmentTransactionSerializer


def _val_to_resp(exc):
    if hasattr(exc, 'message_dict'):
        return Response(exc.message_dict, status=400)
    if hasattr(exc, 'messages'):
        return Response({'detail': exc.messages}, status=400)
    return Response({'detail': str(exc)}, status=400)


class InvestmentViewSet(viewsets.ModelViewSet):
    queryset = Investment.objects.select_related(
        'currency_code', 'investment_account',
    )
    serializer_class = InvestmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        cls = self.request.query_params.get('classification')
        if cls:
            qs = qs.filter(classification=cls)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(audit_user=self.request.user)


class InvestmentTransactionViewSet(viewsets.ModelViewSet):
    queryset = InvestmentTransaction.objects.select_related(
        'investment', 'cash_account', 'journal_entry', 'posted_by',
    )
    serializer_class = InvestmentTransactionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.queryset
        inv = self.request.query_params.get('investment')
        if inv:
            qs = qs.filter(investment_id=inv)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        return qs

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    @action(detail=True, methods=['post'])
    def post_transaction(self, request, pk=None):
        tx = self.get_object()
        try:
            services.post_transaction(tx, request.user)
        except DjangoValidationError as exc:
            return _val_to_resp(exc)
        return Response(self.get_serializer(tx).data)
