"""core/session_teardown.py — end someone's live Omni sessions.

One home for "actually sign this person out", because there are two session
types and forgetting either one leaves a closed account still working:

  * the browser token (core.token_auth) lives 15 hours;
  * the phone session (core.device_auth) lives 30 days.

Both are checked against `auth.User.is_active` on every request, so flipping
that flag is what really ends access — these helpers exist so the audit trail
can say truthfully how many live sessions were cut, and so a token cannot
survive in the table after the account is closed.

Kept in `core` rather than in `payroll` because both the HR leaver sweep
(payroll.offboard_access) and the Users admin screen (core.models.UserProfile)
need it, and a shared helper living inside one caller is how eight copies of
the same function happened before.
"""
from __future__ import annotations

from django.utils import timezone


def _device_session_model():
    try:
        from core.device_session_models import StaffDeviceSession
        return StaffDeviceSession
    except Exception:                                    # noqa: BLE001
        return None


def live_device_session_count(user) -> int:
    model = _device_session_model()
    if model is None:
        return 0
    return model.objects.filter(user=user, revoked_at__isnull=True,
                                expires_at__gt=timezone.now()).count()


def revoke_device_sessions(user, now=None) -> int:
    """Revoke only sessions that are still LIVE.

    An already-expired session needs no revoking, and counting it would make
    an audit row claim we ended a phone session that had already ended. The
    filter matches live_device_session_count() exactly so a dry-run figure and
    a real figure can never disagree.
    """
    model = _device_session_model()
    if model is None:
        return 0
    now = now or timezone.now()
    return model.objects.filter(user=user, revoked_at__isnull=True,
                                expires_at__gt=now).update(revoked_at=now)


def browser_token_count(user) -> int:
    from rest_framework.authtoken.models import Token
    return Token.objects.filter(user=user).count()


def clear_browser_tokens(user) -> int:
    from rest_framework.authtoken.models import Token
    deleted, _ = Token.objects.filter(user=user).delete()
    return deleted


def end_all_sessions(user, now=None) -> dict:
    """Cut every live session for this user. Returns what was actually ended."""
    now = now or timezone.now()
    return {
        'device_sessions_revoked': revoke_device_sessions(user, now),
        'browser_tokens_cleared': clear_browser_tokens(user),
    }
