"""
procurement/pdf_view.py — GET /api/v1/purchase-orders/<id>/pdf/
                          POST /api/v1/purchase-orders/<id>/email/
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api_key_auth import ApiKeyAuthentication, ApiKeyScopePermission
from .models import PurchaseOrder
from .pdf import generate_po_pdf

log = logging.getLogger(__name__)


class POPDFView(APIView):
    # SEC-INT swarm 2026-06-08 #1: previously [ApiKey, Session, Token] which
    # DROPPED AzureJWTAuthentication — SSO users got 401 on PO PDF download
    # (same bug class as the 2026-06-05 HC upload). Inherit settings default
    # (AzureJWT, ApiKey, Session, Token) so SSO Bearer works alongside Manus
    # ApiKey + the legacy login.
    permission_classes = [IsAuthenticated, ApiKeyScopePermission]

    def get(self, request, pk):
        po = get_object_or_404(
            PurchaseOrder.objects.select_related(
                'supplier', 'company', 'currency_code',
                'created_by', 'fm_approved_by', 'cfo_approved_by',
            ).prefetch_related('lines__account'),
            pk=pk,
        )
        pdf = generate_po_pdf(po)
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="{po.po_number or po.id}.pdf"'
        return resp


class POEmailView(APIView):
    """POST /api/v1/purchase-orders/<id>/email/ — email the PO PDF to the
    supplier in one click (CFO 2026-07-07: no more download-then-attach).

    Body: {"to": "supplier@x.com", "cc": "optional@x.com", "note": "optional"}.

    Sent FROM the logged-in user's own mailbox via the Graph backend
    (app-level Mail.Send sends as any tenant user). If the tenant's
    Application Access Policy restricts the app to the omni mailbox, we
    retry from omni@ with Reply-To the user, and say so in the response.
    Cancelled POs cannot be sent. The send is stamped on the PO audit trail.
    """
    permission_classes = [IsAuthenticated, ApiKeyScopePermission]

    def post(self, request, pk):
        po = get_object_or_404(
            PurchaseOrder.objects.select_related(
                'supplier', 'company', 'currency_code', 'created_by',
            ).prefetch_related('lines'),
            pk=pk,
        )

        to = (request.data.get('to') or '').strip()
        cc = (request.data.get('cc') or '').strip()
        note = (request.data.get('note') or '').strip()
        try:
            validate_email(to)
        except ValidationError:
            return Response({'detail': 'Enter a valid supplier email address.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if cc:
            try:
                validate_email(cc)
            except ValidationError:
                return Response({'detail': 'The CC address is not a valid email.'},
                                status=status.HTTP_400_BAD_REQUEST)

        if po.status == PurchaseOrder.Status.CANCELLED:
            return Response({'detail': 'This PO is cancelled — it cannot be '
                                       'sent to a supplier.'},
                            status=status.HTTP_400_BAD_REQUEST)

        sender = (request.user.email or '').strip()
        if not sender:
            return Response({'detail': 'Your omni account has no email address '
                                       'to send from.'},
                            status=status.HTTP_400_BAD_REQUEST)

        user_name = (request.user.get_full_name() or request.user.username).strip()
        company_name = po.company.name if po.company_id else 'Alpha Direct Insurance'
        subject = f'Purchase Order {po.po_number} — {company_name}'
        total = f'{po.currency_code_id or "BWP"} {po.total_amount:,.2f}'
        body_lines = [
            f'{po.supplier.name}',
            '',
            f'Please find attached Purchase Order {po.po_number} '
            f'from {company_name}.',
            '',
            f'- PO number: {po.po_number}',
            f'- Total: {total}',
        ]
        if po.related_claim_reference:
            body_lines.append(f'- Claim reference: {po.related_claim_reference}')
        body_lines += [
            '',
            'Please confirm receipt. Invoices must quote the PO number and be '
            'sent to invoices@alphadirect.co.bw.',
        ]
        if note:
            body_lines += ['', note]
        body_lines += ['', 'Regards,', user_name, company_name]

        pdf = generate_po_pdf(po)
        filename = f'Purchase Order - {po.po_number or po.id}.pdf'

        def _send(from_email: str, reply_to: list[str] | None):
            msg = EmailMessage(
                subject=subject,
                body='\n'.join(body_lines),
                from_email=from_email,
                to=[to],
                cc=[cc] if cc else [],
                reply_to=reply_to or [],
            )
            msg.attach(filename, pdf, 'application/pdf')
            msg.send(fail_silently=False)

        sent_from = sender
        try:
            _send(sender, None)
        except Exception as exc:  # noqa: BLE001 — fall back to the omni mailbox
            log.warning('PO email send-as %s failed (%s); retrying from the '
                        'omni mailbox', sender, exc)
            from django.conf import settings as dj
            fallback = (getattr(dj, 'MICROSOFT_SENDER_UPN', '')
                        or 'omni@alphadirect.co.bw')
            try:
                _send(fallback, [sender])
                sent_from = f'{fallback} (reply-to {sender})'
            except Exception as exc2:  # noqa: BLE001
                log.error('PO email fallback send failed: %s', exc2)
                return Response(
                    {'detail': 'The email could not be sent — the mail service '
                               'rejected it. Try again or download the PDF.'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        # Remember the typed address on the supplier Contact when none is on
        # file yet — next send (and the claims review screen) prefills it.
        # Only ever fills a BLANK email; never overwrites an existing one.
        supplier = po.supplier
        if not (supplier.email or '').strip():
            supplier.email = to
            supplier.save(update_fields=['email', 'updated_at'])

        # "Sent ✓" stamp for the PO detail page + claims review screen.
        po.last_emailed_at = timezone.now()
        po.last_emailed_to = to
        # The email stamp is not a material edit — lift the terminal-status
        # immutability guard in PurchaseOrder.save() so APPROVED (etc.) POs
        # can be stamped too; without it this audit save raises after the
        # email has already gone out.
        po._allow_status_transition = True
        po.save(audit_user=request.user,
                audit_description=f'PO {po.po_number} emailed to {to}'
                                  + (f' (cc {cc})' if cc else '')
                                  + f' from {sent_from}')
        return Response({'sent': True, 'to': to, 'cc': cc or None,
                         'from': sent_from, 'po_number': po.po_number})
