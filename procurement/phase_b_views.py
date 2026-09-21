"""
procurement/phase_b_views.py — DRF endpoints for Manus PO Audit Phase B.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status as drf_status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.api_key_auth import ApiKeyAuthentication, ApiKeyScopePermission

from .models import (
    ApprovalDelegate, POAmendment, POAttachment, PurchaseOrder,
)
from .phase_b_services import (
    apply_amendment, request_amendment, resolve_approver,
)


# ---------------------------------------------------------------------------
# Approval delegation CRUD
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def approval_delegations(request):
    """GET — list caller's active delegations granted.
    POST — create one. Body: {delegate: <user_id|username>, role, starts_at, ends_at, reason}.
    """
    if request.method == 'GET':
        rows = (ApprovalDelegate.objects
                .filter(delegator=request.user, is_active=True)
                .select_related('delegate')
                .order_by('-starts_at'))
        return Response({'delegations': [
            {
                'id':         str(d.id),
                'delegate':   d.delegate.username,
                'role':       d.role,
                'starts_at':  str(d.starts_at),
                'ends_at':    str(d.ends_at),
                'reason':     d.reason,
                'is_active':  d.is_active and d.is_currently_active,
            }
            for d in rows
        ]})

    body = request.data or {}
    from django.contrib.auth.models import User
    target_ref = (body.get('delegate') or '').strip()
    if not target_ref:
        return Response({'detail': 'delegate (user id or username) required.'}, status=400)
    target = User.objects.filter(username=target_ref).first() \
             or User.objects.filter(pk=target_ref).first()
    if target is None:
        return Response({'detail': f'Delegate not found: {target_ref!r}.'}, status=404)
    if target.id == request.user.id:
        return Response({'detail': 'Cannot delegate to yourself.'}, status=400)

    role      = (body.get('role') or '').strip().lower()
    valid_roles = [r[0] for r in ApprovalDelegate.Role.choices]
    if role not in valid_roles:
        return Response({'detail': f'role must be one of {valid_roles}.'}, status=400)
    starts_at = body.get('starts_at')
    ends_at   = body.get('ends_at')
    if not (starts_at and ends_at):
        return Response({'detail': 'starts_at + ends_at (YYYY-MM-DD) required.'}, status=400)

    obj = ApprovalDelegate.objects.create(
        delegator=request.user, delegate=target, role=role,
        starts_at=starts_at, ends_at=ends_at,
        reason=str(body.get('reason') or '')[:200],
        is_active=True,
    )
    return Response({
        'id':        str(obj.id),
        'delegate':  target.username,
        'role':      obj.role,
        'starts_at': str(obj.starts_at),
        'ends_at':   str(obj.ends_at),
        'reason':    obj.reason,
    }, status=drf_status.HTTP_201_CREATED)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def revoke_delegation(request, pk):
    obj = ApprovalDelegate.objects.filter(pk=pk, delegator=request.user).first()
    if not obj:
        return Response({'detail': 'Not found or not yours to revoke.'}, status=404)
    obj.is_active = False
    obj.save(update_fields=['is_active', 'updated_at'])
    return Response({'id': str(obj.id), 'revoked': True})


# ---------------------------------------------------------------------------
# PO Amendments
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def amend_po(request, pk):
    """POST /api/v1/purchase-orders/<uuid>/amend/

    Body: {reason: str, lines: [{description, quantity, unit_price, account_id?}, ...]}
    Creates a PENDING_APPROVAL amendment with diff.before snapshot.
    Approval is granted via POST .../amend/<aid>/approve/.
    """
    po = get_object_or_404(PurchaseOrder, pk=pk)
    body = request.data or {}
    try:
        amendment = request_amendment(
            po, body.get('lines') or [], request.user,
            reason=str(body.get('reason') or '')[:500],
        )
    except ValueError as e:
        return Response({'detail': str(e)}, status=409)
    return Response({
        'id':       str(amendment.id),
        'version':  amendment.version,
        'status':   amendment.status,
        'po':      po.po_number,
    }, status=drf_status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve_amendment(request, pk, aid):
    amendment = get_object_or_404(POAmendment, pk=aid, purchase_order_id=pk)
    # Effective approver — honour delegations
    effective = resolve_approver(request.user, 'cfo')
    if effective.id != request.user.id and effective.id != amendment.purchase_order.created_by_id:
        # Delegate may approve on behalf of delegator
        pass
    try:
        apply_amendment(amendment, request.user)
    except ValueError as e:
        return Response({'detail': str(e)}, status=409)
    return Response({
        'id':         str(amendment.id),
        'po':         amendment.purchase_order.po_number,
        'version':    amendment.version,
        'applied_at': amendment.applied_at.isoformat() if amendment.applied_at else None,
        'new_total':  str(amendment.purchase_order.total_bwp),
    })


# ---------------------------------------------------------------------------
# PO Attachments
# ---------------------------------------------------------------------------

def _visible_po(request, po_pk):
    """The PO, through the SAME gate as GET /purchase-orders/<pk>/ — the
    PurchaseOrderViewSet's company-scoped queryset (CompanyScopedViewSetMixin:
    UserCompanyAccess grants; superuser / admin / CFO unrestricted). None when
    the caller may not see it, so the caller answers exactly as the detail view
    does: 404. One gate, not a second copy of the rule."""
    from procurement.api_views import PurchaseOrderViewSet
    view = PurchaseOrderViewSet()
    view.request = request
    view.action = 'retrieve'
    view.kwargs = {'pk': str(po_pk)}
    view.format_kwarg = None
    return view.get_queryset().filter(pk=po_pk).first()


class POAttachmentViewSet(viewsets.ViewSet):
    """
    GET    /api/v1/purchase-orders/<uuid>/attachments/   list
    POST   /api/v1/purchase-orders/<uuid>/attachments/   multipart upload
    DELETE /api/v1/purchase-orders/<uuid>/attachments/<aid>/   remove
    GET    /api/v1/purchase-orders/<uuid>/attachments/<aid>/file/   open (authenticated)
    """
    # SEC-INT swarm 2026-06-08 #2: [ApiKeyAuthentication] REPLACED the defaults
    # (the "session/token via DRF defaults" comment was wrong — overriding the
    # attribute completely shadows DEFAULT_AUTHENTICATION_CLASSES). That dropped
    # AzureJWT/Session/Token so SSO users got 401 on PO attachments. Inherit
    # the project default (AzureJWT, ApiKey, Session, Token).
    permission_classes     = [IsAuthenticated, ApiKeyScopePermission]
    parser_classes         = [MultiPartParser, FormParser, JSONParser]

    def list(self, request, po_pk=None):
        from django.urls import reverse
        # Same entity gate as the PO detail view — a PO outside the caller's
        # companies does not exist as far as this endpoint is concerned (2026-09-04
        # security review: list/create/destroy were reachable cross-entity).
        if _visible_po(request, po_pk) is None:
            return Response({'detail': 'Not found.'}, status=404)
        rows = POAttachment.objects.filter(purchase_order_id=po_pk).select_related('uploaded_by')
        return Response({'attachments': [
            {
                'id':           str(a.id),
                'label':        a.label,
                'filename':     a.file.name.split('/')[-1] if a.file else '',
                # `url` is the raw /media/ path — only served with DEBUG on.
                # `file_url` is the authenticated endpoint below; the phone
                # opens through it (CFO 2026-09-04). Kept both: desktop callers
                # still read `url`.
                'url':          a.file.url if a.file else '',
                'file_url':     reverse('v1-po-attachment-file',
                                        kwargs={'po_pk': po_pk, 'pk': a.pk}),
                'content_type': a.content_type,
                'size_bytes':   a.size_bytes,
                'uploaded_by':  a.uploaded_by.username if a.uploaded_by else '',
                'created_at':   a.created_at.isoformat(),
            }
            for a in rows
        ]})

    def create(self, request, po_pk=None):
        if _visible_po(request, po_pk) is None:
            return Response({'detail': 'Not found.'}, status=404)
        po = get_object_or_404(PurchaseOrder, pk=po_pk)
        f = request.FILES.get('file')
        if not f:
            return Response({'detail': 'No file uploaded.'}, status=400)
        obj = POAttachment.objects.create(
            purchase_order=po,
            file=f,
            label=str(request.data.get('label') or '')[:120],
            content_type=getattr(f, 'content_type', '')[:100],
            size_bytes=getattr(f, 'size', 0) or 0,
            uploaded_by=request.user,
        )
        return Response({
            'id':       str(obj.id),
            'label':    obj.label,
            'url':      obj.file.url if obj.file else '',
            'size':     obj.size_bytes,
        }, status=drf_status.HTTP_201_CREATED)

    def destroy(self, request, po_pk=None, pk=None):
        if _visible_po(request, po_pk) is None:
            return Response({'detail': 'Not found.'}, status=404)
        obj = POAttachment.objects.filter(pk=pk, purchase_order_id=po_pk).first()
        if not obj:
            return Response({'detail': 'Not found.'}, status=404)
        obj.delete()
        return Response({'id': pk, 'deleted': True})

    def file(self, request, po_pk=None, pk=None):
        """Authenticated inline open of one attachment. Never a raw /media/ URL
        (prod serves DEBUG=False). Mirrors taskboard.payment_request_attachment_file:
        whoever may read the PO pack may open the quote behind it. A PO outside
        the caller's entities, or an attachment id that is not on THIS PO, is a
        plain 404 — the same answer the PO detail view gives, so nothing leaks."""
        import mimetypes

        from django.http import FileResponse, Http404
        if _visible_po(request, po_pk) is None:
            raise Http404
        a = POAttachment.objects.filter(pk=pk, purchase_order_id=po_pk).first()
        if a is None or not a.file:
            raise Http404
        name = (a.file.name or 'attachment').split('/')[-1]
        # Inline rendering ONLY for types a browser cannot execute (PDF, images),
        # and only when the type comes from the file's own extension — never from
        # the uploader-supplied content_type (a file named "quote" uploaded as
        # text/html would otherwise render same-origin = stored XSS).
        guessed = mimetypes.guess_type(name)[0] or ''
        inline_ok = guessed == 'application/pdf' or guessed.startswith('image/')
        if inline_ok:
            return FileResponse(a.file.open('rb'), content_type=guessed,
                                as_attachment=False, filename=name)
        return FileResponse(a.file.open('rb'), content_type='application/octet-stream',
                            as_attachment=True, filename=name)
