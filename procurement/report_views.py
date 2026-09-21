"""
procurement/report_views.py — DRF endpoints for PO reports.
"""

from __future__ import annotations

from datetime import date as _date_cls

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .reports import (
    build_gr_ir_reconciliation,
    build_po_commitment,
    build_po_outstanding,
)
from django.utils import timezone


def _parse_as_of(request) -> _date_cls:
    raw = (request.query_params.get('as_of') or '').strip()
    if not raw:
        return timezone.localdate()
    try:
        return _date_cls.fromisoformat(raw)
    except ValueError:
        return timezone.localdate()


def _company_id(request) -> str | None:
    """Resolve company filter — accept UUID or Company.code (e.g. 'ADIC')."""
    cid = (request.query_params.get('company')
           or request.query_params.get('owner_company')
           or request.query_params.get('company_id')
           or '').strip()
    if not cid:
        return None
    # If it parses as UUID, use as-is.
    import uuid
    try:
        uuid.UUID(cid)
        return cid
    except ValueError:
        pass
    # Otherwise treat as a Company.code and look up.
    from core.models import Company
    c = Company.objects.filter(code__iexact=cid).first()
    return str(c.id) if c else None


class POOutstandingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_po_outstanding(_parse_as_of(request), _company_id(request)))


class POCommitmentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_po_commitment(_parse_as_of(request), _company_id(request)))


class GRIRReconciliationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_gr_ir_reconciliation(_parse_as_of(request), _company_id(request)))
