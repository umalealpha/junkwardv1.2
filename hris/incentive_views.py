"""
hris/incentive_views.py — REST endpoints for the incentive-approval workflow
(CFO directive 2026-07-13).

  GET  /hris/api/incentives/                    role-filtered list + viewer flags
  POST /hris/api/incentives/                    submit (managers and above)
  POST /hris/api/incentives/<uuid>/approve/     sign the CFO or HR slot
  POST /hris/api/incentives/<uuid>/reject/      reject (either approver)
  POST /hris/api/incentives/<uuid>/mark-processed/  Finance: loaded to payroll

Deliberate gate design (differs from the whitelist-gated amendment views):
makers are line managers who are NOT HRIS-whitelist members, and a maker
only ever sees their OWN requests — so the list/submit surface gates on the
manager tier, not the whitelist. Approvals remain locked to the CFO + Unami
(plus superuser backup), and the payroll tick to Finance.
"""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

log = logging.getLogger(__name__)

from .incentive_models import IncentiveRequest
from .incentive_service import (
    amend_incentive, approve_request, can_manage_recurring, can_submit,
    create_recurring_template, current_period, generate_recurring_for_period,
    is_approver, is_finance, list_recurring_templates, mark_processed,
    reject_request, set_recurring_active, slot_for, submit_request,
)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def push_incentives_to_payroll(request):
    """Finance: push a month's approved incentives into the pending payroll
    batch on demand. The auto-feed already runs on approval; this recovers any
    straggler (e.g. approved before its payroll period existed) and is safe to
    press repeatedly — it is idempotent and never double-counts."""
    if not is_finance(request.user):
        return Response({'detail': 'Only Finance can push incentives to payroll.'},
                        status=status.HTTP_403_FORBIDDEN)
    from payroll.amendment_views import _resolve_company
    from .incentive_payroll_feed import feed_period_company
    period = (request.data.get('period') or '').strip()
    company_ref = (request.data.get('company_id') or request.data.get('company') or '').strip()
    if not period:
        return Response({'detail': 'period (YYYY-MM) is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    company = _resolve_company(company_ref) if company_ref else None
    if company is None:
        return Response({'detail': 'A valid entity (company_id or code) is required.'},
                        status=status.HTTP_400_BAD_REQUEST)
    result = feed_period_company(period_label=period, company=company, user=request.user)
    return Response(result, status=status.HTTP_200_OK)



def _serialize(r: IncentiveRequest, viewer=None) -> dict:
    viewer_email = (getattr(viewer, 'email', '') or '').strip().lower()
    return {
        'id': str(r.pk),
        'title': r.title,
        'period': r.period,
        'department': r.department,
        'notes': r.notes,
        'status': r.status,
        'maker_email': r.maker_email,
        'is_own': bool(r.maker_email and viewer_email
                       and r.maker_email.strip().lower() == viewer_email),
        'created_at': r.created_at.isoformat() if r.created_at else None,
        'total': str(r.total),
        'signatures': {
            'cfo': {'signed': bool(r.cfo_approved_at),
                    'at': r.cfo_approved_at.isoformat() if r.cfo_approved_at else None},
            'hr':  {'signed': bool(r.hr_approved_at),
                    'at': r.hr_approved_at.isoformat() if r.hr_approved_at else None},
        },
        'rejected': {
            'at': r.rejected_at.isoformat() if r.rejected_at else None,
            'notes': r.decision_notes,
        } if r.status == IncentiveRequest.Status.REJECTED else None,
        'payroll': {
            'processed': r.payroll_processed,
            'at': r.payroll_processed_at.isoformat() if r.payroll_processed_at else None,
        },
        'is_recurring': bool(r.source_template_id),
        'amended': {
            'at': r.amended_at.isoformat(),
            'by': (getattr(r.amended_by, 'email', '') or '')
                  if r.amended_by_id else '',
        } if r.amended_at else None,
        'lines': [
            {'id': str(l.pk), 'name': l.name, 'basis': l.basis,
             'amount': str(l.amount),
             'beyond_normal_duties': l.beyond_normal_duties,
             'on_time': l.on_time, 'error_free': l.error_free,
             'needed_manager_fix': l.needed_manager_fix,
             'justification': l.justification}
            for l in r.lines.all()
        ],
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def incentives(request):
    user = request.user
    submitter = can_submit(user)
    approver = is_approver(user)
    finance = is_finance(user)

    if request.method == 'GET':
        if not (submitter or approver or finance):
            return Response(
                {'detail': 'Incentive requests are available to managers, '
                           'approvers and Finance only.'},
                status=status.HTTP_403_FORBIDDEN)
        qs = (IncentiveRequest.objects
              .prefetch_related('lines')
              .select_related('maker'))
        if not (approver or finance):
            qs = qs.filter(maker=user)      # makers see only their own
        return Response({
            'me': {
                'can_submit': submitter,
                'can_approve': approver,
                'approver_slot': slot_for(user) or '',
                'is_finance': finance,
            },
            'requests': [_serialize(r, viewer=user) for r in qs[:200]],
        })

    # POST — submit a new request.
    if not submitter:
        return Response(
            {'detail': 'Only managers and above can submit incentive requests.'},
            status=status.HTTP_403_FORBIDDEN)
    data = request.data or {}
    try:
        req = submit_request(
            maker=user,
            title=data.get('title', ''),
            period=data.get('period', ''),
            department=data.get('department', ''),
            notes=data.get('notes', ''),
            lines=data.get('lines') or [],
            manager_attested=data.get('manager_attested', False),
        )
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    # Tell the maker who (if anyone) was held out for overdue tasks (CFO
    # 2026-08-18) — the eligible lines went through; the held ones did not.
    body = _serialize(req, viewer=user)
    held = getattr(req, 'held_lines', []) or []
    if held:
        body['held'] = held
        body['held_notice'] = (
            "Held out for overdue tasks (not added): "
            + ', '.join(h['name'] for h in held)
            + ". Clear the overdue work, then submit for them.")
    return Response(body, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def incentive_employees(request):
    """People the manager can pick for an incentive line, so each line links to
    a real employee and the discipline gate can check THAT person's tasks (CFO
    2026-08-18). Accessible to incentive submitters (managers) — deliberately
    NOT behind the HRIS whitelist, because the makers are line managers who are
    not whitelist members (same rationale as submit)."""
    if not can_submit(request.user):
        return Response({'detail': 'Only managers and above.'},
                        status=status.HTTP_403_FORBIDDEN)
    from payroll.models import Employee
    emps = (Employee.objects.filter(status='active')
            .order_by('full_name')
            .values('id', 'full_name', 'department'))
    return Response({'employees': [
        {'id': str(e['id']), 'name': e['full_name'],
         'department': e['department'] or ''}
        for e in emps if (e['full_name'] or '').strip()]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def parse_upload(request):
    """Upload a spreadsheet of people + amounts → return the rows for the request
    form (CFO / Bharath 2026-08-24). A typing shortcut for a long list: nothing
    is created here and NO gate is bypassed — the manager reviews the loaded rows,
    ticks the declaration, and Submit still runs the full earned-incentive gate.
    The file is parsed server-side and deleted; the data never leaves the box."""
    if not can_submit(request.user):
        return Response(
            {'detail': 'Only managers and above can upload an incentive list.'},
            status=status.HTTP_403_FORBIDDEN)
    import os
    import tempfile
    f = request.FILES.get('file')
    if not f:
        return Response(
            {'detail': 'No file chosen. Pick a spreadsheet with the people and '
                       'their amounts.'},
            status=status.HTTP_400_BAD_REQUEST)
    if getattr(f, 'size', 0) > 20 * 1024 * 1024:
        return Response({'detail': 'File too large (max 20 MB).'},
                        status=status.HTTP_400_BAD_REQUEST)
    suffix = os.path.splitext(f.name)[1].lower()
    if suffix not in ('.xlsx', '.xlsm', '.xlsb', '.xls', '.ods', '.csv'):
        return Response(
            {'detail': 'Upload a spreadsheet (.xlsx, .xlsm, .xls, .ods or .csv).'},
            status=status.HTTP_400_BAD_REQUEST)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        for chunk in f.chunks():
            tmp.write(chunk)
        tmp.close()
        from .incentive_import import parse_incentive_list
        rows = parse_incentive_list(tmp.name)
    except ValueError as exc:
        return Response({'detail': str(exc)},
                        status=status.HTTP_400_BAD_REQUEST)
    except Exception:                          # noqa: BLE001 — friendly, logged
        log.exception('incentive list upload failed to parse %r',
                      getattr(f, 'name', '?'))
        return Response(
            {'detail': 'Could not read that file. Re-save it as .xlsx or .csv '
                       'and try again, or type the lines by hand.'},
            status=status.HTTP_400_BAD_REQUEST)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return Response({'lines': rows, 'count': len(rows)})


def _get_or_404(incentive_id):
    return (IncentiveRequest.objects
            .prefetch_related('lines')
            .filter(pk=incentive_id)
            .first())


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve(request, incentive_id):
    req = _get_or_404(incentive_id)
    if req is None:
        return Response({'detail': 'Request not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    try:
        approve_request(req, request.user,
                        requested_slot=(request.data or {}).get('slot', ''))
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(req, viewer=request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject(request, incentive_id):
    req = _get_or_404(incentive_id)
    if req is None:
        return Response({'detail': 'Request not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    try:
        reject_request(req, request.user,
                       notes=(request.data or {}).get('notes', ''))
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(req, viewer=request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def payroll_mark(request, incentive_id):
    req = _get_or_404(incentive_id)
    if req is None:
        return Response({'detail': 'Request not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    try:
        mark_processed(req, request.user)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(req, viewer=request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def amend(request, incentive_id):
    """Correct a still-pending request's amount / line detail (CFO 2026-07-22).

    Body: {amount?, reason?, lines?:[{id, amount?, basis?, justification?, …}],
    title?, notes?, department?}. Authorisation + the pending/not-processed
    gate + revalidation + audit + signature-reset all live in the service.
    """
    if _get_or_404(incentive_id) is None:
        return Response({'detail': 'Request not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    data = request.data or {}
    try:
        req = amend_incentive(
            incentive_id, request.user,
            amount=data.get('amount'),
            reason=data.get('reason'),
            lines=data.get('lines'),
            title=data.get('title') if 'title' in data else None,
            notes=data.get('notes') if 'notes' in data else None,
            department=data.get('department') if 'department' in data else None,
        )
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(req, viewer=request.user))


# --- Recurring incentive templates -----------------------------------------
def _serialize_template(t) -> dict:
    return {
        'id': str(t.pk),
        'name': t.name,
        'category': t.category,
        'basis': t.basis,
        'amount': str(t.amount),
        'employee_id': str(t.employee_id) if t.employee_id else '',
        'department': t.department,
        'justification': t.justification,
        'note': t.note,
        'active': t.active,
        'created_by_email': t.created_by_email,
        'created_at': t.created_at.isoformat() if t.created_at else None,
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def recurring(request):
    user = request.user
    can_manage = can_manage_recurring(user)

    if request.method == 'GET':
        if not (can_manage or is_approver(user) or is_finance(user)):
            return Response(
                {'detail': 'Recurring incentives are available to managers, '
                           'approvers and Finance only.'},
                status=status.HTTP_403_FORBIDDEN)
        return Response({
            'can_manage': can_manage,
            'current_period': current_period(),
            'templates': [_serialize_template(t)
                          for t in list_recurring_templates(user)],
        })

    # POST — create a template.
    if not can_manage:
        return Response(
            {'detail': 'Only managers and above can manage recurring incentives.'},
            status=status.HTTP_403_FORBIDDEN)
    data = request.data or {}
    try:
        tpl = create_recurring_template(
            user=user,
            name=data.get('name', ''),
            amount=data.get('amount'),
            category=data.get('category', ''),
            basis=data.get('basis', ''),
            employee_id=data.get('employee_id') or None,
            department=data.get('department', ''),
            justification=data.get('justification', ''),
            note=data.get('note', ''),
        )
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize_template(tpl), status=status.HTTP_201_CREATED)


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def recurring_detail(request, template_id):
    """Toggle a template's active flag. DELETE (or POST {active:false})
    deactivates; POST {active:true} reactivates."""
    if request.method == 'DELETE':
        active = False
    else:
        raw = (request.data or {}).get('active', True)
        active = raw if isinstance(raw, bool) else \
            str(raw).strip().lower() in ('1', 'true', 'yes', 'on')
    try:
        tpl = set_recurring_active(template_id, request.user, active=active)
    except ValidationError as exc:
        msg = '; '.join(exc.messages)
        code = (status.HTTP_404_NOT_FOUND if 'not found' in msg.lower()
                else status.HTTP_400_BAD_REQUEST)
        return Response({'detail': msg}, status=code)
    return Response(_serialize_template(tpl))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def recurring_generate(request):
    """Create this month's (or the given period's) requests from active
    templates. Idempotent — already-generated (template, period) pairs skip."""
    period = (request.data or {}).get('period') or current_period()
    try:
        result = generate_recurring_for_period(period, request.user)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response({
        'period': result['period'],
        'created_count': result['created_count'],
        'skipped_count': result['skipped_count'],
        'created': [_serialize(r, viewer=request.user)
                    for r in result['created']],
    })
