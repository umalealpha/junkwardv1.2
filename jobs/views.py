"""jobs/views.py — the scheduled-job dashboard. CFO's own account only."""
from __future__ import annotations

import datetime as dt

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsTheCfo, is_the_cfo


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTheCfo])
def jobs_list(request):
    """GET /api/v1/cfo/jobs/?state=on|off|protected|drift — every scheduled job."""
    from jobs.models import JobRun, ScheduledJob

    state = (request.query_params.get('state') or '').strip()
    qs = ScheduledJob.objects.all()
    rows = []
    last = {r['job_id']: r for r in JobRun.objects.order_by('job_id', '-started_at')
            .values('job_id', 'started_at', 'status').distinct('job_id')} \
        if _supports_distinct() else _last_runs()

    for j in qs:
        lr = last.get(j.id)
        row = {
            'name': j.name,
            'what_it_does': j.what_it_does,
            'who_it_affects': j.who_it_affects,
            'if_switched_off': j.if_switched_off,
            'category': j.category,
            'is_enabled': j.is_enabled,
            'is_protected': j.is_protected,
            'off_until': j.off_until,
            'off_reason': j.off_reason,
            'host_present': j.host_present,
            'host_disabled': j.host_disabled,
            'effective_on': j.effective_on,
            'why': j.why(),
            'last_run': lr['started_at'] if lr else None,
            'last_status': lr['status'] if lr else None,
            'drift': (j.is_enabled and (j.host_disabled or not j.host_present)),
        }
        if state == 'on' and not row['effective_on']:
            continue
        if state == 'off' and row['effective_on']:
            continue
        if state == 'protected' and not j.is_protected:
            continue
        if state == 'drift' and not row['drift']:
            continue
        rows.append(row)

    return Response({
        'jobs': rows,
        'counts': {
            'total': qs.count(),
            'running': sum(1 for j in qs if j.effective_on),
            'switched_off': sum(1 for j in qs if not j.is_enabled),
            'protected': qs.filter(is_protected=True).count(),
            'drift': sum(1 for j in qs
                         if j.is_enabled and (j.host_disabled or not j.host_present)),
        },
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, IsTheCfo])
def jobs_toggle(request):
    """POST /api/v1/cfo/jobs/toggle/ {name, on, reason, off_until}."""
    from jobs.models import ScheduledJob, ScheduledJobChange

    name = (request.data.get('name') or '').strip()
    job = ScheduledJob.objects.filter(name=name).first()
    if job is None:
        return Response({'detail': 'No such job.'}, status=status.HTTP_404_NOT_FOUND)

    on = bool(request.data.get('on'))
    # Protected jobs can never be switched OFF here — the switch is refused, and
    # the wrapper ignores the flag for them anyway. Belt and braces.
    if job.is_protected and not on:
        return Response(
            {'detail': f'{name} is protected and cannot be switched off.'},
            status=status.HTTP_403_FORBIDDEN)

    job.is_enabled = on
    if on:
        job.off_until = None
        job.off_reason = ''
    else:
        # Every "off" carries an end date — default a week — so nothing is
        # silently left off for nine days again.
        raw = (request.data.get('off_until') or '').strip()
        try:
            job.off_until = dt.date.fromisoformat(raw) if raw else \
                timezone.localdate() + dt.timedelta(days=7)
        except ValueError:
            job.off_until = timezone.localdate() + dt.timedelta(days=7)
        job.off_reason = (request.data.get('reason') or '')[:200]
    job.save(update_fields=['is_enabled', 'off_until', 'off_reason', 'updated_at'])

    ScheduledJobChange.objects.create(
        job=job, changed_by=(request.user.email or request.user.username)[:120],
        turned_on=on, reason=job.off_reason,
        at_ip=(request.META.get('REMOTE_ADDR') or '')[:64])
    return Response({'name': name, 'is_enabled': on, 'off_until': job.off_until,
                     'why': job.why()})


def _supports_distinct():
    from django.db import connection
    return connection.vendor == 'postgresql'


def _last_runs():
    from jobs.models import JobRun
    out = {}
    for r in JobRun.objects.order_by('-started_at').values('job_id', 'started_at', 'status'):
        out.setdefault(r['job_id'], r)
    return out
