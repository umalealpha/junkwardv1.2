"""reinsurance/history_views.py — 11-Year Historical Treaty Performance.

CFO directive 2026-08-14. Serves the LOCKED 11-year reinsurance performance
dataset that powers the /reinsurance/history board dashboard.

Design (exactly the CFO's three requirements):
  • Locked & permanent — read is open to authenticated staff; there is NO delete
    endpoint, so the dataset can never be wiped from the UI (by staff or the AI).
  • Amend behind a password — an amendment re-authenticates the caller's OWN
    password (the same login they use for HRIS) AND requires the HRIS/CFO gate.
  • Snapshotted — every amendment first copies the current figures into an
    immutable ReinsuranceHistorySnapshot, so the old numbers are never lost.

All three actions are written to the AuditLog.
"""
import logging

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from .models import ReinsuranceHistory, ReinsuranceHistorySnapshot

log = logging.getLogger(__name__)


def _can_amend(user) -> bool:
    """Who may amend: superuser (the CFO) or anyone with the HRIS gate — the
    same population the CFO means by 'same password as HRIS'."""
    return bool(user and user.is_authenticated and (user.is_superuser or user_can_access_hris(user)))


def _audit(request, action, description, old=None, new=None):
    try:
        from core.models import AuditLog
        AuditLog.objects.create(
            table_name='reinsurance_history',
            record_id=ReinsuranceHistory.SINGLETON_KEY,
            action=action,
            old_values=old, new_values=new,
            user=request.user if request.user.is_authenticated else None,
            ip_address=(request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
                        or request.META.get('REMOTE_ADDR') or '')[:45],
            description=description,
        )
    except Exception:    # noqa: BLE001 — never block the request on an audit failure
        log.exception('reinsurance_history: audit write failed (%s)', action)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reinsurance_history(request):
    """Return the current locked dataset + lock state + amend stamp."""
    obj = ReinsuranceHistory.current()
    if obj is None:
        return Response({
            'exists': False,
            'detail': 'The 11-year history has not been loaded yet. Run seed_reinsurance_history.',
        }, status=404)
    return Response({
        'exists': True,
        'locked': obj.locked,
        'version': obj.version,
        'data': obj.data,
        'source_note': obj.source_note,
        'last_amended_by': (obj.last_amended_by.get_full_name() or obj.last_amended_by.username)
                           if obj.last_amended_by else None,
        'last_amended_at': obj.last_amended_at.isoformat() if obj.last_amended_at else None,
        'can_amend': _can_amend(request.user),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reinsurance_history_amend(request):
    """Amend the dataset. Requires: HRIS/CFO gate + the caller's own password.
    Snapshots the current figures first, then applies the new ones."""
    user = request.user
    if not _can_amend(user):
        return Response({'detail': 'You are not allowed to amend the reinsurance history.'}, status=403)

    password = (request.data.get('password') or '').strip()
    if not password or not user.check_password(password):
        # Same message either way — never reveal which half failed (timing/oracle).
        return Response({'detail': 'Password incorrect. The history was not changed.'}, status=403)

    new_data = request.data.get('data')
    if not isinstance(new_data, dict) or not new_data:
        return Response({'detail': 'No valid data supplied.'}, status=400)

    note = (request.data.get('note') or '').strip()[:300]

    obj = ReinsuranceHistory.current()
    if obj is None:
        return Response({'detail': 'Dataset not initialised — run the seeder first.'}, status=404)

    # 1) snapshot the CURRENT figures before overwriting (nothing is ever lost)
    ReinsuranceHistorySnapshot.objects.create(
        version=obj.version, data=obj.data, amended_by=user,
        note=f'Superseded by v{obj.version + 1}. {note}'.strip(),
    )
    old = obj.data
    # 2) apply
    obj.data = new_data
    obj.version += 1
    obj.last_amended_by = user
    obj.last_amended_at = timezone.now()
    if note:
        obj.source_note = note
    obj.save()

    _audit(request, 'update',
           f'Reinsurance 11-year history amended to v{obj.version} by {user.username}.',
           old=old, new=new_data)
    return Response({'ok': True, 'version': obj.version,
                     'last_amended_at': obj.last_amended_at.isoformat()})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reinsurance_history_snapshots(request):
    """List the past versions (who changed it, when, and the note)."""
    rows = ReinsuranceHistorySnapshot.objects.all()[:50]
    return Response({'snapshots': [{
        'version': s.version,
        'amended_by': (s.amended_by.get_full_name() or s.amended_by.username) if s.amended_by else None,
        'amended_at': s.amended_at.isoformat(),
        'note': s.note,
    } for s in rows]})
