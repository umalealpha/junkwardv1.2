"""integrations/claim_status_views.py — PUBLIC "check my claim status" line.

A customer enters a claim number → we email a 6-digit code to the address ON
FILE for that claim (never a caller-supplied one) → they enter the code → we
return that claim's status. No login, no account.

Security / DPA (mirrors rewards/customer_auth.py + customer_views.py):
  * The claim number is the first factor (the customer must know it).
  * The code goes ONLY to the email already on file for the claim's customer —
    never to an address supplied by the caller. This blocks the "knows a claim
    number → attach my own email → take over" class of attack.
  * Same generic response whether or not the claim exists / has an email, so the
    endpoint never confirms a claim's existence.
  * Codes are hashed, single-use, attempt-limited, short-lived; endpoints are
    AllowAny but per-IP throttled.
  * Only the status (+ type / logged date) is ever returned — no money, no PII.
  * Email delivery only (WhatsApp is a later channel). Broker-shared PHONE was a
    known risk (integrations/management/commands/graphite_phone_distribution.py);
    email is the safer first channel and is what ships here.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta

from django.utils import timezone
from rest_framework import status as http
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes, throttle_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from .models import ClaimStatusCode
from .graphite_ro import query

OTP_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
CODES_PER_CLAIM_PER_HOUR = 5

# Plain-English status wording for the customer (raw Graphite enum -> friendly).
STATUS_LABEL = {
    'Pending':  'Received — under review',
    'Open':     'Open — being worked on',
    'Approved': 'Approved',
    'Reopen':   'Reopened — being reviewed again',
    'Rejected': 'Not approved',
    'Closed':   'Closed',
}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _make_otp() -> str:
    return f'{secrets.randbelow(1_000_000):06d}'


def _client_ip(request) -> str:
    xff = request.META.get('HTTP_CF_CONNECTING_IP') or request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()[:45]
    return (request.META.get('REMOTE_ADDR') or '')[:45]


class ClaimStatusThrottle(SimpleRateThrottle):
    """Per-IP throttle for the public claim-status endpoints (rate =
    DEFAULT_THROTTLE_RATES['claim_status']). Fixed scope so it works on
    function-based views."""
    scope = 'claim_status'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


def _lookup_claim(claim_number: str):
    """Look up ONE claim by its number on the live Graphite replica.

    Returns a dict {claim_number, status, claim_type, logged_date, email,
    first_name} or None. Read-only SELECT, parameterised (no injection).
    """
    sql = (
        "SELECT c.claim_number AS claim_number, c.status AS status, "
        "c.claim_type AS claim_type, c.created_at AS logged_date, "
        "cu.email AS email, cu.firstName AS first_name "
        "FROM claims c "
        "JOIN policies p ON c.policy_id = p.id "
        "JOIN customer cu ON p.customer_id = cu.id "
        "WHERE c.claim_number = %s "
        "ORDER BY c.id DESC LIMIT 1"
    )
    rows = list(query(sql, [claim_number]))
    return rows[0] if rows else None


def _email_code(email: str, name: str, claim_number: str, code: str) -> None:
    """Email the 6-digit code to the on-file address (customer mail — no CFO CC)."""
    from core.notifications import send_html_with_cfo_cc
    safe_name = (name or 'there').strip() or 'there'
    subject = 'Your Alpha Direct claim status code'
    html = f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto;color:#1D3270">
      <div style="background:#1D3270;padding:20px 24px;border-radius:12px 12px 0 0">
        <span style="color:#fff;font-size:18px;font-weight:700">Alpha Direct Insurance</span>
      </div>
      <div style="border:1px solid #eee;border-top:none;padding:24px;border-radius:0 0 12px 12px">
        <p>Hi {safe_name},</p>
        <p>Here is your one-time code to view the status of claim
           <b>{claim_number}</b>:</p>
        <p style="font-size:32px;font-weight:800;letter-spacing:6px;color:#F47C20;margin:18px 0">{code}</p>
        <p>It expires in {OTP_TTL_MINUTES} minutes. If you did not request this,
           you can ignore this email.</p>
        <p style="color:#64748B;font-size:12px;margin-top:24px">
           Alpha Direct Insurance Company (Pty) Ltd, Botswana.</p>
      </div>
    </div>"""
    text = (f'Your one-time code to view claim {claim_number} is {code}. '
            f'It expires in {OTP_TTL_MINUTES} minutes.')
    send_html_with_cfo_cc(subject, html, [email], text_fallback=text, cc_cfo=False, no_reply=True)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ClaimStatusThrottle])
def claim_request_code(request):
    """POST /api/v1/public/claim/request-code/  Body: {claim_number}

    Emails a 6-digit code to the address on file for the claim. Always returns
    the same generic message (never reveals whether the claim exists)."""
    claim_number = str((request.data or {}).get('claim_number') or '').strip()
    if not claim_number:
        return Response({'detail': 'claim_number is required.'}, status=http.HTTP_400_BAD_REQUEST)

    generic = Response({
        'ok': True,
        'message': ("If a claim with that number exists and has an email on file, "
                    "we've sent a 6-digit code to it. Check your inbox (and spam)."),
    })

    try:
        row = _lookup_claim(claim_number)
    except Exception:
        return generic  # never leak backend errors on a public endpoint

    if row and row.get('email'):
        since = timezone.now() - timedelta(hours=1)
        recent = ClaimStatusCode.objects.filter(
            claim_number__iexact=claim_number, created_at__gte=since,
        ).count()
        if recent < CODES_PER_CLAIM_PER_HOUR:
            code = _make_otp()
            ClaimStatusCode.objects.create(
                claim_number=claim_number,
                email=row['email'],
                code_hash=_sha(code),
                expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES),
                sent_ip=_client_ip(request),
            )
            try:
                _email_code(row['email'], row.get('first_name'), claim_number, code)
            except Exception:
                pass  # a mail hiccup must not change the generic response
    return generic


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ClaimStatusThrottle])
def claim_verify_code(request):
    """POST /api/v1/public/claim/verify-code/  Body: {claim_number, code}

    On a correct, unexpired, unused code returns the claim's status."""
    data = request.data or {}
    claim_number = str(data.get('claim_number') or '').strip()
    code = str(data.get('code') or '').strip()
    if not claim_number or not code:
        return Response({'detail': 'claim_number and code are required.'},
                        status=http.HTTP_400_BAD_REQUEST)

    now = timezone.now()
    supplied = _sha(code)
    recs = (ClaimStatusCode.objects
            .filter(claim_number__iexact=claim_number, consumed=False, expires_at__gte=now)
            .order_by('-created_at')[:CODES_PER_CLAIM_PER_HOUR])
    ok = False
    for rec in recs:
        if rec.attempts >= MAX_ATTEMPTS:
            continue
        rec.attempts += 1
        if secrets.compare_digest(rec.code_hash, supplied):
            rec.consumed = True
            ok = True
        rec.save(update_fields=['attempts', 'consumed', 'updated_at'])
        if ok:
            break

    if not ok:
        return Response({'detail': 'That code is invalid or has expired.'},
                        status=http.HTTP_401_UNAUTHORIZED)

    try:
        row = _lookup_claim(claim_number)
    except Exception:
        row = None
    if not row:
        return Response({'detail': 'Claim not found.'}, status=http.HTTP_404_NOT_FOUND)

    raw_status = row.get('status') or ''
    logged = row.get('logged_date')
    return Response({
        'claim_number': row.get('claim_number'),
        'status': raw_status,
        'status_label': STATUS_LABEL.get(raw_status, raw_status or 'Unknown'),
        'claim_type': row.get('claim_type'),
        'logged_date': str(logged)[:10] if logged else None,
    })


# ===========================================================================
# CFO twin 2-factor login (Fable review fix): the Digital CFO's "Private Access"
# now needs BOTH the CFO password AND a 6-digit code emailed to the CFO's own
# address before it will hand over individual pay / leave / medical data.
# Reuses ClaimStatusCode (sentinel claim_number keys) — no new table.
# ===========================================================================
CFO_CODE_KEY = '__cfo_2fa_code__'        # rows holding the emailed login code
CFO_SESSION_KEY = '__cfo_2fa_session__'  # rows holding an issued session token
CFO_SESSION_TTL_HOURS = 8


def _cfo_config():
    return (
        (os.environ.get('TWIN_CFO_PASSWORD') or 'CFO2026Private'),
        (os.environ.get('TWIN_CFO_EMAIL') or 'pganesharajah@alphadirect.co.bw'),
    )


def _email_cfo_login_code(email: str, code: str) -> None:
    from core.notifications import send_html_with_cfo_cc
    subject = 'Your Digital CFO login code'
    html = f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto;color:#1D3270">
      <div style="background:#1D3270;padding:20px 24px;border-radius:12px 12px 0 0">
        <span style="color:#fff;font-size:18px;font-weight:700">Alpha Direct · Digital CFO</span>
      </div>
      <div style="border:1px solid #eee;border-top:none;padding:24px;border-radius:0 0 12px 12px">
        <p>Your one-time login code for the Digital CFO's private access is:</p>
        <p style="font-size:32px;font-weight:800;letter-spacing:6px;color:#F47C20;margin:18px 0">{code}</p>
        <p>It expires in {OTP_TTL_MINUTES} minutes. If you did NOT just try to log in,
           ignore this email and consider changing the private-access password.</p>
      </div>
    </div>"""
    text = f'Your Digital CFO private-access login code is {code} (expires in {OTP_TTL_MINUTES} min).'
    send_html_with_cfo_cc(subject, html, [email], text_fallback=text, cc_cfo=False, no_reply=True)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ClaimStatusThrottle])
def cfo_request_code(request):
    """POST /api/v1/public/cfo-twin/request-code  Body: {password}

    If the private-access password is correct, email a 6-digit code to the CFO's
    fixed on-file address. Always returns the same generic message."""
    password = str((request.data or {}).get('password') or '')
    cfo_pw, cfo_email = _cfo_config()
    generic = Response({'ok': True, 'message': "If the password is correct, a login code has been emailed to the CFO."})
    if not secrets.compare_digest(password, cfo_pw):
        return generic
    since = timezone.now() - timedelta(minutes=OTP_TTL_MINUTES)
    if ClaimStatusCode.objects.filter(claim_number=CFO_CODE_KEY, created_at__gte=since).count() >= CODES_PER_CLAIM_PER_HOUR:
        return generic
    code = _make_otp()
    ClaimStatusCode.objects.create(
        claim_number=CFO_CODE_KEY, email=cfo_email, code_hash=_sha(code),
        expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES), sent_ip=_client_ip(request),
    )
    try:
        _email_cfo_login_code(cfo_email, code)
    except Exception:
        pass
    return generic


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ClaimStatusThrottle])
def cfo_verify_code(request):
    """POST /api/v1/public/cfo-twin/verify-code  Body: {password, code}

    Needs BOTH the password AND a valid emailed code. On success returns a
    session token the twin sends with each CFO-tier chat request."""
    data = request.data or {}
    password = str(data.get('password') or '')
    code = str(data.get('code') or '').strip()
    cfo_pw, _ = _cfo_config()
    if not secrets.compare_digest(password, cfo_pw) or not code:
        return Response({'detail': 'Invalid login.'}, status=http.HTTP_401_UNAUTHORIZED)

    now = timezone.now()
    supplied = _sha(code)
    recs = (ClaimStatusCode.objects
            .filter(claim_number=CFO_CODE_KEY, consumed=False, expires_at__gte=now)
            .order_by('-created_at')[:CODES_PER_CLAIM_PER_HOUR])
    ok = False
    for rec in recs:
        if rec.attempts >= MAX_ATTEMPTS:
            continue
        rec.attempts += 1
        if secrets.compare_digest(rec.code_hash, supplied):
            rec.consumed = True
            ok = True
        rec.save(update_fields=['attempts', 'consumed', 'updated_at'])
        if ok:
            break
    if not ok:
        return Response({'detail': 'That code is invalid or has expired.'}, status=http.HTTP_401_UNAUTHORIZED)

    token = secrets.token_urlsafe(32)
    _, cfo_email = _cfo_config()
    ClaimStatusCode.objects.create(
        claim_number=CFO_SESSION_KEY, email=cfo_email, code_hash=_sha(token),
        expires_at=timezone.now() + timedelta(hours=CFO_SESSION_TTL_HOURS),
    )
    return Response({'ok': True, 'token': token, 'expires_in': CFO_SESSION_TTL_HOURS * 3600})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def cfo_check_session(request):
    """POST /api/v1/public/cfo-twin/check  Body: {token} -> {valid: bool}

    Called by the twin's server on each CFO-tier request. No throttle: it is a
    cheap validation from the trusted server and needs a real token to pass."""
    token = str((request.data or {}).get('token') or '')
    if not token:
        return Response({'valid': False})
    now = timezone.now()
    valid = ClaimStatusCode.objects.filter(
        claim_number=CFO_SESSION_KEY, code_hash=_sha(token), consumed=False, expires_at__gte=now,
    ).exists()
    return Response({'valid': bool(valid)})


# ===========================================================================
# claim_lookup — INTERNAL claim detail by number, for the Digital CFO chat.
# Authenticated (ApiKey), NOT public. Returns the operational picture of one
# claim (status, type, dates, policyholder, policy) so the twin can answer
# "what's the status of claim G2026004594?". Reuses the Graphite read replica.
# ===========================================================================
def _lookup_claim_detail(claim_number: str):
    sql = (
        "SELECT c.claim_number AS claim_number, c.status AS status, "
        "c.claim_type AS claim_type, c.created_at AS logged_date, "
        "c.incident_date AS incident_date, c.reported_date AS reported_date, "
        "p.policyNumber AS policy_number, "
        "CONCAT(COALESCE(cu.firstName,''),' ',COALESCE(cu.lastName,'')) AS customer_name "
        "FROM claims c "
        "JOIN policies p ON c.policy_id = p.id "
        "JOIN customer cu ON p.customer_id = cu.id "
        "WHERE c.claim_number = %s "
        "ORDER BY c.id DESC LIMIT 1"
    )
    rows = list(query(sql, [claim_number]))
    return rows[0] if rows else None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def claim_lookup(request):
    """GET /api/v1/reports/claim-lookup/?claim=<number>

    One claim's detail by claim number (format like G2026004594). Authenticated
    (the twin's read-only ApiKey); never public."""
    claim_number = str(request.GET.get('claim') or request.GET.get('claim_number') or '').strip()
    if not claim_number:
        return Response({'detail': 'A "claim" query parameter is required.'}, status=http.HTTP_400_BAD_REQUEST)
    try:
        row = _lookup_claim_detail(claim_number)
    except Exception:
        return Response({'detail': 'Claim lookup failed.'}, status=http.HTTP_502_BAD_GATEWAY)
    if not row:
        return Response({'detail': 'No claim found with that number.', 'claim_number': claim_number},
                        status=http.HTTP_404_NOT_FOUND)

    def _d(v):
        return str(v)[:10] if v else None

    raw = row.get('status') or ''
    return Response({
        'claim_number':  row.get('claim_number'),
        'status':        raw,
        'status_label':  STATUS_LABEL.get(raw, raw or 'Unknown'),
        'claim_type':    row.get('claim_type'),
        'logged_date':   _d(row.get('logged_date')),
        'incident_date': _d(row.get('incident_date')),
        'reported_date': _d(row.get('reported_date')),
        'policy_number': row.get('policy_number'),
        'customer_name': (row.get('customer_name') or '').strip(),
    })
