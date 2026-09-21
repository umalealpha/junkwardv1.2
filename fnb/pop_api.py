"""
fnb/pop_api.py — the proof-of-payment register endpoints (CFO 2026-08-26).

Read is finance-gated. The confirm endpoint is the ONLY way a proposed match
becomes a filed one — the AI cannot reach it (see fnb/pop_ai.py).

The PDF is served through this authenticated view, never a raw /media/ URL —
same rule as PaymentRequestAttachment, and /media/ is not served in production
anyway.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import FileResponse, Http404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import CanViewFinancials

from .models import FNBProofOfPayment as POP


def _serialise(p: POP) -> dict:
    return {
        'id': str(p.id),
        'reference': p.reference,
        'amount': p.amount,
        'bank_status': p.bank_status,
        'paid': p.paid,
        'received_at': p.received_at,
        'state': p.state,
        'state_display': p.get_state_display(),
        'is_filed': p.is_filed,
        'claim_number': (p.claim.claim_number if p.claim_id else p.claim_number_seen),
        'claim_matched': bool(p.claim_id),
        'payment_request_ref': (p.payment_request.ref if p.payment_request_id else ''),
        'proposed_kind': p.proposed_kind,
        'proposed_ref': p.proposed_ref,
        'proposed_reason': p.proposed_reason,
        'has_pdf': bool(p.proof_pdf),
        'confirmed_by': (p.confirmed_by.get_full_name() or p.confirmed_by.username
                         if p.confirmed_by_id else ''),
        'confirmed_at': p.confirmed_at,
        'review_note': p.review_note,
        'body_text': p.body_text,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def proofs(request):
    """The register. `?state=unfiled` / `?state=proposed` / `?days=30`."""
    qs = POP.objects.select_related('claim', 'payment_request', 'confirmed_by')
    state = (request.query_params.get('state') or '').strip().lower()
    if state:
        qs = qs.filter(state=state)
    days = request.query_params.get('days')
    if days:
        from datetime import timedelta
        from django.utils import timezone
        try:
            qs = qs.filter(received_at__gte=timezone.now() - timedelta(days=int(days)))
        except (TypeError, ValueError):
            pass

    rows = list(qs.order_by('-received_at')[:400])
    allp = POP.objects.filter(paid=True)
    summary = {
        'total': POP.objects.count(),
        'paid': allp.count(),
        'paid_value': allp.aggregate(s=Sum('amount'))['s'] or 0,
        'filed_claim': allp.filter(state=POP.State.FILED_CLAIM).count(),
        'filed_request': allp.filter(state=POP.State.FILED_REQUEST).count(),
        'unfiled': allp.filter(state=POP.State.UNFILED).count(),
        'proposed': allp.filter(state=POP.State.PROPOSED).count(),
        'dismissed': allp.filter(state=POP.State.DISMISSED).count(),
        'unfiled_value': (allp.filter(state__in=[POP.State.UNFILED, POP.State.PROPOSED])
                          .aggregate(s=Sum('amount'))['s'] or 0),
    }
    return Response({'summary': summary, 'proofs': [_serialise(p) for p in rows]})


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def proof_confirm(request, pk):
    """Accept or reject a proposed match. The only route to a filed proof."""
    from .pop_ai import confirm_proposal
    try:
        p = POP.objects.get(pk=pk)
    except (POP.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'Proof not found.'}, status=404)
    try:
        confirm_proposal(p, user=request.user,
                         accept=bool(request.data.get('accept')),
                         note=request.data.get('note') or '')
    except ValidationError as exc:
        msgs = getattr(exc, 'messages', None)
        return Response({'detail': msgs[0] if msgs else str(exc)}, status=400)
    p.refresh_from_db()
    return Response(_serialise(p))


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewFinancials])
def proof_pdf(request, pk):
    """Serve the stored proof through an authenticated view."""
    try:
        p = POP.objects.get(pk=pk)
    except (POP.DoesNotExist, ValueError, TypeError):
        raise Http404
    if not p.proof_pdf:
        raise Http404
    safe = (p.reference or 'proof').replace('/', '-')[:60]
    return FileResponse(p.proof_pdf.open('rb'), content_type='application/pdf',
                        as_attachment=True, filename=f'FNB-POP-{safe}.pdf')
