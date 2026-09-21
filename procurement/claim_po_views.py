"""PO-in-Graphite doorway — CFO directive 2026-08-24.

ONE read-only endpoint so Graphite can show, on a claim, the purchase order(s)
Omni raised for it:

    GET /api/v1/purchase-orders/by-claim/?claim_ref=G2026004801

Omni owns the accounting truth; this is a read doorway, never a second copy.
Graphite calls it server-side with a dedicated read-only key and caches the
answer briefly — it does NOT store status or amounts (those live only here).

Contract agreed with Pramod Bisen (ADRisk IT), 2026-08-24:
  Auth          Authorization: ApiKey <key>, carrying the `po-claim-read` scope
                (a dedicated key — never the AlphaBrain/notebook key). The scope
                is read-only and path-narrowed at the authentication layer
                (core.api_key_auth), so the key can reach nothing else and can
                change nothing.
  Match         exact on related_claim_reference after trim + upper-case
                (case-insensitive), hard filter department = claims, no fuzzy.
  Reference     treated as an opaque string — never parsed or derived from.
                A claim may have more than one PO.
  Response 200  {claim_ref, purchase_orders: [ {allow-listed fields} ]}.
                No POs  → 200 with an empty list, never 404, so "no PO yet" and
                "unknown reference" read as the same quiet state for the user.
  PII           none, ever — an explicit allow-list of fields, not a serializer
                over the model, so a new PII field on PurchaseOrder can never
                leak here by accident.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api_key_auth import ApiKeyAuthentication
from procurement.models import PurchaseOrder

log = logging.getLogger(__name__)

# The only fields that ever leave this endpoint. Kept as an explicit builder
# (below) rather than a ModelSerializer so that adding a field to PurchaseOrder
# — a bank detail, a note, a person — can never widen the response by default.
_ALLOWED_FIELDS = (
    'uuid', 'po_number', 'status', 'total', 'currency',
    'date', 'supplier_name', 'deep_link',
)


def _key_has_scope(request) -> bool:
    """True when the caller authenticated with a key carrying `po-claim-read`
    (or `admin`). Defence-in-depth: the authentication layer already refuses a
    key without the scope, but this endpoint states its own requirement so it
    stays correct even if the auth wiring changes."""
    api_key = getattr(request, 'auth', None)
    if api_key is None or not hasattr(api_key, 'allowed_scopes'):
        return False
    scopes = list(api_key.allowed_scopes or [])
    return 'po-claim-read' in scopes or 'admin' in scopes


def _deep_link(po) -> str:
    """Absolute link that opens THIS exact PO in Omni. Same base the PO-verify
    QR uses; the path is the frontend PO detail route (/purchase-orders/<id>)."""
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw')
    return f'{base}/purchase-orders/{po.id}'


def _render(po) -> dict:
    """Allow-listed projection of one PO. No PII, no model passthrough."""
    return {
        'uuid':          str(po.id),
        'po_number':     po.po_number,
        'status':        po.get_status_display(),
        'total':         str(po.total_amount),
        'currency':      po.currency_code_id,
        'date':          po.issue_date.isoformat() if po.issue_date else None,
        'supplier_name': po.supplier.name if po.supplier_id else '',
        'deep_link':     _deep_link(po),
    }


@api_view(['GET'])
@authentication_classes([ApiKeyAuthentication])
@permission_classes([IsAuthenticated])
def po_by_claim(request):
    """GET /api/v1/purchase-orders/by-claim/?claim_ref=G2026004801"""
    if not _key_has_scope(request):
        return Response({'detail': 'This key may not read purchase orders.'},
                        status=status.HTTP_403_FORBIDDEN)

    raw = request.GET.get('claim_ref', '')
    ref = (raw or '').strip().upper()
    if not ref:
        return Response({'detail': 'claim_ref is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Exact match after trim + upper-case (case-insensitive), claims only, no
    # fuzzy. iexact handles the input casing; department is a hard filter.
    qs = (PurchaseOrder.objects
          .filter(department=PurchaseOrder.Department.CLAIMS,
                  related_claim_reference__iexact=ref)
          .select_related('supplier')
          .order_by('-issue_date', '-po_number'))

    purchase_orders = [_render(po) for po in qs]

    key = getattr(request, 'auth', None)
    log.info('po-by-claim key=%s ref=%s count=%d',
             getattr(key, 'key_prefix', '?'), ref, len(purchase_orders))

    return Response({'claim_ref': ref, 'purchase_orders': purchase_orders})
