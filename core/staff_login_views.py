"""core/staff_login_views.py — staff email + password + email-OTP login.

CFO directive 2026-07-07: a sign-in that does NOT depend on Microsoft SSO,
usable by ALL active Alpha Direct staff (broader than admin-only /break-glass).

Flow (3 steps):
  1. POST /api/v1/auth/staff/start   {email, password}
       - email must be an active Alpha Direct M365 user (allow-list)
       - password checked (everyone seeded 'Omni123' first)
       - a 6-digit code is emailed; returns {otp_sent: true}
  2. POST /api/v1/auth/staff/verify  {email, code}
       - code checked (hashed, <=10 min, single-use, <=5 attempts)
       - still on the default password -> {change_required: true, ticket}
       - otherwise                    -> {token}
  3. POST /api/v1/auth/staff/change  {email, ticket, new_password}
       - ticket proves step 2 passed; sets the new password; returns {token}

Hardening: allow-list gate (M365 users only), Django-hashed passwords,
hashed + expiring + single-use OTP, per-IP rate limit (10/min), audited.
The emailed code is the second factor: a known/default password alone cannot
sign anyone in — the person must also control the Alpha Direct mailbox.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password as _check_hash, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.utils import timezone
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes, throttle_classes)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework.throttling import SimpleRateThrottle

from core.device_session_service import mint_device_session, wants_device_session
from core.review_demo import is_review_user, review_code, review_email, review_mode_on
from core.models import AuditLog, EmailLoginCode

log = logging.getLogger('staff-login')
User = get_user_model()

DEFAULT_PASSWORD = 'Omni123'
CODE_TTL_MIN = 10
MAX_ATTEMPTS = 5
MIN_PW_LEN = 8
_SALT = 'staff-login-otp-passed'


# Precomputed once — a real hash to run on reject paths so a rejected request
# spends about the same time as a real check_password (blunts timing-oracle
# user enumeration).
_DUMMY_HASH = make_password('timing-equaliser')


def _client_ip(request) -> str:
    # Cloudflare fronts omni and sets CF-Connecting-IP to the real client; it
    # cannot be spoofed past Cloudflare. NEVER trust X-Forwarded-For here (the
    # client controls it — that let the throttle be bypassed).
    return (request.META.get('HTTP_CF_CONNECTING_IP')
            or request.META.get('REMOTE_ADDR') or '')


class LoginThrottle(SimpleRateThrottle):
    """Per-real-client throttle on the 'login' rate (10/min), FBV-safe.

    Keys on the Cloudflare-resolved client IP, not the spoofable XFF."""
    scope = 'login'

    def get_cache_key(self, request, view):
        return f'throttle_stafflogin_{_client_ip(request) or "anon"}'


def _norm(email) -> str:
    return str(email or '').strip().lower()


def _burn_timing(password) -> None:
    """Run one hash so reject-before-password paths take ~the same time."""
    try:
        _check_hash(str(password or ''), _DUMMY_HASH)
    except Exception:
        pass


# Alpha Direct GROUP staff domains — genuine people who work for the group
# (CFO confirmed 2026-07-07/08). Individual humans on these can use staff
# login; shared/service boxes on them are still excluded by the denylist.
# NOTE: an 'adrisk' domain was named too but does not yet exist in the system
# — add it here once the exact domain is confirmed.
_GROUP_DOMAINS = (
    '@alphadirect.co.bw', '@alphadirect.co.zm', '@alphadirect.co.za',
    '@insurance.co.bw', '@theriskco.com', '@quantum.co.bw',
    '@motorliquidators.co.bw',
)
# Shared / forwarding / distribution / service mailboxes — NOT individual
# humans. They must never get a staff login (CFO directive 2026-07-07).
_SHARED_LOCALPARTS = {
    'hc', 'people', 'health', 'info', 'admin', 'claims', 'claimsdept',
    'accounts', 'accountsdept', 'accountsdepartment', 'noreply', 'no-reply',
    'support', 'helpdesk', 'sales', 'finance', 'marketing', 'team', 'group',
    'excoboard', 'hr', 'it', 'reception', 'careers', 'billing', 'payments',
    'notifications', 'alerts', 'underwriting', 'reinsurance', 'omni',
    'internalauditors', 'auditors', 'audit',
}


def _is_human_ad_email(email: str) -> bool:
    """A real individual Alpha Direct group human mailbox — not a shared/
    forwarding/service box. Must be on a group domain (@alphadirect.co.bw or
    @insurance.co.bw)."""
    e = _norm(email)
    if not any(e.endswith(d) for d in _GROUP_DOMAINS):
        return False
    local = e.split('@', 1)[0]
    if local in _SHARED_LOCALPARTS or local.startswith('svc-') or 'dept' in local:
        return False
    return True


def _is_alpha_direct_user(email: str) -> bool:
    """True only for an ACTIVE, individual Alpha Direct human who is either an
    active M365 user or an existing staff account. Shared/forwarding/service
    mailboxes and non-alphadirect domains are always rejected."""
    e = _norm(email)
    if not _is_human_ad_email(e):
        return False
    try:
        from licensing.models import M365ActiveUser
        if (M365ActiveUser.objects.filter(email__iexact=e).exists()
                or M365ActiveUser.objects.filter(user_principal_name__iexact=e).exists()):
            return True
    except Exception:
        log.exception('M365 allow-list check failed')
    # Any active provisioned account on a group domain (the domain list is
    # controlled and the shared/service denylist already applied; the emailed
    # OTP to the person's own mailbox is the real second gate).
    return User.objects.filter(email__iexact=e, is_active=True).exists()


def _get_user(email: str):
    """The one active account for this email, or None.

    SEC-10 (security assessment 2026-08-06): this used to take the LOWEST-id
    match, so where an email exists twice — which has happened here — sign-in
    silently picked the older record. That is a coin toss over which permissions
    the person gets, and the wrong side of it once posted a real journal. Refuse
    the ambiguity instead: the caller treats None as "no account", which fails
    closed, and IT resolves the duplicate.
    """
    matches = list(User.objects.filter(email__iexact=_norm(email), is_active=True)
                   .order_by('id')[:2])
    if len(matches) > 1:
        log.error('staff-login REFUSED: %s matches more than one active account — '
                  'IT must merge the duplicate before this person can sign in.',
                  _norm(email))
        return None
    return matches[0] if matches else None


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _audit(request, email: str, *, ok: bool, reason: str = '') -> None:
    try:
        A = AuditLog.Action
        AuditLog.objects.create(
            table_name=User._meta.db_table, record_id='',
            action=getattr(A, 'UPDATE', list(A)[0]), user=None,
            ip_address=_client_ip(request)[:45],
            description=(f"Staff email-login {'OK' if ok else 'DENY'} "
                         f"{email} {reason} ip={_client_ip(request)}")[:250],
        )
    except Exception:
        log.exception('staff-login audit write failed')


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def staff_login_start(request):
    email = _norm(request.data.get('email'))
    password = str(request.data.get('password') or '')
    # One generic message for every credential failure — never reveal which
    # part was wrong or whether the email exists.
    generic = Response(
        {'detail': 'Wrong email or password, or this account is not set up for email sign-in.'},
        status=400)
    if not email or not password:
        return Response({'detail': 'Enter your email and password.'}, status=400)
    # App-store reviewer (CFO 2026-09-06): a Play/App Store reviewer cannot read
    # our mailbox, so this ONE identity skips the emailed code — no EmailLoginCode
    # row, no mail. Inert unless OMNI_REVIEW_MODE is armed; every other email
    # falls through to the normal flow below, unchanged.
    if review_mode_on() and email == review_email():
        review_user = _get_user(email)
        if not review_user or not is_review_user(review_user):
            _burn_timing(password)
            _audit(request, email, ok=False, reason='store-review-not-the-review-identity')
            return generic
        if not review_user.check_password(password):
            _burn_timing(password)
            _audit(request, email, ok=False, reason='store-review-bad-password')
            return generic
        _audit(request, email, ok=True, reason='store-review-otp-skipped')
        return Response({'otp_sent': True})
    user = _get_user(email)
    # Short-circuit paths still spend a hash (_burn_timing) so an outsider
    # can't tell a real staff email from a stranger by response time.
    if not _is_alpha_direct_user(email) or not user or not user.has_usable_password():
        _burn_timing(password)
        _audit(request, email, ok=False, reason='not-allowlisted-or-no-pw')
        return generic
    if not user.check_password(password):
        _audit(request, email, ok=False, reason='bad-password')
        return generic

    P = EmailLoginCode.Purpose
    EmailLoginCode.objects.filter(email=email, consumed=False,
                                  purpose=P.SIGN_IN).update(consumed=True)
    code = f'{secrets.randbelow(1_000_000):06d}'
    EmailLoginCode.objects.create(
        email=email, code_hash=_hash(code), purpose=P.SIGN_IN,
        expires_at=timezone.now() + timedelta(minutes=CODE_TTL_MIN))
    try:
        send_mail(
            subject='Your Omni sign-in code',
            message=(f'Your Omni sign-in code is {code}\n\n'
                     f'It expires in {CODE_TTL_MIN} minutes. '
                     f'If you did not try to sign in, ignore this email.'),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[user.email or email], fail_silently=False)
    except Exception:
        log.exception('OTP email send failed')
        return Response({'detail': 'Could not send the code email — try again shortly.'}, status=502)
    _audit(request, email, ok=True, reason='otp-sent')
    return Response({'otp_sent': True})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def staff_login_verify(request):
    email = _norm(request.data.get('email'))
    code = str(request.data.get('code') or '').strip()
    if not email or not code:
        return Response({'detail': 'Enter the code from your email.'}, status=400)
    # App-store reviewer (CFO 2026-09-06): the fixed code from the environment
    # stands in for the emailed one. Constant-time compare, so it cannot be
    # guessed a character at a time. Every other email is untouched.
    if review_mode_on() and email == review_email():
        if not secrets.compare_digest(code, review_code()):
            _audit(request, email, ok=False, reason='store-review-bad-code')
            return Response({'detail': 'Wrong code — try again.'}, status=400)
        review_user = _get_user(email)
        if not review_user or not is_review_user(review_user):
            _audit(request, email, ok=False, reason='store-review-not-the-review-identity')
            return Response({'detail': 'Account not found.'}, status=400)
        if wants_device_session(request.data):
            _audit(request, email, ok=True, reason='store-review-signed-in-phone')
            return Response({**mint_device_session(review_user, request),
                             'change_required': False})
        # Deliberately NOT a DRF token: the review identity must hold exactly one
        # credential shape, so the demo middleware can never be bypassed.
        _audit(request, email, ok=True, reason='store-review-signed-in-phone-only')
        return Response({**mint_device_session(review_user, request),
                         'change_required': False})
    # SEC-05: a code issued for a password RESET must not sign anyone in.
    rec = (EmailLoginCode.objects.filter(email=email, consumed=False,
                                         purpose=EmailLoginCode.Purpose.SIGN_IN)
           .order_by('-created_at').first())
    if not rec or rec.expires_at < timezone.now():
        return Response({'detail': 'That code has expired — request a new one.'}, status=400)
    if rec.attempts >= MAX_ATTEMPTS:
        rec.consumed = True
        rec.save(update_fields=['consumed'])
        return Response({'detail': 'Too many wrong codes — start again.'}, status=400)
    if _hash(code) != rec.code_hash:
        rec.attempts += 1
        rec.save(update_fields=['attempts'])
        return Response({'detail': 'Wrong code — try again.'}, status=400)

    rec.consumed = True
    rec.save(update_fields=['consumed'])
    user = _get_user(email)
    if not user:
        return Response({'detail': 'Account not found.'}, status=400)

    if user.check_password(DEFAULT_PASSWORD):
        ticket = TimestampSigner(salt=_SALT).sign(email)
        _audit(request, email, ok=True, reason='otp-ok-change-required')
        return Response({'change_required': True, 'ticket': ticket})

    if wants_device_session(request.data):
        _audit(request, email, ok=True, reason='signed-in-phone')
        return Response({**mint_device_session(user, request), 'change_required': False})
    token, _ = Token.objects.get_or_create(user=user)
    _audit(request, email, ok=True, reason='signed-in')
    return Response({'token': token.key, 'change_required': False})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def staff_login_change(request):
    email = _norm(request.data.get('email'))
    ticket = str(request.data.get('ticket') or '')
    new_pw = str(request.data.get('new_password') or '')
    try:
        signed_email = TimestampSigner(salt=_SALT).unsign(ticket, max_age=CODE_TTL_MIN * 60)
    except (BadSignature, SignatureExpired):
        return Response({'detail': 'Your sign-in step expired — start again.'}, status=400)
    if _norm(signed_email) != email:
        return Response({'detail': 'Sign-in mismatch — start again.'}, status=400)
    user = _get_user(email)
    if not user:
        return Response({'detail': 'Account not found.'}, status=400)
    if len(new_pw) < MIN_PW_LEN:
        return Response({'detail': f'New password must be at least {MIN_PW_LEN} characters.'}, status=400)
    if new_pw.strip().lower() == DEFAULT_PASSWORD.lower():
        return Response({'detail': 'Choose a password different from the default.'}, status=400)
    try:
        validate_password(new_pw, user)   # Django's configured strength rules
    except DjangoValidationError as ve:
        return Response({'detail': ' '.join(ve.messages)}, status=400)
    user.set_password(new_pw)
    user.save(update_fields=['password'])
    Token.objects.filter(user=user).delete()   # rotate any prior token
    if wants_device_session(request.data):
        _audit(request, email, ok=True, reason='password-changed-phone')
        return Response(mint_device_session(user, request))
    token = Token.objects.create(user=user)
    _audit(request, email, ok=True, reason='password-changed')
    return Response({'token': token.key})


# ---------------------------------------------------------------------------
# Forgot password (CFO directive 2026-07-20): a self-service reset for staff
# who set their own password and then forgot it. The normal /start needs the
# current password, so a forgotten one has no way back short of an admin. This
# adds a 2-step reset that does NOT need the old password — the emailed code
# (mailbox control) is the gate, exactly like the sign-in second factor.
#   1. POST /api/v1/auth/staff/forgot-start  {email}            -> emails a code
#   2. POST /api/v1/auth/staff/forgot-reset  {email, code, new_password} -> {token}
# Reuses EmailLoginCode + the same OTP hardening (hashed, <=10 min, single-use,
# <=5 attempts, per-IP throttle, audited) and the same password rules as change.
# ---------------------------------------------------------------------------
@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def staff_login_forgot_start(request):
    """Forgot-password step 1: email a reset code WITHOUT the old password.
    Always returns a generic ok so an outsider cannot probe which emails exist;
    a code is only actually sent to an allow-listed, active staff mailbox."""
    email = _norm(request.data.get('email'))
    generic = Response({'otp_sent': True})   # identical reply whether or not the email exists
    if not email:
        return Response({'detail': 'Enter your work email.'}, status=400)
    user = _get_user(email)
    # Reject-before-send paths still spend a hash so timing can't enumerate users.
    if not _is_alpha_direct_user(email) or not user:
        _burn_timing('reset-no-user')
        _audit(request, email, ok=False, reason='forgot-not-allowlisted')
        return generic

    P = EmailLoginCode.Purpose
    EmailLoginCode.objects.filter(email=email, consumed=False,
                                  purpose=P.RESET).update(consumed=True)
    code = f'{secrets.randbelow(1_000_000):06d}'
    EmailLoginCode.objects.create(
        email=email, code_hash=_hash(code), purpose=P.RESET,
        expires_at=timezone.now() + timedelta(minutes=CODE_TTL_MIN))
    try:
        send_mail(
            subject='Your Omni password reset code',
            message=(f'Your Omni password reset code is {code}\n\n'
                     f'It expires in {CODE_TTL_MIN} minutes. '
                     f'If you did not ask to reset your password, ignore this email '
                     f'— your current password still works.'),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[user.email or email], fail_silently=False)
    except Exception:
        log.exception('reset OTP email send failed')
        return Response({'detail': 'Could not send the code email — try again shortly.'}, status=502)
    _audit(request, email, ok=True, reason='forgot-otp-sent')
    return generic


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([LoginThrottle])
def staff_login_forgot_reset(request):
    """Forgot-password step 2: check the emailed code, then set a brand-new
    password (no old password needed) and sign the person in. A weak/invalid
    new password does NOT burn the code — only a wrong code costs an attempt —
    so the person can fix the password and retry the same code."""
    email = _norm(request.data.get('email'))
    code = str(request.data.get('code') or '').strip()
    new_pw = str(request.data.get('new_password') or '')
    if not email or not code:
        return Response({'detail': 'Enter the code from your email.'}, status=400)
    # SEC-05: a sign-in code must not be spendable on a password change.
    rec = (EmailLoginCode.objects.filter(email=email, consumed=False,
                                         purpose=EmailLoginCode.Purpose.RESET)
           .order_by('-created_at').first())
    if not rec or rec.expires_at < timezone.now():
        return Response({'detail': 'That code has expired — request a new one.'}, status=400)
    if rec.attempts >= MAX_ATTEMPTS:
        rec.consumed = True
        rec.save(update_fields=['consumed'])
        return Response({'detail': 'Too many wrong codes — start again.'}, status=400)
    if _hash(code) != rec.code_hash:
        rec.attempts += 1
        rec.save(update_fields=['attempts'])
        return Response({'detail': 'Wrong code — try again.'}, status=400)

    # Code is valid. Validate the new password BEFORE consuming the code, so a
    # weak password is a friendly retry rather than forcing a whole new code.
    user = _get_user(email)
    if not user:
        return Response({'detail': 'Account not found.'}, status=400)
    if len(new_pw) < MIN_PW_LEN:
        return Response({'detail': f'New password must be at least {MIN_PW_LEN} characters.'}, status=400)
    if new_pw.strip().lower() == DEFAULT_PASSWORD.lower():
        return Response({'detail': 'Choose a password different from the default.'}, status=400)
    try:
        validate_password(new_pw, user)   # Django's configured strength rules
    except DjangoValidationError as ve:
        return Response({'detail': ' '.join(ve.messages)}, status=400)

    rec.consumed = True
    rec.save(update_fields=['consumed'])
    user.set_password(new_pw)
    user.save(update_fields=['password'])
    Token.objects.filter(user=user).delete()   # rotate any prior token
    if wants_device_session(request.data):
        _audit(request, email, ok=True, reason='password-reset-phone')
        return Response(mint_device_session(user, request))
    token = Token.objects.create(user=user)
    _audit(request, email, ok=True, reason='password-reset')
    return Response({'token': token.key})
