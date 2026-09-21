"""
billing/views.py

Non-API (Django) views — currently just invoice PDF download.
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from .models import Invoice
from .pdf import generate_invoice_pdf


class InvoicePDFView(APIView):
    """
    GET /api/v1/invoices/{id}/pdf/
    Returns the invoice as a downloadable PDF.
    """
    # SEC-INT swarm 2026-06-08 #4: [Session, Token] dropped AzureJWT — SSO
    # users got 401 downloading invoice PDFs. Inherit settings default
    # (AzureJWT, ApiKey, Session, Token).
    permission_classes     = [IsAuthenticated]

    def get(self, request, pk):
        invoice = get_object_or_404(
            Invoice.objects.prefetch_related('lines__tax_code', 'lines__account'),
            pk=pk,
        )
        pdf_bytes = generate_invoice_pdf(invoice)
        filename  = f"{invoice.invoice_number}.pdf"
        response  = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
