"""
fnb/client.py

HTTP client for FNB Botswana banking API.

Auth modes supported:
  - oauth2_client_credentials  (most common pattern; tokens cached for ~50 min)
  - api_key                    (Bearer token in Authorization header)
  - mtls                       (placeholder; needs cert paths configured)

Endpoint URLs are read from settings (TBD — fill in once Boitumelo sends specs):
  FNB_API_BASE          e.g. https://api-sandbox.fnbbotswana.co.bw/v1
  FNB_AUTH_URL          e.g. https://auth-sandbox.fnbbotswana.co.bw/oauth2/token
  FNB_CLIENT_ID         OAuth client ID
  FNB_CLIENT_SECRET     OAuth client secret
  FNB_API_KEY           Static API key (alternate to OAuth)
  FNB_AUTH_MODE         'oauth2_client_credentials' | 'api_key' | 'mtls'
  FNB_TLS_CERT_PATH     For mTLS — client cert path
  FNB_TLS_KEY_PATH      For mTLS — client key path
  FNB_TIMEOUT_SECONDS   default 15

Every API call writes one FNBSyncLog row — auditable replay built in.

When Boitumelo sends the spec, ALL that needs to change is:
  1. The endpoint path constants in `endpoints.py`
  2. The request/response shape in the service modules (statements.py,
     payments.py, beneficiaries.py)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests
from django.conf import settings
from django.utils import timezone

from .models import FNBSyncLog


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-endpoint rate limiter
# ---------------------------------------------------------------------------
# FNB confirmed 2026-05-25 (Kabelo Sekoto): 10 requests / minute / endpoint.
# Cross-worker enforcement via FNBSyncLog (already a system of record).
# We count outbound rows for this exact endpoint in the last 60 s; if N >= 10
# we sleep until the oldest of the 10 most-recent calls is older than 60 s.

FNB_RATE_LIMIT_RPM    = 10
FNB_RATE_LIMIT_WINDOW = 60.0   # seconds


def _throttle_endpoint(path: str) -> None:
    """Block until path is under FNB_RATE_LIMIT_RPM in the last
    FNB_RATE_LIMIT_WINDOW seconds.
    Authorisation calls (`/oauth2/token/v2`) are NOT counted against the
    business endpoint quota — FNB treats those separately.
    """
    if not path or path.startswith('oauth2/'):
        return

    from datetime import timedelta
    window_start = timezone.now() - timedelta(seconds=FNB_RATE_LIMIT_WINDOW)
    recent_ts = list(
        FNBSyncLog.objects
        .filter(
            direction=FNBSyncLog.Direction.OUTBOUND,
            endpoint=path,
            created_at__gte=window_start,
        )
        .exclude(service=FNBSyncLog.Service.AUTH)
        .order_by('-created_at')
        .values_list('created_at', flat=True)[:FNB_RATE_LIMIT_RPM]
    )
    if len(recent_ts) < FNB_RATE_LIMIT_RPM:
        return

    oldest_in_window = recent_ts[-1]
    wake_at = oldest_in_window + timedelta(seconds=FNB_RATE_LIMIT_WINDOW)
    sleep_s = (wake_at - timezone.now()).total_seconds()
    if sleep_s > 0:
        log.warning(
            'FNB rate limit reached on %s (%d/%ds); sleeping %.1fs',
            path, FNB_RATE_LIMIT_RPM, FNB_RATE_LIMIT_WINDOW, sleep_s,
        )
        # Cap the sleep at the window length to avoid pathological waits
        time.sleep(min(sleep_s, FNB_RATE_LIMIT_WINDOW))


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FNBNotConfigured(Exception):
    """Raised when an FNB call is attempted but env vars aren't set."""


class FNBAuthError(Exception):
    """Raised when auth fails (bad creds / expired)."""


class FNBAPIError(Exception):
    """Raised when FNB returns a non-2xx response we can't handle."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body        = body
        super().__init__(f'FNB API HTTP {status_code}: {body[:200]}')


# ---------------------------------------------------------------------------
# Configuration helper
# ---------------------------------------------------------------------------

@dataclass
class FNBConfig:
    api_base:       str
    auth_mode:      str
    auth_url:       str
    client_id:      str
    client_secret:  str
    api_key:        str
    tls_cert_path:  str
    tls_key_path:   str
    webhook_secret: str
    timeout:        float

    @classmethod
    def from_settings(cls) -> 'FNBConfig':
        cfg = cls(
            api_base       = getattr(settings, 'FNB_API_BASE', '') or '',
            auth_mode      = (getattr(settings, 'FNB_AUTH_MODE', '') or
                              'oauth2_client_credentials'),
            auth_url       = getattr(settings, 'FNB_AUTH_URL', '') or '',
            client_id      = getattr(settings, 'FNB_CLIENT_ID', '') or '',
            client_secret  = getattr(settings, 'FNB_CLIENT_SECRET', '') or '',
            api_key        = getattr(settings, 'FNB_API_KEY', '') or '',
            tls_cert_path  = getattr(settings, 'FNB_TLS_CERT_PATH', '') or '',
            tls_key_path   = getattr(settings, 'FNB_TLS_KEY_PATH', '') or '',
            webhook_secret = getattr(settings, 'FNB_WEBHOOK_SECRET', '') or '',
            timeout        = float(getattr(settings, 'FNB_TIMEOUT_SECONDS', 15)),
        )
        # In-app override: if the CFO saved the bank login via the Bank Connection
        # page (FnbCredential row), those values win over the environment — so the
        # login can be set/replaced without server access. Guarded so a missing
        # table or any error simply falls back to the env settings above.
        try:
            from .models import FnbCredential
            row = FnbCredential.load()
            if row:
                if row.client_id:
                    cfg.client_id = row.client_id
                _sec = row.get_secret()
                if _sec:
                    cfg.client_secret = _sec
        except Exception:  # noqa: BLE001
            pass
        return cfg

    @property
    def is_configured(self) -> bool:
        if not self.api_base:
            return False
        if self.auth_mode == 'oauth2_client_credentials':
            return bool(self.auth_url and self.client_id and self.client_secret)
        if self.auth_mode == 'api_key':
            return bool(self.api_key)
        if self.auth_mode == 'mtls':
            return bool(self.tls_cert_path and self.tls_key_path)
        return False


# ---------------------------------------------------------------------------
# Token cache — module-level, simple in-memory
# ---------------------------------------------------------------------------

_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}   # client_id → {token, expires_at}


def _get_oauth_token(cfg: FNBConfig) -> str:
    """Fetch (or reuse) an OAuth2 client_credentials access token."""
    cached = _TOKEN_CACHE.get(cfg.client_id)
    if cached and cached.get('expires_at', 0) > time.time() + 30:
        return cached['token']

    if not cfg.auth_url:
        raise FNBNotConfigured('FNB_AUTH_URL is not set.')

    started = time.monotonic()
    log_row = FNBSyncLog.objects.create(
        direction      = FNBSyncLog.Direction.OUTBOUND,
        service        = FNBSyncLog.Service.AUTH,
        endpoint       = cfg.auth_url,
        http_method    = 'POST',
        request_summary= 'OAuth2 client_credentials token',
        status         = FNBSyncLog.Status.PENDING,
    )
    try:
        # FNB requires the i_can scope on the token request (per
        # EFT-Payments-OpenAPI.yaml securitySchemes.oauth.flows.clientCredentials).
        # Override via FNB_OAUTH_SCOPE if Botswana uses a different one.
        scope = getattr(settings, 'FNB_OAUTH_SCOPE', 'i_can') or 'i_can'
        resp = requests.post(
            cfg.auth_url,
            data={
                'grant_type':    'client_credentials',
                'client_id':     cfg.client_id,
                'client_secret': cfg.client_secret,
                'scope':         scope,
            },
            headers={'Accept': 'application/json'},
            timeout=cfg.timeout,
        )
    except requests.RequestException as e:
        FNBSyncLog.objects.filter(pk=log_row.pk).update(
            status=FNBSyncLog.Status.TIMEOUT,
            error_message=str(e)[:500],
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        raise FNBAuthError(f'OAuth network error: {e}')

    elapsed_ms = int((time.monotonic() - started) * 1000)

    if resp.status_code != 200:
        FNBSyncLog.objects.filter(pk=log_row.pk).update(
            status=FNBSyncLog.Status.FAILED,
            http_status=resp.status_code,
            error_message=resp.text[:500],
            elapsed_ms=elapsed_ms,
        )
        raise FNBAuthError(f'OAuth HTTP {resp.status_code}: {resp.text[:200]}')

    try:
        data = resp.json()
        token = data['access_token']
        ttl   = int(data.get('expires_in', 3600))
    except (ValueError, KeyError) as e:
        FNBSyncLog.objects.filter(pk=log_row.pk).update(
            status=FNBSyncLog.Status.FAILED,
            http_status=resp.status_code,
            error_message=f'Bad token response: {e}',
            elapsed_ms=elapsed_ms,
        )
        raise FNBAuthError(f'Bad token response: {e}')

    _TOKEN_CACHE[cfg.client_id] = {
        'token':      token,
        'expires_at': time.time() + ttl,
    }
    FNBSyncLog.objects.filter(pk=log_row.pk).update(
        status=FNBSyncLog.Status.SUCCESS,
        http_status=resp.status_code,
        elapsed_ms=elapsed_ms,
        response_payload={'expires_in': ttl},
    )
    return token


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@dataclass
class FNBResponse:
    status_code: int
    json:        Dict[str, Any]
    text:        str
    elapsed_ms:  int


class FNBClient:
    """Thin wrapper that:
      - Resolves auth (OAuth / API key / mTLS)
      - Issues HTTP requests with timeout
      - Logs every call to FNBSyncLog
      - Raises typed exceptions on auth/API failures

    Replace endpoint URLs in service modules — the client is endpoint-agnostic.
    """

    def __init__(self, cfg: Optional[FNBConfig] = None, *, user=None):
        self.cfg  = cfg or FNBConfig.from_settings()
        self.user = user

    # ---- header builder ----------------------------------------------------

    def _auth_headers(self) -> Dict[str, str]:
        if self.cfg.auth_mode == 'oauth2_client_credentials':
            return {'Authorization': f'Bearer {_get_oauth_token(self.cfg)}'}
        if self.cfg.auth_mode == 'api_key':
            return {'Authorization': f'Bearer {self.cfg.api_key}'}
        if self.cfg.auth_mode == 'mtls':
            return {}
        raise FNBNotConfigured(f'Unknown FNB_AUTH_MODE: {self.cfg.auth_mode}')

    # ---- request -----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        service: str = FNBSyncLog.Service.OTHER,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        request_summary: str = '',
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> FNBResponse:
        if not self.cfg.is_configured:
            # Mark a SKIPPED sync log so the audit trail shows we tried
            FNBSyncLog.objects.create(
                direction      = FNBSyncLog.Direction.OUTBOUND,
                service        = service,
                endpoint       = path,
                http_method    = method.upper(),
                request_summary= request_summary or 'Skipped — FNB not configured',
                status         = FNBSyncLog.Status.SKIPPED,
                request_payload= json_body or {},
                triggered_by_user = self.user if self.user and self.user.is_authenticated else None,
            )
            raise FNBNotConfigured(
                'FNB integration is not configured. Set FNB_API_BASE and the '
                'auth env vars before calling.'
            )

        url = self.cfg.api_base.rstrip('/') + '/' + path.lstrip('/')
        headers = {
            'Accept':       'application/json',
            'Content-Type': 'application/json',
        }
        headers.update(self._auth_headers())
        if extra_headers:
            headers.update(extra_headers)

        log_row = FNBSyncLog.objects.create(
            direction      = FNBSyncLog.Direction.OUTBOUND,
            service        = service,
            endpoint       = path,
            http_method    = method.upper(),
            request_summary= (request_summary or path)[:1000],
            request_payload= _redact(json_body) if json_body else {},
            status         = FNBSyncLog.Status.PENDING,
            triggered_by_user = self.user if self.user and self.user.is_authenticated else None,
        )

        kwargs: Dict[str, Any] = {
            'headers': headers,
            'params':  params,
            # Per-call override (statements are slow on FNB's side and need
            # a longer read window than the default cfg.timeout).
            'timeout': timeout if timeout is not None else self.cfg.timeout,
        }
        if json_body is not None:
            kwargs['data'] = json.dumps(json_body)
        if self.cfg.auth_mode == 'mtls':
            kwargs['cert'] = (self.cfg.tls_cert_path, self.cfg.tls_key_path)

        _throttle_endpoint(path)

        started = time.monotonic()
        try:
            # noqa: S113 — `timeout` is set via kwargs above (line 343); ruff
            # cannot see the kwargs dict construction and false-flags this call.
            resp = requests.request(method, url, **kwargs)  # noqa: S113
        except requests.RequestException as e:
            FNBSyncLog.objects.filter(pk=log_row.pk).update(
                status=FNBSyncLog.Status.TIMEOUT,
                error_message=str(e)[:500],
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            raise FNBAPIError(599, f'Network error: {e}')

        elapsed_ms = int((time.monotonic() - started) * 1000)

        try:
            body_json = resp.json()
        except ValueError:
            body_json = {}

        ok = 200 <= resp.status_code < 300
        # 425 Too Early is "not processed yet", not a failure - see the comment
        # on FNBSyncLog.Status.WAITING. It still raises below, because the
        # CALLER must know the answer is not ready; it simply stops polluting
        # the failure count and the error text.
        waiting = resp.status_code == 425
        if ok:
            row_status = FNBSyncLog.Status.SUCCESS
        elif waiting:
            row_status = FNBSyncLog.Status.WAITING
        else:
            row_status = FNBSyncLog.Status.FAILED
        FNBSyncLog.objects.filter(pk=log_row.pk).update(
            status      = row_status,
            http_status = resp.status_code,
            response_payload = body_json or {'_raw': resp.text[:2000]},
            error_message = '' if (ok or waiting) else resp.text[:500],
            elapsed_ms  = elapsed_ms,
        )

        if not ok:
            raise FNBAPIError(resp.status_code, resp.text)

        return FNBResponse(
            status_code = resp.status_code,
            json        = body_json,
            text        = resp.text,
            elapsed_ms  = elapsed_ms,
        )

    # ---- convenience wrappers ----------------------------------------------

    def get(self, path: str, **kw):  return self.request('GET',  path, **kw)
    def post(self, path: str, **kw): return self.request('POST', path, **kw)
    def put(self, path: str, **kw):  return self.request('PUT',  path, **kw)


# ---------------------------------------------------------------------------
# Redaction — strip secrets before persisting payloads
# ---------------------------------------------------------------------------

_SECRET_KEYS = {
    'password', 'secret', 'client_secret', 'api_key',
    'authorization', 'access_token', 'refresh_token', 'pin',
}


def _redact(payload: Any) -> Any:
    """Walk *payload* and replace values of secret-looking keys with '***'."""
    if isinstance(payload, dict):
        return {
            k: ('***' if k.lower() in _SECRET_KEYS else _redact(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_redact(x) for x in payload]
    return payload
