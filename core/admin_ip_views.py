"""
core/admin_ip_views.py — self-service admin IP lock.

CFO directive 2026-05-19 (Manus Final Verification Audit § 4): the Django
admin panel is exposed to the public internet. Instead of asking a
non-technical CFO to find his office IP, ship a one-click endpoint that
captures his current public IP and locks /admin/ to it.

Flow:
  1. CFO opens https://omni.alphadirect.co.bw/api/v1/admin/lock-to-my-ip/
     while signed in.
  2. Endpoint requires `request.user.is_superuser`. Anything else → 403.
  3. Reads the leftmost X-Forwarded-For IP (the real client; Cloudflare
     and the AWS ALB append after it).
  4. Inserts an AdminAllowlistEntry(cidr='<ip>/32', label='self-locked
     by <username> at <ts>') if no row exists for that IP, or
     re-activates it.
  5. Returns JSON {ip, cidr, locked, total_active}.

The AdminIPAllowlistMiddleware refreshes its DB-derived view every 30 s,
so the lock takes effect on the next admin request without a container
restart.

Other endpoints:
  GET  /api/v1/admin/allowlist/        — list active entries (superuser only)
  POST /api/v1/admin/allowlist/clear/  — wipe all entries (superuser only,
                                          unblocks if CFO got locked out)
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _is_superuser(user) -> bool:
    return bool(user and user.is_authenticated and user.is_superuser)


def _client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or ''


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def lock_to_my_ip(request):
    """
    Lock /admin/ access to the calling client's IP. Superuser only.
    Accepts GET so the CFO can click it as a plain link from his browser.
    """
    if not _is_superuser(request.user):
        return Response(
            {'detail': 'Superuser required.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    from core.models import AdminAllowlistEntry

    ip = _client_ip(request)
    if not ip:
        return Response(
            {'detail': 'Could not determine client IP.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    cidr = f'{ip}/32'
    label = f'self-locked by {request.user.username} at {timezone.now().isoformat()}'
    obj, created = AdminAllowlistEntry.objects.update_or_create(
        cidr=cidr,
        defaults={
            'label':      label,
            'is_active':  True,
            'created_by': request.user,
        },
    )
    total = AdminAllowlistEntry.objects.filter(is_active=True).count()
    return Response({
        'ip':           ip,
        'cidr':         cidr,
        'locked':       True,
        'was_created':  created,
        'total_active': total,
        'note':         (
            'Allowlist refreshes every ~30s. Try /admin/ in a new tab. '
            'If you get a 404, your office IP changed — hit this URL '
            'again to re-lock.'
        ),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_allowlist(request):
    if not _is_superuser(request.user):
        return Response({'detail': 'Superuser required.'},
                        status=status.HTTP_403_FORBIDDEN)
    from core.models import AdminAllowlistEntry
    rows = list(AdminAllowlistEntry.objects.filter(is_active=True)
                .values('cidr', 'label', 'created_at', 'created_by__username'))
    return Response({'entries': rows, 'count': len(rows)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def clear_allowlist(request):
    """Emergency unlock — superuser only. Disables every active row."""
    if not _is_superuser(request.user):
        return Response({'detail': 'Superuser required.'},
                        status=status.HTTP_403_FORBIDDEN)
    from core.models import AdminAllowlistEntry
    n = AdminAllowlistEntry.objects.filter(is_active=True).update(is_active=False)
    return Response({'cleared': n})
