"""
reporting/ma_trace_views.py — endpoint for the TB → BS / P&L trace.
"""
from __future__ import annotations

import datetime as _dt

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from reporting.ma_trace import build_trial_balance_trace
from django.utils import timezone


class MaTraceView(APIView):
    """GET /api/v1/reports/ma-trace/?as_of=YYYY-MM-DD&company=<id>

    Returns every account's closing balance + the BS/P&L bucket the
    report engine slots it into. Anything in `unmapped` is a production
    incident — a rand of balance the MA workbook isn't seeing.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        as_of_raw = request.query_params.get('as_of')
        try:
            as_of = _dt.date.fromisoformat(as_of_raw) if as_of_raw else timezone.localdate()
        except ValueError:
            return Response(
                {'detail': 'as_of must be ISO YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from core.mixins import resolve_company_id_param
        company_id = resolve_company_id_param(request)
        return Response(build_trial_balance_trace(as_of=as_of, company_id=company_id))


class DraftTriageView(APIView):
    """GET /api/v1/journal-entries/triage/?as_of=YYYY-MM-DD&company=<id>

    Classify every draft JE into POST_READY / STALE_DELETE /
    INVESTIGATE / REVIEW so the close-team has a clear queue.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from ledger.draft_triage import triage_drafts

        as_of_raw = request.query_params.get('as_of')
        try:
            as_of = _dt.date.fromisoformat(as_of_raw) if as_of_raw else None
        except ValueError:
            return Response(
                {'detail': 'as_of must be ISO YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from core.mixins import resolve_company_id_param
        company_id = resolve_company_id_param(request)
        return Response(triage_drafts(as_of=as_of, company_id=company_id))
