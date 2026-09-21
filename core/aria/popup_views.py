"""
core/aria/popup_views.py — API endpoints for Aria popup messages.

GET  /api/v1/aria/popups/        — unread popups for the current user
POST /api/v1/aria/popups/<id>/read/  — mark a popup as read
"""

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def unread_popups(request):
    from core.models import AriaPopup
    popups = AriaPopup.objects.filter(
        recipient=request.user, is_read=False,
    ).order_by('created_at')[:5]

    return Response({
        'popups': [
            {
                'id': str(p.id),
                'message': p.message,
                'sender': p.sender.get_full_name() if p.sender else 'Aria',
                'created_at': p.created_at.isoformat(),
            }
            for p in popups
        ]
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_popup_read(request, popup_id):
    from core.models import AriaPopup
    try:
        popup = AriaPopup.objects.get(id=popup_id, recipient=request.user)
    except AriaPopup.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)

    popup.is_read = True
    popup.read_at = timezone.now()
    popup.save(update_fields=['is_read', 'read_at'])
    return Response({'ok': True})
