"""
payroll/signoff_views.py — the payroll sign-off screen's API.

  GET  /api/v1/payroll/sign-off/          board: every company x the chosen
                                          period, with live totals + both legs
  POST /api/v1/payroll/sign-off/sign/     {period, company, side?}  HR or Finance signs
  POST /api/v1/payroll/sign-off/reject/   {period, company, reason} send back

Dual sign-off — CFO directive 2026-07-28: a month closes when an HR signer
(Unami/Dorothy) AND a Finance signer (Kago/Pako) have both signed the current
figures. The CFO is out of the routine loop.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Company

from .amendment_views import user_can_view_payroll
from .models import PayrollPeriod, PayrollSignOff
from .signoff_models import live_payroll_totals
from .signoff_service import (
    can_sign_finance, can_sign_hr, is_signed_off, reject_signoff,
    sign_payroll, signoff_side,
)

log = logging.getLogger(__name__)


def _name(u):
    return (u.get_full_name() or u.email) if u else None


def _row_json(company, period, row):
    live = live_payroll_totals(period, company)
    out = {
        'company_id':     str(company.id),
        'company':        company.name,
        'period':         period.period_name,
        'period_id':      str(period.id),
        'live_headcount': live['headcount'],
        'live_gross':     str(live['gross']),
        'live_paye':      str(live['paye']),
        'live_net':       str(live['net']),
        'hr_signed_by':   None,
        'hr_signed_at':   None,
        'fin_signed_by':  None,
        'fin_signed_at':  None,
        'rejected_by':    None,
        'rejection_reason': '',
        'drift':          None,
        'closed':         False,
        'needs_hr':       True,
        'needs_fin':      True,
    }
    if row is None:
        return out
    drift = row.drift()
    drifted = drift is not None
    rejected = row.status == PayrollSignOff.Status.REJECTED
    invalid = drifted or rejected     # a change / send-back drops both legs
    out.update({
        'id':             str(row.id),
        'hr_signed_by':   None if invalid else _name(row.hr_signed_by),
        'hr_signed_at':   None if invalid else (row.hr_signed_at.isoformat() if row.hr_signed_at else None),
        'fin_signed_by':  None if invalid else _name(row.fin_signed_by),
        'fin_signed_at':  None if invalid else (row.fin_signed_at.isoformat() if row.fin_signed_at else None),
        'rejected_by':    _name(row.rejected_by) if rejected else None,
        'rejection_reason': row.rejection_reason if rejected else '',
        'closed':         is_signed_off(period, company),
        'needs_hr':       invalid or not row.hr_signed_by_id,
        'needs_fin':      invalid or not row.fin_signed_by_id,
    })
    if drifted:
        out['drift'] = {side: {k: str(v) for k, v in vals.items()}
                        for side, vals in drift.items()}
    return out


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def signoff_board(request):
    if not user_can_view_payroll(request.user):
        return Response({'detail': 'Payroll data is restricted to HR / Finance.'},
                        status=403)

    period_name = request.GET.get('period') or ''
    if period_name:
        period = PayrollPeriod.objects.filter(period_name=period_name).first()
    else:
        period = PayrollPeriod.objects.order_by('-start_date').first()

    side = signoff_side(request.user)
    base = {
        'can_sign_hr':  can_sign_hr(request.user),
        'can_sign_fin': can_sign_finance(request.user),
        'my_side':      side,
    }
    if period is None:
        return Response({'period': None, 'rows': [], 'periods': [], **base})

    existing = {r.company_id: r for r in
                PayrollSignOff.objects.filter(period=period)
                .select_related('company', 'period', 'hr_signed_by',
                                'fin_signed_by', 'rejected_by')}
    rows = []
    for company in Company.objects.all().order_by('name'):
        live = live_payroll_totals(period, company)
        row = existing.get(company.id)
        if live['headcount'] == 0 and row is None:
            continue
        rows.append(_row_json(company, period, row))

    return Response({
        'period':    period.period_name,
        'period_id': str(period.id),
        'periods':   list(PayrollPeriod.objects.order_by('-start_date')
                          .values_list('period_name', flat=True)[:24]),
        'rows':      rows,
        **base,
    })


def _resolve(request):
    period_name = (request.data.get('period') or '').strip()
    company_id  = request.data.get('company')
    if not period_name or not company_id:
        return None, None, Response(
            {'detail': 'period and company are required.'}, status=400)
    period  = get_object_or_404(PayrollPeriod, period_name=period_name)
    company = get_object_or_404(Company, pk=company_id)
    return period, company, None


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def signoff_sign(request):
    period, company, err = _resolve(request)
    if err:
        return err
    side = (request.data.get('side') or '').strip().lower() or None
    try:
        row = sign_payroll(period, company, request.user, side=side)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    _notify_after_sign(row)
    return Response(_row_json(company, period, row))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def signoff_reject(request):
    period, company, err = _resolve(request)
    if err:
        return err
    row = PayrollSignOff.objects.filter(period=period, company=company).first()
    if row is None:
        return Response({'detail': 'Nothing to send back yet.'}, status=400)
    try:
        reject_signoff(row, request.user, request.data.get('reason') or '')
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    from core.notifications import notify_payroll_signoff_rejected
    notify_payroll_signoff_rejected(row)
    return Response(_row_json(company, period, row))


# ── Notifications ───────────────────────────────────────────────────────────
def _notify_after_sign(row):
    """After a signature: clear the just-signed side's task, and if the month
    is now fully signed, clear the other side's task too and tell the payroll
    team it is released. Best-effort."""
    from core.notifications import (
        close_payroll_side_tasks, notify_payroll_signoff_complete,
    )
    if row.hr_signed_by_id:
        close_payroll_side_tasks(row.company, row.period, 'hr')
    if row.fin_signed_by_id:
        close_payroll_side_tasks(row.company, row.period, 'finance')
    if is_signed_off(row.period, row.company):
        notify_payroll_signoff_complete(row)
        _push_payslips_released(row)


def _push_payslips_released(row):
    """Web Push each employee on the just-released month: "Your payslip for
    <month> is ready" → /app/payslips (CFO 2026-09-03). Fail-soft: push may be
    unconfigured, an employee may have no login, a device may be dead — none of
    that can fail the sign-off that already happened."""
    try:
        from core.webpush import push_enabled, send_push_to_user
        if not push_enabled():
            return
        from django.contrib.auth.models import User
        from .models import Payslip
        month = _month_label(row.period.period_name)
        seen = set()
        for ps in (Payslip.objects.filter(period=row.period, company=row.company)
                   .exclude(status=Payslip.Status.CANCELLED)
                   .select_related('employee__user')):
            emp = ps.employee
            user = emp.user
            if user is None and emp.email:
                user = User.objects.filter(email__iexact=emp.email, is_active=True).first()
            if user is None or user.id in seen:
                continue
            seen.add(user.id)
            send_push_to_user(user, 'Payslip ready',
                              f'Your payslip for {month} is ready.', url='/app/payslips')
    except Exception as exc:  # noqa: BLE001 — telemetry-grade side effect, never fatal
        log.warning('Payslip-released push failed for %s: %s', row, exc)


def _month_label(period_name: str) -> str:
    """'2026-08' → 'August 2026'; anything else is shown as-is."""
    try:
        from datetime import datetime
        return datetime.strptime(period_name, '%Y-%m').strftime('%B %Y')
    except (TypeError, ValueError):  # not YYYY-MM → show the raw period name
        return period_name
