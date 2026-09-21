"""rewards/customer_auth.py — lightweight auth for the customer app.

Customers are RewardMembers, not Django Users, so the staff SSO/DRF auth stack
does not apply. This module issues email one-time codes and bearer-token
sessions that map a token back to exactly ONE RewardMember — the foundation of
per-customer data isolation (a customer must never see another's data).

Only HASHES of codes/tokens are stored. Codes are short-lived, attempt-limited
and single-use. Nothing here is logged with a raw code/token or a name.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import CustomerLoginCode, CustomerSession

OTP_TTL_MINUTES = 10
SESSION_TTL_DAYS = 30
MAX_OTP_ATTEMPTS = 5
OTP_RATE_PER_HOUR = 5  # max codes requested per member per hour


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def make_otp() -> str:
    """A 6-digit numeric code (cryptographically random, zero-padded)."""
    return f'{secrets.randbelow(1_000_000):06d}'


def make_token() -> str:
    return secrets.token_urlsafe(32)


def recent_code_count(member) -> int:
    """How many codes this member requested in the last hour (rate limit)."""
    since = timezone.now() - timedelta(hours=1)
    return CustomerLoginCode.objects.filter(member=member, created_at__gte=since).count()


def issue_code(member) -> str:
    """Create a login code for the member; return the RAW code (to email)."""
    code = make_otp()
    CustomerLoginCode.objects.create(
        member=member,
        code_hash=_sha(code),
        expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES),
    )
    return code


def verify_code(member, code: str) -> bool:
    """Check + consume any of the member's still-valid unconsumed codes.

    Checks EVERY unexpired, unconsumed code (not just the newest one). A user
    who taps "resend" then types the code from the FIRST email would otherwise
    be locked out, because the old view only ever looked at the most recent
    code — a common "the code doesn't work" complaint.
    """
    # App Store / Play review demo account: Apple's (and Google's) reviewer must
    # sign in, but Alpha Nexus uses email-OTP and the reviewer can't receive our
    # code. So one designated review email accepts a fixed code, given to the
    # reviewer in the App Review notes. Enabled ONLY when both env vars are set
    # (prod /etc/alpha-finance/.env); review-only, remove after approval if wanted.
    import os
    rev_email = (os.environ.get('NEXUS_REVIEW_EMAIL', '') or '').strip().lower()
    rev_otp   = (os.environ.get('NEXUS_REVIEW_OTP', '') or '').strip()
    if rev_email and rev_otp and (getattr(member, 'email', '') or '').strip().lower() == rev_email:
        return secrets.compare_digest(str(code or '').strip(), rev_otp)

    now = timezone.now()
    supplied_hash = _sha(str(code or ''))
    recs = (CustomerLoginCode.objects
            .filter(member=member, consumed=False, expires_at__gte=now)
            .order_by('-created_at')[:OTP_RATE_PER_HOUR])
    matched = None
    for rec in recs:
        if rec.attempts >= MAX_OTP_ATTEMPTS:
            continue
        rec.attempts += 1
        if secrets.compare_digest(rec.code_hash, supplied_hash):
            rec.consumed = True
            matched = rec
        rec.save(update_fields=['attempts', 'consumed', 'updated_at'])
        if matched is not None:
            break
    return matched is not None


def start_session(member) -> str:
    """Open a session for the member; return the RAW bearer token (to client)."""
    token = make_token()
    CustomerSession.objects.create(
        member=member,
        token_hash=_sha(token),
        expires_at=timezone.now() + timedelta(days=SESSION_TTL_DAYS),
    )
    return token


def resolve_member(request):
    """Map an `Authorization: Bearer <token>` header to a RewardMember, or None."""
    auth = request.META.get('HTTP_AUTHORIZATION', '') or ''
    if not auth.startswith('Bearer '):
        return None
    token = auth[len('Bearer '):].strip()
    if not token:
        return None
    sess = (CustomerSession.objects
            .filter(token_hash=_sha(token), revoked=False)
            .select_related('member').first())
    if sess is None or sess.expires_at < timezone.now():
        return None
    return sess.member


def revoke_session(request) -> bool:
    auth = request.META.get('HTTP_AUTHORIZATION', '') or ''
    if not auth.startswith('Bearer '):
        return False
    token = auth[len('Bearer '):].strip()
    n = CustomerSession.objects.filter(token_hash=_sha(token)).update(revoked=True)
    return n > 0


def customer_required(view):
    """Decorator: require a valid customer session; inject request.customer_member."""
    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        member = resolve_member(request)
        if member is None:
            return Response({'detail': 'Not signed in.'}, status=status.HTTP_401_UNAUTHORIZED)
        request.customer_member = member
        return view(request, *args, **kwargs)
    return _wrapped
