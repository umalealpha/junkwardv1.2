"""
healthcare/afa_views.py — Phase 5 of the ADH → AFA load file.

The screen's backend: today's queue, what is held and why, a masked preview,
the Release button, the history, and the iMed group mapping.

Every endpoint is gated by IsAfaLoadFileOperator, server-side, returning 403.
The preview MASKS ID numbers and bank accounts by default — an authorised
operator does not need to read 289 Omang numbers to decide whether to release
a file. `?unmasked=1` shows them for the one row being investigated and is
still behind the same permission.
"""
from __future__ import annotations

import logging
import re

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from healthcare import afa_loadfile as engine
from healthcare import afa_service, afa_sftp
from healthcare.afa_members import GraphiteReplicaNotConfigured
from healthcare.models import AfaGroupNameMap, AfaLoadFileRun
from healthcare.permissions import IsAfaLoadFileOperator

log = logging.getLogger('afa-loadfile')

_ID_COL   = engine.COLUMNS.index('IDNumber')
_PASS_COL = engine.COLUMNS.index('Passport')
_ACCT_COL = engine.COLUMNS.index('Account Number')
_DOB_COL  = engine.COLUMNS.index('Date of Birth')


def _mask(value: str, keep: int = 3) -> str:
    v = (value or '').strip()
    if len(v) <= keep:
        return '•' * len(v)
    return '•' * (len(v) - keep) + v[-keep:]


def _mask_line(line: str) -> str:
    parts = line.split(engine.DELIMITER)
    if len(parts) != len(engine.COLUMNS):
        return line
    for idx in (_ID_COL, _PASS_COL, _ACCT_COL):
        parts[idx] = _mask(parts[idx])
    parts[_DOB_COL] = re.sub(r'^\d{4}', '••••', parts[_DOB_COL])
    return engine.DELIMITER.join(parts)


def _run_json(run: AfaLoadFileRun, *, include_body: bool = False,
              unmasked: bool = False) -> dict:
    data = {
        'id': str(run.id),
        'runDate': run.run_date.isoformat(),
        'status': run.status,
        'statusLabel': run.get_status_display(),
        'rowCount': run.row_count,
        'newCount': run.new_count,
        'changedCount': run.changed_count,
        'departureCount': run.departure_count,
        'heldCount': run.held_count,
        'heldReasons': run.held_reasons or {},
        'fileName': run.file_name,
        'sha256': run.file_sha256,
        'sourceRowCount': run.source_row_count,
        'replicaLagSeconds': run.replica_lag_seconds,
        'abortReason': run.abort_reason,
        'builtAt': run.built_at.isoformat() if run.built_at else None,
        'releasedAt': run.released_at.isoformat() if run.released_at else None,
        'releasedBy': getattr(run.released_by, 'email', '') or '',
        'sentAt': run.sent_at.isoformat() if run.sent_at else None,
        'sendError': run.send_error,
        'ackStatus': run.ack_status,
        'autosendEnabled': afa_sftp.autosend_enabled(),
        'transferConfigured': afa_sftp.is_configured(),
    }
    if include_body and run.file_body:
        lines = run.file_body.strip().split('\n')
        body_lines = lines if unmasked else [lines[0]] + [_mask_line(l) for l in lines[1:]]
        data['preview'] = body_lines[:201]
        data['previewTruncated'] = len(body_lines) > 201
        data['masked'] = not unmasked
    return data


@api_view(['GET'])
@permission_classes([IsAfaLoadFileOperator])
def afa_runs(request):
    """The history — newest first."""
    runs = AfaLoadFileRun.objects.defer('file_body', 'sent_keys')[:60]
    return Response({
        'results': [_run_json(r) for r in runs],
        'autosendEnabled': afa_sftp.autosend_enabled(),
        'transferConfigured': afa_sftp.is_configured(),
    })


@api_view(['GET'])
@permission_classes([IsAfaLoadFileOperator])
def afa_run_detail(request, run_id):
    try:
        run = AfaLoadFileRun.objects.get(id=run_id)
    except (AfaLoadFileRun.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'That load file run was not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    unmasked = request.query_params.get('unmasked') in ('1', 'true', 'yes')
    return Response(_run_json(run, include_body=True, unmasked=unmasked))


@api_view(['POST'])
@permission_classes([IsAfaLoadFileOperator])
def afa_build(request):
    """Build (or rebuild) today's file. A released or sent run is never rebuilt."""
    try:
        run = afa_service.build_and_store()
    except GraphiteReplicaNotConfigured as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
    if run.status == AfaLoadFileRun.Status.ABORTED:
        return Response(_run_json(run), status=status.HTTP_409_CONFLICT)
    return Response(_run_json(run, include_body=True))


@api_view(['POST'])
@permission_classes([IsAfaLoadFileOperator])
def afa_release(request, run_id):
    """A person releases the file to AFA.

    This is the human gate. Until AFA confirm the open questions and the run
    has been clean for a fortnight, nothing reaches them without this click.
    """
    try:
        run = AfaLoadFileRun.objects.get(id=run_id)
    except (AfaLoadFileRun.DoesNotExist, ValueError, TypeError):
        return Response({'detail': 'That load file run was not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    if run.status == AfaLoadFileRun.Status.SENT:
        return Response({'detail': 'That file has already been delivered to AFA.'},
                        status=status.HTTP_409_CONFLICT)
    if run.status == AfaLoadFileRun.Status.ABORTED:
        return Response({'detail': f'That run was aborted and cannot be sent. {run.abort_reason}'},
                        status=status.HTTP_409_CONFLICT)
    if not run.row_count:
        return Response({'detail': 'There is nothing to send — the file has no rows.'},
                        status=status.HTTP_409_CONFLICT)

    # Record WHO released it, but do not move the status until the transfer
    # outcome is known. Flipping to RELEASED up front bricked the date: the
    # build refused to rebuild a released run, so with sending switched off
    # (the shipped default) the day could never be retried.
    run.released_by = request.user
    run.released_at = timezone.now()
    run.save(update_fields=['released_by', 'released_at'])

    try:
        # A person is clicking Release, so the unattended-cron switch does not
        # apply — but credentials still must exist.
        afa_sftp.send(run.file_name, run.file_body, unattended=False)
    except (afa_sftp.AfaSendDisabled, afa_sftp.AfaSendNotConfigured) as exc:
        # Not an error the operator caused. The file is built and recorded as
        # released; the transfer simply is not set up yet.
        return Response({
            'detail': ('The file is built and your release is recorded, but the '
                       'connection to AFA is not set up yet, so nothing has been '
                       'sent.'),
            'run': _run_json(run),
        }, status=status.HTTP_202_ACCEPTED)
    except afa_sftp.AfaSendUnknown as exc:
        run.status = AfaLoadFileRun.Status.FAILED
        run.send_error = str(exc)
        run.save(update_fields=['status', 'send_error'])
        log.error('afa release %s UNKNOWN outcome', run.run_date)
        return Response({'detail': str(exc)}, status=status.HTTP_409_CONFLICT)
    except afa_sftp.AfaSendFailed as exc:
        run.status = AfaLoadFileRun.Status.FAILED
        run.send_error = str(exc)
        run.save(update_fields=['status', 'send_error'])
        return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    run.status = AfaLoadFileRun.Status.SENT
    run.sent_at = timezone.now()
    run.send_error = ''
    run.save(update_fields=['status', 'sent_at', 'send_error'])

    try:
        afa_service.commit_snapshot(run)
    except afa_service.SnapshotNotRecorded as exc:
        # The file IS with AFA. Say so loudly rather than swallow it — an
        # uncommitted snapshot makes tomorrow re-send the whole membership.
        log.error('afa release %s: snapshot NOT advanced', run.run_date)
        return Response({
            'detail': f'The file reached AFA, but {exc}',
            'run': _run_json(run),
        }, status=status.HTTP_409_CONFLICT)

    return Response(_run_json(run))


@api_view(['GET', 'POST'])
@permission_classes([IsAfaLoadFileOperator])
def afa_groups(request):
    """The iMed group-name mapping — the thing that stops silent rejections."""
    if request.method == 'GET':
        return Response({'results': [{
            'id': str(m.id),
            'employerGroupId': m.employer_group_id,
            'graphiteName': m.graphite_name,
            'imedGroupName': m.imed_group_name,
            'regionName': m.region_name,
            'isActive': m.is_active,
            'billingContactId': str(m.billing_contact_id) if m.billing_contact_id else None,
            'billingContactName': getattr(m.billing_contact, 'name', '') or '',
            'confirmedBy': m.confirmed_by,
        } for m in AfaGroupNameMap.objects.select_related('billing_contact')]})

    payload = request.data or {}
    group_id = (payload.get('employerGroupId') or '').strip()
    imed_name = (payload.get('imedGroupName') or '').strip()
    if not group_id or not imed_name:
        return Response(
            {'detail': 'Please give both the Graphite employer group and the exact iMed group name.'},
            status=status.HTTP_400_BAD_REQUEST)

    defaults = {
        'imed_group_name': imed_name,
        'graphite_name': (payload.get('graphiteName') or '').strip(),
        'region_name': (payload.get('regionName') or '').strip(),
        'is_active': bool(payload.get('isActive', True)),
        'confirmed_by': (getattr(request.user, 'email', '') or ''),
        'confirmed_at': timezone.now(),
    }
    # The Omni contact invoiced for this group. Without it the payment gate can
    # never open and every new member in the group is held forever.
    if 'billingContactId' in payload:
        contact_id = payload.get('billingContactId') or None
        if contact_id:
            from billing.models import Contact
            if not Contact.objects.filter(pk=contact_id).exists():
                return Response({'detail': 'That billing contact does not exist.'},
                                status=status.HTTP_400_BAD_REQUEST)
        defaults['billing_contact_id'] = contact_id

    mapping, _ = AfaGroupNameMap.objects.update_or_create(
        employer_group_id=group_id, defaults=defaults,
    )
    return Response({'id': str(mapping.id), 'employerGroupId': mapping.employer_group_id,
                     'imedGroupName': mapping.imed_group_name})
