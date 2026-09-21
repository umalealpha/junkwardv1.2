"""Me → Signed-in devices (Omni staff phone app). List my live phone sessions,
switch one off. Only the owner's rows are ever visible; revoking somebody
else's id is a 404, not a 403, so ids cannot be probed."""
from __future__ import annotations

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.device_session_models import StaffDeviceSession


def _row(sess, current_pk):
    return {
        'id': str(sess.pk),
        'label': sess.device_label or 'Phone',
        'created_at': sess.created_at.isoformat(),
        'last_seen_at': sess.last_seen_at.isoformat() if sess.last_seen_at else None,
        'expires_at': sess.expires_at.isoformat(),
        'current': sess.pk == current_pk,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_devices(request):
    current_pk = getattr(request.auth, 'pk', None) if isinstance(request.auth, StaffDeviceSession) else None
    live = (StaffDeviceSession.objects.filter(user=request.user, revoked_at__isnull=True,
                                              expires_at__gt=timezone.now())
            .order_by('-last_seen_at', '-created_at'))
    return Response({'devices': [_row(s, current_pk) for s in live]})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def revoke_device(request, pk):
    sess = StaffDeviceSession.objects.filter(pk=pk, user=request.user).first()
    if sess is None:
        return Response({'detail': 'Not found.'}, status=404)
    sess.revoke(by=request.user)
    return Response({'revoked': True, 'id': str(sess.pk)})
