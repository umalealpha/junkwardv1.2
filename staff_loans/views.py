"""staff_loans/views.py — DRF endpoints for the staff-loan workflow."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.notifications import send_html_with_cfo_cc

from . import policy
from . import rates
from . import services as svc
from .models import StaffLoanApplication
from .serializers import (
    CfoDecideSerializer,
    CreateSerializer,
    DisburseSerializer,
    SignSerializer,
    StaffLoanApplicationSerializer,
)

log = logging.getLogger(__name__)

NAVY, ORANGE = '#0D1B2A', '#F4A623'


def _err(exc):
    if hasattr(exc, 'message_dict'):
        return Response(exc.message_dict, status=status.HTTP_400_BAD_REQUEST)
    if hasattr(exc, 'messages'):
        return Response({'detail': exc.messages}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', ''))[:45]


CONFIDENTIALITY_NOTICE = (
    'CONFIDENTIALITY NOTICE: This email and any attachments are confidential and '
    'intended solely for the addressee. If you have received this in error, please '
    'notify the sender immediately and delete all copies.'
)


def _house(title, body):
    return (
        f'<div style="font-family:\'Book Antiqua\',Palatino,Georgia,serif;color:{NAVY};max-width:640px;'
        f'border:1px solid #e5e7eb;border-radius:10px;overflow:hidden">'
        f'<div style="background:{NAVY};color:#fff;padding:16px 20px">'
        f'<span style="color:{ORANGE};font-size:20px;font-weight:bold">{title}</span></div>'
        f'<div style="padding:18px 20px;font-size:15px;line-height:1.55">{body}'
        f'<p style="color:#6b7280;font-size:12px;margin-top:18px">Alpha Direct · Omni HRIS → Staff Loans</p>'
        f'<p style="color:#9ca3af;font-size:11px;margin-top:10px;border-top:1px solid #eee;padding-top:8px">{CONFIDENTIALITY_NOTICE}</p>'
        f'</div></div>'
    )


def _notify(*, to, subject, body_html):
    to = [a for a in (to or []) if a]
    if not to:
        return
    try:
        send_html_with_cfo_cc(subject=subject, html=_house('Staff Loan', body_html), to=to)
    except Exception:  # noqa: BLE001
        log.exception('staff-loan email failed: %s', subject)


class StaffLoanApplicationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']
    serializer_class = StaffLoanApplicationSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx

    def get_queryset(self):
        qs = (StaffLoanApplication.objects
              .select_related('employee', 'cfo_decided_by', 'disbursed_by',
                              'employee_loan', 'issuance_journal_entry')
              .order_by('-created_at'))
        user = self.request.user
        if (getattr(user, 'is_superuser', False)
                or svc._is_hr_manager(user) or svc._is_final_approver(user)
                # Finance releases the loan now (CFO 15-Sep-2026), so Finance
                # has to be able to SEE it. Without this a Finance Manager's
                # disburse POST 404s on a queryset filtered to their own loans.
                or svc._is_finance_approver(user)):
            return qs
        return qs.filter(employee__user=user)

    # ---- scheme rules + the requester's cap, for the form ----
    @action(detail=False, methods=['get'])
    def meta(self, request):
        from payroll.models import Employee
        from hris.attendance_gate import attendance_gate
        emp = Employee.objects.filter(user=request.user).first()
        att_ok, _bad, att_msg = attendance_gate(getattr(emp, 'hris_profile', None)) if emp else (True, [], '')
        rate_row = rates.current_rate_row()
        return Response({
            'attendance_ok': att_ok,
            'attendance_msg': att_msg,
            'default_rate_pct': str(rates.current_annual_rate()),
            'rate_reference': (rate_row.reference_name if rate_row else 'scheme default'),
            'rate_updated': (rate_row.effective_from.isoformat() if rate_row else None),
            'staff_max_term_months': policy.STAFF_MAX_TERM_MONTHS,
            'vehicle_max_amount': str(policy.VEHICLE_MAX_AMOUNT),
            'vehicle_max_term_months': policy.VEHICLE_MAX_TERM_MONTHS,
            'blue_book_holders': [{'value': v, 'label': l} for v, l in policy.BLUE_BOOK_HOLDERS],
            'has_employee_record': bool(emp),
            'my_monthly_salary': (str(svc.monthly_salary_for(emp)) if emp and svc.monthly_salary_for(emp) else None),
        })

    def create(self, request, *args, **kwargs):
        from payroll.models import Employee
        emp = Employee.objects.filter(user=request.user).first()
        if not emp:
            return Response(
                {'detail': 'Your account is not linked to a payroll record yet — HR needs to link it first.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        ser = CreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        try:
            app = svc.create_application(
                employee=emp, loan_type=v['loan_type'],
                amount_requested=v['amount_requested'],
                term_months_requested=v['term_months_requested'],
                reason=v['reason'], user=request.user,
                vehicle_description=v.get('vehicle_description', ''),
                vehicle_reg=v.get('vehicle_reg', ''),
                blue_book_holder=v.get('blue_book_holder', ''),
                no_other_loans=v.get('no_other_loans', False),
                purchased_via_veritas=v.get('purchased_via_veritas', False),
            )
        except DjangoValidationError as exc:
            return _err(exc)
        return Response(self.get_serializer(app).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        app = self.get_object()
        try:
            app = svc.submit_application(app, request.user)
        except DjangoValidationError as exc:
            return _err(exc)
        _notify(
            to=[u.email for u in svc.cfo_approvers()],
            subject=f'Staff loan to approve — {app.employee.full_name} (P{app.amount_requested})',
            body_html=(f'<p><b>{app.employee.full_name}</b> applied for a '
                       f'<b>{app.get_loan_type_display()}</b> of <b>P{app.amount_requested:,}</b> '
                       f'over {app.term_months_requested} month(s).</p>'
                       f'<p style="color:#6b7280">Motivation: {app.reason}</p>'
                       f'<p style="color:#6b7280">The applicant has confirmed they have no other loans '
                       f'with any bank or financial institution.</p>'
                       f'<p style="background:#FFF7ED;border-left:3px solid {ORANGE};padding:8px 12px">'
                       f'A staff loan is a <b>favour, not an entitlement</b> — not everyone qualifies. '
                       f'Please weigh the applicant\'s Time Doctor hours, discipline and performance '
                       f'before approving.</p>'
                       f'<p>Approve or decline it in Omni → HRIS → Staff Loans.</p>'))
        # Tell the applicant WHY their loan now needs an extra signature, and
        # which tasks caused it (CFO 2026-08-07 — the first cut told nobody).
        from hris import exec_signoff_service, overdue_gate
        from hris.exec_signoff_models import ExecSignoff
        blocking = exec_signoff_service.blocking_signoff(ExecSignoff.Module.LOAN, app.pk)
        return Response({**self.get_serializer(app).data,
                         **overdue_gate.signoff_payload(blocking)})

    @action(detail=True, methods=['post'])
    def cfo_decide(self, request, pk=None):
        app = self.get_object()
        ser = CfoDecideSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        try:
            app = svc.cfo_decide(app, request.user, approve=v['approve'], notes=v.get('notes', ''),
                           decline_reason=v.get('decline_reason', ''),
                           approved_amount=v.get('approved_amount'),
                           approved_term_months=v.get('approved_term_months'),
                           annual_rate_pct=v.get('annual_rate_pct'))
        except DjangoValidationError as exc:
            return _err(exc)
        if app.employee.email:
            if app.status == StaffLoanApplication.Status.APPROVED:
                _notify(to=[app.employee.email],
                        subject='Your Alpha Direct staff loan was approved',
                        body_html=(f'<p>Good news — your <b>{app.get_loan_type_display()}</b> was approved.</p>'
                                   f'<p><b>P{app.approved_amount:,}</b> over {app.approved_term_months} month(s) '
                                   f'at {app.annual_rate_pct}% a year. You repay <b>P{app.total_repayable:,}</b> '
                                   f'in total — about <b>P{app.monthly_instalment:,}</b> a month from your salary.</p>'
                                   f'<p>Open Omni → HRIS → Staff Loans and sign to start it.</p>'))
            else:
                _notify(to=[app.employee.email],
                        subject='Your Alpha Direct staff loan was declined',
                        body_html=(f'<p>Your <b>{app.get_loan_type_display()}</b> request was declined.</p>'
                                   f'<p>Reason: {app.decline_reason}</p>'))
        # let HR know an approved loan is coming their way to disburse
        if app.status == StaffLoanApplication.Status.APPROVED:
            _notify(to=[u.email for u in svc.hr_managers()],
                    subject=f'Staff loan approved — {app.employee.full_name}, awaiting signature',
                    body_html=(f'<p>The CFO approved {app.employee.full_name}\'s '
                               f'<b>{app.get_loan_type_display()}</b>. Once they sign, disburse it in '
                               f'Omni → HRIS → Staff Loans.</p>'))
        return Response(self.get_serializer(app).data)

    @action(detail=True, methods=['post'])
    def sign(self, request, pk=None):
        app = self.get_object()
        ser = SignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            app = svc.sign_application(app, request.user,
                                 signature_data_url=ser.validated_data['signature_data_url'],
                                 signatory_full_name=ser.validated_data['signatory_full_name'],
                                 ip=_client_ip(request))
        except DjangoValidationError as exc:
            return _err(exc)
        # Finance releases loans now, so the "ready" notice goes to Finance.
        # Sending it to HR left the one role that can no longer act on it
        # holding the only prompt to act.
        _notify(to=[u.email for u in svc.finance_approvers()],
                subject=f'Staff loan signed — ready to release: {app.employee.full_name}',
                body_html=(f'<p>{app.employee.full_name} signed the undertaking for their '
                           f'<b>{app.get_loan_type_display()}</b>. Release it in '
                           f'Omni → HRIS → Staff Loans. The payment is loaded for CFO '
                           f'authorisation automatically once you do.</p>'))
        return Response(self.get_serializer(app).data)

    @action(detail=True, methods=['post'])
    def disburse(self, request, pk=None):
        app = self.get_object()
        ser = DisburseSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        try:
            app = svc.disburse(app, request.user,
                         disbursement_bank_code=v['disbursement_bank_code'],
                         disbursement_ref=v.get('disbursement_ref', ''),
                         blue_book_received=v.get('blue_book_received', False))
        except DjangoValidationError as exc:
            return _err(exc)
        if app.employee.email:
            _notify(to=[app.employee.email],
                    subject='Your Alpha Direct staff loan has been approved for payment',
                    # NOT "paid out". At this point the money has not left the
                    # bank — the payment is queued for CFO authorisation, and
                    # the CFO releases it in FNB. Telling a staff member they
                    # have been paid when they have not is how they go looking
                    # for money that is not there yet.
                    body_html=(f'<p>Your <b>{app.get_loan_type_display()}</b> of '
                               f'<b>P{app.effective_amount:,}</b> has been approved for '
                               f'payment. You will receive it once the CFO authorises '
                               f'the payment at the bank.</p>'
                               f'<p>You repay <b>P{app.monthly_instalment:,}</b> a month from your salary for '
                               f'{app.effective_term} month(s), starting next payroll.</p>'))
        return Response(self.get_serializer(app).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        app = self.get_object()
        try:
            app = svc.cancel_application(app, request.user)
        except DjangoValidationError as exc:
            return _err(exc)
        return Response(self.get_serializer(app).data)
