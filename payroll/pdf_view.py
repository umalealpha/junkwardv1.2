"""
payroll/pdf_view.py — single PDF endpoint for a Payslip.

GET /api/v1/payslips/<pk>/pdf/

CFO directive 2026-05-19 (Final Verification Audit § 5):
"Wire up the Payslips PDF generation API."

Returns an A4 PDF for the supplied payslip. Authenticated callers only;
extra check that the caller actually owns the payslip (employee linked
to request.user) or has approver authority — payroll data is sensitive.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from core.models import get_user_profile
from .models import Payslip
from .pdf import generate_payslip_pdf


class PayslipPDFView(APIView):
    # SEC-INT swarm 2026-06-08 #3: [Session, Token] dropped AzureJWT — SSO
    # employees got 401 pulling their own payslip PDF. Inherit settings default
    # (AzureJWT, ApiKey, Session, Token).
    permission_classes     = [IsAuthenticated]

    def get(self, request, pk):
        payslip = get_object_or_404(
            Payslip.objects.select_related('employee', 'period', 'company')
            .prefetch_related('lines__component'),
            pk=pk,
        )
        if not self._may_read(request, payslip):
            raise PermissionDenied(
                'Payslip access restricted to the employee or an approver.'
            )
        # CFO directive 2026-07-26: an employee's own copy is only released once
        # the CFO has signed that company's month off. Payroll/HR/Finance keep
        # internal access so they can check a run before submitting it.
        if not self._is_approver(request.user):
            from .signoff_service import release_blocked_reason
            blocked = release_blocked_reason(payslip)
            if blocked:
                raise PermissionDenied(blocked)
        pdf = generate_payslip_pdf(payslip)
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = (
            f'attachment; filename="payslip_'
            f'{payslip.employee.employee_number or payslip.employee_id}_'
            f'{payslip.period.period_name}.pdf"'
        )
        return resp

    def _may_read(self, request, payslip) -> bool:
        user = request.user
        # Self-access: the linked Django user matches the requester
        if payslip.employee.user_id and payslip.employee.user_id == user.id:
            return True
        return self._is_approver(user)

    @staticmethod
    def _is_approver(user) -> bool:
        """CFO / FM / FC (or superuser) — may pull ANY payslip, signed or not."""
        if getattr(user, 'is_superuser', False):
            return True
        profile = get_user_profile(user)
        return bool(profile and getattr(profile, 'can_approve_journal_entries', False))
