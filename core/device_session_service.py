"""Issue a phone-app device session from the existing login flows.

A control keyed on a value with a fallback is no control (notebook, L6): the
client must send the LITERAL string 'phone' — anything else, including case or
whitespace variants, gets the desktop 15h token as before.
"""
from __future__ import annotations

from core.device_session_models import StaffDeviceSession


def wants_device_session(data) -> bool:
    try:
        return data.get('device') == 'phone'
    except AttributeError:
        return False


def mint_device_session(user, request) -> dict:
    label = str(request.data.get('device_label') or '')[:120]
    ua = request.META.get('HTTP_USER_AGENT', '')
    raw, _row = StaffDeviceSession.issue(user, device_label=label, user_agent=ua)
    return {'token': raw, 'token_type': 'device', 'expires_days': 30}
