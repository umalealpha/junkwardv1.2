"""
core/dpa_checklist.py — monthly COMPULSORY DPO checklist (Graphite + omni).

The DPO (Oratile) completes + submits it every month. C-suite + Finance Mgr can
see it on the Data Protection dashboard. An OVERDUE run sits there as an action
item and the C-suite can convert it to a disciplinary action in one click.
CFO directive 2026-07-19.
"""
from __future__ import annotations

import calendar
import datetime

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.dpa_dashboard import can_view_dpa_dashboard, is_dpo

# Fixed monthly template — Graphite + omni + both.
CHECKLIST_ITEMS = [
    {'key': 'g_access',    'system': 'Graphite', 'question': 'Reviewed Graphite access — no ex-staff / ex-contractors still have access; least privilege holds.'},
    {'key': 'g_kyc',       'system': 'Graphite', 'question': 'KYC / Omang documents in Graphite storage stayed access-restricted; no unmasked bulk export left the system.'},
    {'key': 'g_dsr',       'system': 'Graphite', 'question': 'Any Graphite data-subject requests (access / correction / erasure) were logged and actioned in time.'},
    {'key': 'o_access',    'system': 'Omni',     'question': 'Reviewed omni users & roles — least privilege, no access drift; leavers removed.'},
    {'key': 'o_shield',    'system': 'Omni',     'question': 'AI privacy shield confirmed ON (tokenize) — no raw personal data sent to any external AI.'},
    {'key': 'o_retention', 'system': 'Omni',     'question': 'Records past their retention period were flagged / purged / anonymised.'},
    {'key': 'x_breach',    'system': 'Both',     'question': 'Any data breach or incident this month was logged, and (if reportable) the IDPC was notified within 72 hours.'},
    {'key': 'x_xborder',   'system': 'Both',     'question': 'No new cross-border processor without a data-processing agreement; existing transfers unchanged.'},
    {'key': 'x_notice',    'system': 'Both',     'question': 'Privacy notices (staff + policyholder) are current and accessible.'},
    {'key': 'x_dsr',       'system': 'Both',     'question': 'Staff / customer data-subject requests were handled within the statutory time limit.'},
]
_ITEM_KEYS = {i['key'] for i in CHECKLIST_ITEMS}
VALID_RESPONSES = {'yes', 'no', 'na'}


def _last_day(period: str) -> datetime.date:
    y, m = int(period[:4]), int(period[5:7])
    return datetime.date(y, m, calendar.monthrange(y, m)[1])


def get_or_create_current(today=None):
    from core.models import DpoChecklistRun
    from django.utils import timezone
    today = today or timezone.localdate()
    period = f'{today.year:04d}-{today.month:02d}'
    run, _ = DpoChecklistRun.objects.get_or_create(
        period=period, defaults={'due_date': _last_day(period)})
    return run


def sweep(today=None) -> dict:
    """Create this month's run + mark any unsubmitted past-due run OVERDUE."""
    from core.models import DpoChecklistRun
    from django.utils import timezone
    today = today or timezone.localdate()
    cur = get_or_create_current(today)
    newly = []
    for run in DpoChecklistRun.objects.exclude(status=DpoChecklistRun.Status.SUBMITTED):
        if run.status != DpoChecklistRun.Status.OVERDUE and today > run.due_date:
            run.status = DpoChecklistRun.Status.OVERDUE
            run.save(update_fields=['status', 'updated_at'])
            newly.append(run.period)
    return {'current': cur.period, 'newly_overdue': newly}


def _run_dict(run, *, editable):
    resp = run.responses or {}
    return {
        'id': run.id, 'period': run.period, 'status': run.status,
        'due_date': run.due_date.isoformat(),
        'submitted_at': run.submitted_at.isoformat() if run.submitted_at else None,
        'editable': editable,
        'disciplinary_raised': bool(run.disciplinary_task_id),
        'items': [{**it, 'response': (resp.get(it['key']) or {}).get('response', ''),
                   'note': (resp.get(it['key']) or {}).get('note', '')}
                  for it in CHECKLIST_ITEMS],
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def checklist(request):
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Restricted.'}, status=403)
    from core.models import DpoChecklistRun
    run = get_or_create_current()
    dpo = is_dpo(request.user)
    editable = dpo and run.status != DpoChecklistRun.Status.SUBMITTED
    history = [{'id': r.id, 'period': r.period, 'status': r.status,
                'due_date': r.due_date.isoformat(),
                'submitted_at': r.submitted_at.isoformat() if r.submitted_at else None,
                'disciplinary_raised': bool(r.disciplinary_task_id)}
               for r in DpoChecklistRun.objects.all()[:12]]
    overdue = [_run_dict(r, editable=False)
               for r in DpoChecklistRun.objects.filter(status=DpoChecklistRun.Status.OVERDUE)]
    return Response({'is_dpo': dpo, 'current': _run_dict(run, editable=editable),
                     'overdue': overdue, 'history': history})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def checklist_save(request):
    """DPO saves / submits. body: {responses:{key:{response,note}}, submit:bool}."""
    if not is_dpo(request.user):
        return Response({'detail': 'Only the DPO may complete the checklist.'}, status=403)
    from core.models import DpoChecklistRun
    from django.utils import timezone
    run = get_or_create_current()
    if run.status == DpoChecklistRun.Status.SUBMITTED:
        return Response({'detail': 'This month is already submitted.'}, status=400)
    incoming = request.data.get('responses') or {}
    merged = dict(run.responses or {})
    for k, v in incoming.items():
        if k in _ITEM_KEYS and isinstance(v, dict):
            r = str(v.get('response', '')).lower()
            merged[k] = {'response': r if r in VALID_RESPONSES else '',
                         'note': str(v.get('note', ''))[:500]}
    run.responses = merged
    if request.data.get('submit'):
        missing = [i['key'] for i in CHECKLIST_ITEMS
                   if (merged.get(i['key']) or {}).get('response') not in VALID_RESPONSES]
        if missing:
            run.save(update_fields=['responses', 'updated_at'])
            return Response({'detail': f'Answer all {len(CHECKLIST_ITEMS)} items before submitting '
                                       f'({len(missing)} still blank).'}, status=400)
        run.status = DpoChecklistRun.Status.SUBMITTED
        run.submitted_at = timezone.now()
        run.submitted_by = request.user
        run.save(update_fields=['responses', 'status', 'submitted_at', 'submitted_by', 'updated_at'])
    else:
        run.save(update_fields=['responses', 'updated_at'])
    return Response({'status': run.status})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def checklist_discipline(request, pk):
    """C-suite converts an OVERDUE run to a disciplinary action (one click)."""
    from hris.document_access import _is_csuite
    if not (_is_csuite(request.user) or getattr(request.user, 'is_superuser', False)):
        return Response({'detail': 'Only the C-suite may raise a disciplinary action.'}, status=403)
    from core.models import DpoChecklistRun, OmniTask
    from django.contrib.auth import get_user_model
    from django.utils import timezone
    run = DpoChecklistRun.objects.filter(pk=pk).first()
    if not run:
        return Response({'detail': 'Not found.'}, status=404)
    if run.status != DpoChecklistRun.Status.OVERDUE:
        return Response({'detail': 'Only an overdue checklist can be escalated.'}, status=400)
    if run.disciplinary_task_id:
        return Response({'detail': 'A disciplinary action was already raised.',
                         'task_id': run.disciplinary_task_id}, status=400)
    U = get_user_model()
    hr_head = U.objects.filter(email__istartswith='ubutale').first()
    task = OmniTask.objects.create(
        assigner=request.user, assignee=(hr_head or request.user),
        title=f'Disciplinary — DPO missed the {run.period} data-protection checklist'[:200],
        body=(f'The compulsory monthly data-protection checklist for {run.period} (Graphite + omni) '
              f'was not submitted by the DPO by its due date ({run.due_date}). Raised from the Data '
              f'Protection dashboard by {request.user.get_full_name() or request.user.username}. '
              f'Action: initiate the disciplinary process per policy.')[:5000],
        due_at=timezone.localdate() + datetime.timedelta(days=3),
        priority=OmniTask.Priority.HIGH,
        status=OmniTask.Status.PENDING,
    )
    run.disciplinary_task = task
    run.save(update_fields=['disciplinary_task', 'updated_at'])
    return Response({'ok': True, 'task_id': task.id})
