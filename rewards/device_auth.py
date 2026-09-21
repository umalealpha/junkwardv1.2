"""rewards/device_auth.py — device pairing + step-sync token for Alpha Nexus v10.

Two-tier trust (CFO 16-Aug-2026): the 30-day email-OTP session (customer_auth.py)
runs the app; a SEPARATE device token authorises ONE thing — posting real steps
read from Health Connect / Apple Health by the paired phone.

Flow:
  1. web page (valid CustomerSession) -> POST /pair/start
        backend mints a single-use pairing code under THAT member's session.
  2. the code rides an intent:// link into the native PairingActivity.
  3. PairingActivity -> POST /pair/complete {code}
        backend swaps the code for a device token (member known from step 1).
  4. PairingActivity reads steps, -> POST /steps/sync (Bearer <device token>).

The device token is honoured ONLY by the step-sync endpoint — never by
resolve_member() — so a stolen token can, at worst, post capped step counts,
never read customer data. Only hashes are stored; nothing logs a raw value.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import CustomerDevicePairingCode, CustomerDeviceToken

PAIR_CODE_TTL_MINUTES = 5
PAIR_RATE_PER_HOUR = 5          # pairing codes per member per hour
MAX_DEVICES_PER_MEMBER = 5      # a member's active paired phones


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _make_code() -> str:
    """A high-entropy pairing code (URL-safe, not a guessable 6-digit)."""
    return secrets.token_urlsafe(18)


def recent_pair_code_count(member) -> int:
    since = timezone.now() - timedelta(hours=1)
    return CustomerDevicePairingCode.objects.filter(member=member, created_at__gte=since).count()


def issue_pairing_code(member) -> str:
    """Mint a single-use pairing code for the member; return the RAW code."""
    code = _make_code()
    CustomerDevicePairingCode.objects.create(
        member=member,
        code_hash=_sha(code),
        expires_at=timezone.now() + timedelta(minutes=PAIR_CODE_TTL_MINUTES),
    )
    return code


def complete_pairing(code: str, device_label: str = '') -> str | None:
    """Swap a valid pairing code for a device token. Return the RAW token or None.

    Direct lookup by hash of the supplied code — the code is a 144-bit
    token_urlsafe(18), not a guessable 6-digit, so a per-member scan is
    unnecessary and would (a) let junk POSTs brick every member's outstanding
    code and (b) time as an oracle. Consume atomically: only the request whose
    UPDATE flips consumed 0→1 (rowcount == 1) wins, so a single code can never
    mint two tokens under a race (Fable v10 review).
    """
    now = timezone.now()
    supplied = _sha(str(code or ''))
    rec = (CustomerDevicePairingCode.objects
           .filter(code_hash=supplied, consumed=False, expires_at__gte=now)
           .select_related('member').first())
    if rec is None:
        return None
    claimed = (CustomerDevicePairingCode.objects
               .filter(pk=rec.pk, consumed=False)
               .update(consumed=True))
    if claimed != 1:
        return None  # lost the race — another request consumed it first

    member = rec.member
    token = secrets.token_urlsafe(32)
    CustomerDeviceToken.objects.create(
        member=member, token_hash=_sha(token), device_label=str(device_label or '')[:80])

    # Cap active devices: revoke the oldest beyond the limit.
    active = (CustomerDeviceToken.objects
              .filter(member=member, revoked=False).order_by('-created_at'))
    stale_ids = list(active.values_list('id', flat=True)[MAX_DEVICES_PER_MEMBER:])
    if stale_ids:
        CustomerDeviceToken.objects.filter(id__in=stale_ids).update(revoked=True)
    return token


def resolve_device(request):
    """Map an `Authorization: Bearer <device token>` to a member, or None.

    Deliberately distinct from resolve_member(): this checks the device-token
    table only, so a device token can never satisfy customer_required().
    """
    auth = request.META.get('HTTP_AUTHORIZATION', '') or ''
    if not auth.startswith('Bearer '):
        return None
    token = auth[len('Bearer '):].strip()
    if not token:
        return None
    tok = (CustomerDeviceToken.objects
           .filter(token_hash=_sha(token), revoked=False)
           .select_related('member').first())
    if tok is None:
        return None
    tok.last_used_at = timezone.now()
    tok.save(update_fields=['last_used_at', 'updated_at'])
    return tok.member


def revoke_member_devices(member) -> int:
    """Revoke all of a member's device tokens (call on logout / account delete)."""
    return CustomerDeviceToken.objects.filter(member=member, revoked=False).update(revoked=True)


def device_required(view):
    """Decorator: require a valid DEVICE token; inject request.device_member.

    Never accepts a normal customer session token — the two token stores are
    separate, so this endpoint cannot be reached with a web session bearer.
    """
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        member = resolve_device(request)
        if member is None:
            return Response({'detail': 'This device is not paired.'},
                            status=status.HTTP_401_UNAUTHORIZED)
        request.device_member = member
        return view(request, *args, **kwargs)
    return _wrapped
