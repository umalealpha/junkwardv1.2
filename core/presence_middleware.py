"""
core/presence_middleware.py — auto-heartbeat OnlinePresence on every request.

Every authenticated HTTP hit bumps `OnlinePresence.last_seen` for the user.
Cost is a single UPDATE; we throttle to once-per-60-seconds via an in-memory
cache so a chatty SPA polling every 2s does not pound the DB.

The `presence_online_users()` helper (see api_views.py) queries this table
filtered to rows seen within the last 5 minutes — that is the "online now"
list rendered in the sidebar.
"""

from __future__ import annotations

import time
from typing import Optional

from django.utils.deprecation import MiddlewareMixin


_LAST_BUMP: dict = {}
_BUMP_THROTTLE_SEC = 60


class PresenceHeartbeatMiddleware(MiddlewareMixin):
    """Bump OnlinePresence.last_seen for the requesting user once a minute."""

    def process_response(self, request, response):
        try:
            user = getattr(request, 'user', None)
            if user is None or not user.is_authenticated:
                return response
            now = time.time()
            last = _LAST_BUMP.get(user.pk)
            if last and now - last < _BUMP_THROTTLE_SEC:
                return response
            _LAST_BUMP[user.pk] = now
            # Lazy import — avoids AppRegistryNotReady at boot.
            from django.utils import timezone
            from core.models import OnlinePresence
            OnlinePresence.objects.update_or_create(
                user=user, defaults={'last_seen': timezone.now()},
            )
        except Exception:
            # Never break a real request just because presence bump failed.
            pass
        return response
