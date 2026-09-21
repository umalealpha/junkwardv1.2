"""integrations/claim_insight_views.py — GET /api/v1/claims/insight/?claim=…

The read-only "Claim insight" for the payment-request form (CFO 2026-08-31,
Claim-Description-Spec). Returns the claim facts, deterministic hard flags, and
the stored AI summary for one Graphite claim number. Facts/flags are Omni's own
arithmetic; the AI text is a stored string written off-peak. Never blocks."""
from decimal import Decimal, InvalidOperation

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from integrations.claim_insight import build_claim_insight


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def claim_insight(request):
    claim = (request.query_params.get('claim') or '').strip()
    entity = (request.query_params.get('entity') or '').strip()
    payee = (request.query_params.get('payee') or '').strip()
    exclude = (request.query_params.get('exclude') or '').strip() or None

    line_amount = None
    raw_amount = request.query_params.get('amount')
    if raw_amount not in (None, ''):
        try:
            line_amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            line_amount = None

    data = build_claim_insight(claim, line_amount=line_amount, entity=entity,
                               exclude_pk=exclude, payee=payee)
    return Response(data)
