"""core/chat_views.py — in-app team chat (polled, single 'general' room).

CFO directive 2026-06-09: a small chatroom that pops up on login so logged-in
users can chat; a "/task @user <desc>" message also creates an OmniTask for
that user (drives their task inbox / dashboard). No websockets — omni is WSGI,
so the frontend polls GET /chat/messages/?since=<iso>. Presence is already
free via PresenceHeartbeatMiddleware + /presence/online/.
"""
from __future__ import annotations

import datetime as dt
import re

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

ROOM = 'general'
# /task @username rest-of-line   (the @ is optional)
_TASK_RE = re.compile(r'^/task\s+@?([^\s]+)\s+(.+)$', re.S | re.I)


def _dm_room(a_id: int, b_id: int) -> str:
    """Canonical 1:1 room key for a pair of users — order-independent so both
    parties compute the same key. Reuses the existing ChatMessage.room column,
    so private DMs need no new table (CFO 2026-07-13: 'team should be able to
    communicate privately')."""
    lo, hi = sorted((int(a_id), int(b_id)))
    return f'dm:{lo}:{hi}'


def _resolve_room(request, src):
    """Work out which room this request is for.

    `src` is request.query_params (GET) or request.data (POST). If it names a
    `dm` target (username or user-id), the room is derived from the CURRENT
    user + that target — so a caller can ONLY ever address a room they are
    themselves a member of. There is no way to pass a raw room string and read
    two OTHER people's private conversation.

    Returns (room, peer) where peer is {username, full_name} for a DM (else
    None), or (None, None) when a named target does not exist → 404.
    """
    dm = ((src or {}).get('dm') or '').strip()
    if not dm:
        return ROOM, None
    User = get_user_model()
    target = User.objects.filter(username__iexact=dm, is_active=True).first()
    if target is None and dm.isdigit():
        target = User.objects.filter(pk=int(dm), is_active=True).first()
    if target is None:
        return None, None
    peer = {'username': target.username,
            'full_name': target.get_full_name() or target.username}
    return _dm_room(request.user.id, target.id), peer


def _ser(m) -> dict:
    deleted = getattr(m, 'is_deleted', False)
    return {
        'id':          str(m.id),
        'sender':      m.sender.username,
        'sender_name': m.sender.get_full_name() or m.sender.username,
        'body':        '' if deleted else m.body,
        'is_task':     m.is_task,
        'task_id':     str(m.task_id) if m.task_id else None,
        'created_at':  m.created_at.isoformat(),
        'edited':      bool(getattr(m, 'edited_at', None)),
        'deleted':     deleted,
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def chat_messages(request):
    from core.models import ChatMessage

    if request.method == 'GET':
        room, peer = _resolve_room(request, request.query_params)
        if room is None:
            return Response({'detail': 'No such user to message.'}, status=404)
        qs = ChatMessage.objects.filter(room=room).select_related('sender')
        since = (request.query_params.get('since') or '').strip()
        rows = None
        if since:
            try:
                ts = dt.datetime.fromisoformat(since.replace('Z', '+00:00'))
                rows = list(qs.filter(created_at__gt=ts).order_by('created_at')[:300])
            except ValueError:
                rows = None
        if rows is None:
            rows = list(qs.order_by('-created_at')[:50])
            rows.reverse()
        return Response({
            'messages':    [_ser(m) for m in rows],
            'server_time': timezone.now().isoformat(),
            'peer':        peer,
        })

    # POST — send a message (may be a /task command)
    from core.models import ChatMessage, OmniTask
    User = get_user_model()
    body = ((request.data or {}).get('body') or '').strip()
    if not body:
        return Response({'detail': 'body is required.'}, status=400)

    room, _peer = _resolve_room(request, request.data)
    if room is None:
        return Response({'detail': 'No such user to message.'}, status=404)

    task = None
    is_task = False
    m = _TASK_RE.match(body)
    if m:
        uname, desc = m.group(1).strip(), m.group(2).strip()
        assignee = (User.objects.filter(username__iexact=uname, is_active=True).first()
                    or User.objects.filter(is_active=True)
                        .filter(Q(first_name__iexact=uname) | Q(email__istartswith=uname + '@'))
                        .first())
        if assignee is None:
            return Response({'detail': f'No active user "{uname}" to assign.'}, status=404)
        task = OmniTask.objects.create(
            assigner=request.user, assignee=assignee,
            title=desc[:200],
            body=f'Created from team chat by @{request.user.username}.',
            priority=OmniTask.Priority.NORMAL,
            status=OmniTask.Status.PENDING,
        )
        is_task = True
        body = f'📋 Task for @{assignee.username}: {desc}'

    msg = ChatMessage.objects.create(
        room=room, sender=request.user, body=body[:4000],
        is_task=is_task, task=task,
    )
    return Response(_ser(msg), status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def chat_message_detail(request, pk):
    """Edit (PATCH) or soft-delete (DELETE) a single chat message.
    Bug e79e4166. Only the original sender may edit/delete their own message
    (staff/superusers may also delete, for moderation). Soft-delete keeps the
    row + any linked task; the body is hidden in _ser()."""
    from core.models import ChatMessage
    try:
        # No room filter: DM messages live in per-pair rooms, not 'general'.
        # Access is enforced below (sender-only edit; mod-delete never reaches
        # into a private DM the moderator is not a party to).
        m = ChatMessage.objects.select_related('sender').get(pk=pk)
    except ChatMessage.DoesNotExist:
        return Response({'detail': 'Message not found.'}, status=404)

    is_sender = (m.sender_id == request.user.id)
    is_dm     = str(m.room or '').startswith('dm:')
    is_mod    = bool(getattr(request.user, 'is_staff', False) or getattr(request.user, 'is_superuser', False))

    if request.method == 'DELETE':
        # Moderators may delete in the shared room, but a private DM can only be
        # deleted by the person who wrote it — never by a superuser reaching in.
        if not (is_sender or (is_mod and not is_dm)):
            return Response({'detail': 'You can only delete your own messages.'}, status=403)
        if not m.is_deleted:
            m.is_deleted = True
            m.save(update_fields=['is_deleted', 'updated_at'])
        return Response(_ser(m))

    # PATCH — edit body (sender only)
    if not is_sender:
        return Response({'detail': 'You can only edit your own messages.'}, status=403)
    if m.is_deleted:
        return Response({'detail': 'Cannot edit a deleted message.'}, status=400)
    new_body = ((request.data or {}).get('body') or '').strip()
    if not new_body:
        return Response({'detail': 'body is required.'}, status=400)
    m.body = new_body[:4000]
    m.edited_at = timezone.now()
    m.save(update_fields=['body', 'edited_at', 'updated_at'])
    return Response(_ser(m))
