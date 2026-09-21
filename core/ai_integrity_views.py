"""
core/ai_integrity_views.py — endpoint for AI-backed ledger integrity scans.
"""
from __future__ import annotations

import datetime as _dt

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class AIIntegrityScanView(APIView):
    """POST /api/v1/ai/integrity-scan/

    Body:
      {
        "from_date": "YYYY-MM-DD",
        "to_date":   "YYYY-MM-DD",
        "company":   "<uuid|optional>",
        "scanners":  ["narrative","vendor","amount"]   // optional
      }

    Returns the structured `run_integrity_scan` payload.

    Cost note: each scanner makes 1 DeepSeek call. Run with a narrow
    window first (≤ 7 days) to keep the bill predictable.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from core.ai_integrity import run_integrity_scan

        body = request.data or {}
        try:
            frm = _dt.date.fromisoformat(str(body.get('from_date') or ''))
            to  = _dt.date.fromisoformat(str(body.get('to_date')   or ''))
        except ValueError:
            return Response(
                {'detail': 'from_date and to_date must be ISO YYYY-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if to < frm:
            return Response({'detail': 'to_date before from_date.'},
                            status=status.HTTP_400_BAD_REQUEST)

        scanners = body.get('scanners') or ('narrative', 'vendor', 'amount')
        if not isinstance(scanners, (list, tuple)):
            scanners = ('narrative', 'vendor', 'amount')

        result = run_integrity_scan(
            from_date=frm, to_date=to,
            company_id=body.get('company') or None,
            scanners=tuple(scanners),
        )
        return Response(result)
