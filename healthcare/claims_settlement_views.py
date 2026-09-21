"""
healthcare/claims_settlement_views.py — the ADH settlement runs screen's backend.

What each weekly run loaded, and what it skipped and why. Read-only: there is no
endpoint here that raises a payment request, because the ONLY route in is the
scheduled loader, which itself goes through the ordinary gated create path.

Claimant names are not in this response and are not in the model behind it.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from healthcare.models import AdhSettlementRun
# The SAME gate the AFA load-file screens already use — not a new class. This
# response carries claim numbers and settlement amounts for identifiable
# medical claims; a plain signed-in check served all of that to every staffer
# in the company. (Fable, 13-Sep-2026.)
from healthcare.permissions import IsAfaLoadFileOperator
# CFO directive 2026-09-13: "Health and Finance". The health half is the AFA
# operator allow-list above; the finance half is the existing finance-and-
# management title gate. Composed with DRF's OR — no new permission class.
from core.permissions import CanViewFinancials

#: Health OR Finance. Import this rather than re-deriving the pair.
CanViewAdhSettlements = IsAfaLoadFileOperator | CanViewFinancials


def _run_json(run: AdhSettlementRun) -> dict:
    return {
        'id': str(run.id),
        'loadedOn': run.loaded_on.isoformat(),
        'status': run.status,
        'statusLabel': run.get_status_display(),
        'fileName': run.file_name,
        'sha256': run.file_sha256,
        # The other three files that landed that Saturday, each with the one
        # condition it failed — the whole point of the screen.
        'notProcessed': run.rejected or [],
        'lineCount': run.line_count,
        'createdCount': run.created_count,
        'skippedCount': run.skipped_count,
        'failedCount': run.failed_count,
        'problems': run.problems or [],
        'error': run.error,
        'createdAt': run.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([CanViewAdhSettlements])
def adh_settlement_runs(request):
    """GET /health/adh-settlements/runs/ — the weekly load history, newest first."""
    runs = AdhSettlementRun.objects.all()[:104]        # two years of Saturdays
    return Response({'runs': [_run_json(r) for r in runs]})


@api_view(['GET'])
def adh_settlement_access(request):
    """GET /health/adh-settlements/access/ — may this user open the screen?

    The sidebar asks this so the menu entry is hidden rather than clicked into
    a 403. Hiding is NOT the control — adh_settlement_runs above is gated
    server-side and stays gated whatever the menu shows.
    """
    allowed = CanViewAdhSettlements().has_permission(request, None)
    return Response({'allowed': bool(allowed)})
