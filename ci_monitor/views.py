"""The live gate board — what omni's gate is doing right now.

His own account only, the same gate as the build log: several chats build omni
at once and the board names branches and commit subjects, which is his working
picture and nobody else's.
"""
from __future__ import annotations

import datetime as dt

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.permissions import IsTheCfo
from ci_monitor.models import CiJob, CiRun, eta_seconds, typical_seconds

# How far back "recently finished" goes. Long enough to see what landed while
# he was away from the screen, short enough that the board is not a history page.
RECENT_HOURS = 6
MAX_RECENT = 12


def _job_row(job: CiJob) -> dict:
    typical = typical_seconds(job.name)
    elapsed = job.elapsed_seconds()
    left = eta_seconds(job)
    # Percentage is only ever an ESTIMATE of a running job, and it is capped at
    # 99: a bar that sits at 100% while the job is still going reads as stuck.
    pct = None
    if job.status == CiRun.Status.RUNNING and typical:
        pct = min(int(elapsed / typical * 100), 99)
    elif job.status != CiRun.Status.RUNNING and job.ended_at:
        pct = 100
    return {
        'name': job.name,
        'status': job.status,
        'elapsed_seconds': elapsed,
        'typical_seconds': typical,
        'seconds_left': left,
        'percent': pct,
        'url': job.url,
    }


def _run_row(run: CiRun) -> dict:
    jobs = [_job_row(j) for j in run.jobs.all()]
    # The run finishes when its SLOWEST job does, so the countdown is the
    # longest one left, never the sum and never the average.
    lefts = [j['seconds_left'] for j in jobs if j['seconds_left'] is not None]
    return {
        'run_id': run.run_id,
        'branch': run.branch,
        'title': run.title,
        'actor': run.actor,
        'event': run.event,
        'status': run.status,
        'url': run.url,
        'machine': run.machine,
        'started_at': run.started_at.isoformat() if run.started_at else None,
        'elapsed_seconds': run.elapsed_seconds(),
        'seconds_left': max(lefts) if lefts else None,
        'jobs': jobs,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTheCfo])
def board(request):
    """GET /api/v1/cfo/ci/ — everything building now, and what just landed."""
    live = (CiRun.objects
            .filter(status__in=[CiRun.Status.QUEUED, CiRun.Status.RUNNING])
            .prefetch_related('jobs'))
    since = timezone.now() - dt.timedelta(hours=RECENT_HOURS)
    recent = (CiRun.objects
              .filter(status__in=[CiRun.Status.SUCCESS, CiRun.Status.FAILURE,
                                  CiRun.Status.CANCELLED],
                      ended_at__gte=since)
              .prefetch_related('jobs')
              .order_by('-ended_at')[:MAX_RECENT])

    live_rows = [_run_row(r) for r in live]
    return Response({
        'now': timezone.localtime().isoformat(),
        'building_now': live_rows,
        'branches_building': len({r['branch'] for r in live_rows}),
        'recently_finished': [_run_row(r) for r in recent],
        # Stated rather than implied: an empty board because nothing is running
        # and an empty board because nothing has ever been recorded look
        # identical, and one of them means the webhook is not wired up.
        'ever_recorded': CiRun.objects.exists(),
        'recent_window_hours': RECENT_HOURS,
    })
