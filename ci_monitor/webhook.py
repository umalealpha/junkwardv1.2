"""GitHub tells omni what the gate is doing. omni never asks GitHub.

A webhook rather than polling, deliberately: omni holds NO GitHub credential.
There is no token here to store, rotate or leak, and nothing in omni can reach
into the repository. The traffic is one-way and read-only in effect — GitHub
posts what already happened.

Authenticity is the whole security model, so it is not optional:
  * the request is signed by GitHub with a shared secret (X-Hub-Signature-256),
  * the signature is compared in CONSTANT TIME — a plain `==` leaks, one byte
    at a time, how much of a guess was right,
  * an unsigned, wrongly-signed, or unconfigured request is refused. If the
    secret is missing the endpoint refuses everything rather than falling open,
    because a build board that anyone on the internet can write to is a board
    that lies.

Only `workflow_run` and `workflow_job` are handled. Anything else is accepted
and ignored, so adding an event on GitHub can never 500 a delivery.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from ci_monitor.models import CiJob, CiRun

log = logging.getLogger(__name__)

# GitHub's own words -> ours. Anything unrecognised is treated as running
# rather than dropped: a run we cannot classify is still a run in flight, and
# showing it as "running" is closer to the truth than hiding it.
CONCLUSION = {
    'success': CiRun.Status.SUCCESS,
    'failure': CiRun.Status.FAILURE,
    'cancelled': CiRun.Status.CANCELLED,
    'timed_out': CiRun.Status.FAILURE,
    'startup_failure': CiRun.Status.FAILURE,
    'action_required': CiRun.Status.FAILURE,
    'neutral': CiRun.Status.SUCCESS,
    'skipped': CiRun.Status.SUCCESS,
}


def _status_of(payload: dict) -> str:
    if payload.get('status') in ('queued', 'waiting', 'pending'):
        return CiRun.Status.QUEUED
    if payload.get('status') != 'completed':
        return CiRun.Status.RUNNING
    return CONCLUSION.get(payload.get('conclusion') or '', CiRun.Status.FAILURE)


def _when(value) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def _signature_ok(request) -> bool:
    secret = getattr(settings, 'CI_WEBHOOK_SECRET', '') or ''
    if not secret:
        # Fail CLOSED. An unconfigured secret must never mean "let everyone in".
        log.warning('ci webhook: no CI_WEBHOOK_SECRET set — refusing delivery')
        return False
    sent = request.headers.get('X-Hub-Signature-256', '')
    if not sent.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(secret.encode(), request.body,
                                    hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, expected)


@csrf_exempt
@require_POST
def github_webhook(request):
    """POST /api/v1/ci/webhook/ — GitHub posts here as the gate moves."""
    if not _signature_ok(request):
        return HttpResponseForbidden('bad signature')

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse('unreadable payload', status=400)

    event = request.headers.get('X-GitHub-Event', '')
    if event == 'workflow_run':
        _save_run(payload)
    elif event == 'workflow_job':
        _save_job(payload)
    # Everything else: accepted and ignored on purpose. A new event type added
    # on GitHub must never turn into failing deliveries here.
    return HttpResponse('ok')


def _save_run(payload: dict) -> None:
    run = payload.get('workflow_run') or {}
    if not run.get('id'):
        return
    head = run.get('head_commit') or {}
    CiRun.objects.update_or_create(
        run_id=run['id'],
        defaults={
            'run_number': run.get('run_number') or 0,
            'workflow': (run.get('name') or '')[:120],
            'branch': (run.get('head_branch') or '')[:200],
            'head_sha': (run.get('head_sha') or '')[:40],
            # The commit subject, not the workflow name — he reads this to know
            # WHICH piece of work is building, and "CI — guardrails" says nothing.
            'title': ((head.get('message') or '').splitlines() or [''])[0][:300],
            'actor': ((run.get('actor') or {}).get('login') or '')[:120],
            'event': (run.get('event') or '')[:40],
            'url': (run.get('html_url') or '')[:200],
            'status': _status_of(run),
            'started_at': _when(run.get('run_started_at') or run.get('created_at')),
            'ended_at': _when(run.get('updated_at')) if run.get('status') == 'completed' else None,
        },
    )


def _save_job(payload: dict) -> None:
    job = payload.get('workflow_job') or {}
    if not job.get('id') or not job.get('run_id'):
        return
    # The job can arrive before its run — GitHub does not promise an order. A
    # placeholder keeps the job rather than dropping it; the run event fills in
    # the detail moments later.
    run, _ = CiRun.objects.get_or_create(
        run_id=job['run_id'],
        defaults={'branch': (job.get('head_branch') or '')[:200],
                  'status': CiRun.Status.RUNNING},
    )
    CiJob.objects.update_or_create(
        job_id=job['id'],
        defaults={
            'run': run,
            'name': (job.get('name') or '')[:120],
            'status': _status_of(job),
            'started_at': _when(job.get('started_at')),
            'ended_at': _when(job.get('completed_at')),
            'url': (job.get('html_url') or '')[:200],
        },
    )
