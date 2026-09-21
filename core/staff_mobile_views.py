"""core/staff_mobile_views.py — small self-service endpoints for the Nexus
Staff Portal (CFO 2026-07-14). Both are strictly self/internal-directory data:

  GET /api/v1/payroll/my-payslips/   the caller's OWN payslips (figures only —
                                     the full payroll register stays behind
                                     CanViewPayroll; seeing your own pay is
                                     every employee's right)
  GET /api/v1/staff/phonebook/       internal work directory: name, title,
                                     department, work phone/email of active
                                     staff. No IDs, no banking, no salary.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_payslips(request):
    from payroll.models import Employee, Payslip
    emp_ids = list(Employee.objects.filter(user=request.user).values_list('id', flat=True))
    if not emp_ids:
        return Response({'payslips': [], 'detail': 'No payroll record is linked to your account.'})
    qs = (Payslip.objects.filter(employee_id__in=emp_ids)
          .select_related('period', 'company')
          .prefetch_related('lines__component')
          .order_by('-period__start_date')[:24])

    def _detail(p):
        """Full earnings + deductions breakdown, from the SAME classifier the
        PDF uses (payroll/payslip_breakdown.build_breakdown).

        Before 2026-07-25 this method carried its own copy of the split and the
        copy was wrong the same way the PDF's was — employer contributions were
        returned as employee earnings, and a negative earning (housing salary
        sacrifice) was returned as a deduction that had already been applied. So
        the mobile app showed staff an earnings list that did not sum to gross.
        One classifier now serves both surfaces; see that module for detail.
        """
        from payroll.payslip_breakdown import build_breakdown
        b = build_breakdown(p)
        return {
            'earnings':   [{'label': l, 'amount': str(a)} for l, a in b.earnings],
            'deductions': [{'label': l, 'amount': str(a)} for l, a in b.deductions],
            # Employer contributions + cost-to-company removed from the
            # employee-facing payslip (CFO / Pako Kago directive 2026-07-29):
            # CTC is company cost, not the employee's pay, and must not display.
            'gross':             str(b.gross),
            'total_deductions':  str(b.total_deductions),
            'net':               str(b.net),
            # Non-empty when the itemised lines disagree with the stored
            # totals — the app must show this rather than a figure that cannot
            # be reconciled.
            'variances':  list(b.variances),
        }

    # "Available" = the slip actually has pay data. On prod payslips can carry
    # real figures while still status='draft', so we do NOT gate on status —
    # we gate on content. A month with no figures yet (e.g. June before the
    # run: 0 gross, no lines) shows "not finalised yet" instead of a misleading
    # BWP 0.00 card, and offers no download. (CFO 2026-07-14, Bharath feedback.)
    # On top of the content gate: from 2026-07 a payslip is only RELEASED to
    # the employee once the CFO has signed that company's month off (CFO
    # directive 2026-07-26). Earlier months are unaffected.
    from payroll.signoff_service import release_blocked_reason

    def _row(p):
        det = _detail(p)
        gross = p.gross_amount or 0
        net = p.net_amount or 0
        has_content = bool(gross or net or det['earnings'] or det['deductions'])
        hold = release_blocked_reason(p)
        available = has_content and hold is None
        return {
            'hold_reason': hold or '',
            'id': str(p.id),
            'period': getattr(p.period, 'period_name', '') or str(p.period_id),
            'start': p.period.start_date.isoformat() if getattr(p.period, 'start_date', None) else None,
            'end': p.period.end_date.isoformat() if getattr(p.period, 'end_date', None) else None,
            'company': p.company.code if p.company_id else '',
            'gross': str(p.gross_amount or 0),
            'paye': str(p.paye_amount or 0),
            'net': str(p.net_amount or 0),
            'currency': getattr(p, 'source_currency', 'BWP') or 'BWP',
            'status': p.status,
            'available': available,
            'detail': det,
        }
    return Response({'payslips': [_row(p) for p in qs]})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def phonebook(request):
    from payroll.models import Employee
    q = (request.query_params.get('q') or '').strip()
    qs = (Employee.objects.filter(status=Employee.Status.ACTIVE)
          .exclude(full_name='')
          .order_by('full_name'))
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(full_name__icontains=q) | Q(department__icontains=q)
                       | Q(job_title__icontains=q))
    return Response({'people': [{
        'name': e.full_name,
        'title': e.job_title,
        'department': e.department,
        'phone': e.phone,
        'email': e.email,
    } for e in qs[:200]]})
