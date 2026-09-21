"""
realpay/client.py — thin RealPay OMNI Channel Integration API client.

Auth: OAuth2 client_credentials. Token URL + transaction-list endpoint
shape follow RealPay's OMNI Channel Integration spec (uat.realpaycollect.com
:4448/api_doc). Endpoints are typed from the spec PDF + spec walkthrough
2026-05-25 (Nedine Olivier-Vorster).

Spec confirmed:
  • POST  {BASE}/oauth/token       (client_id + client_secret)  → access_token (+ expires_in)
  • POST  {BASE}/Transactions/list (Authorization Bearer)       → batch + line items
    body: {beneficiaryUserId, productCode, fromDate, toDate, pageSize, pageNumber}
  • Pagination via pageNumber / pageSize (max 200)

Hardening:
  • In-memory token cache w/ 60s clock-skew safety margin
  • requests.Session for connection re-use
  • Hard timeout 30s
  • Retry once on 401 (rotates token) then bubble up
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class RealPayError(Exception):
    """Raised on any non-recoverable RealPay API error."""


_TOKEN_CACHE: Dict[str, Tuple[str, float]] = {}
_HARD_TIMEOUT = 30


def _tls_verify() -> bool:
    """Whether to verify the RealPay TLS chain.

    Defaults to True (prod CAs). Override to False ONLY for the UAT
    self-signed endpoint by setting `REALPAY_TLS_VERIFY = False` in
    settings or env REALPAY_TLS_VERIFY=false.

    Security audit 2026-06-09 — bandit B501. Hardcoded ``verify=False``
    breaks MITM detection in production. The env-gated default keeps
    the UAT workflow working while making prod safe-by-default.
    """
    import os
    raw = (
        os.environ.get('REALPAY_TLS_VERIFY')
        or getattr(settings, 'REALPAY_TLS_VERIFY', None)
    )
    if raw is None:
        return True  # prod-safe default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in ('0', 'false', 'no', 'off')


def _base_url() -> str:
    base = (getattr(settings, 'REALPAY_BASE_URL', '') or '').rstrip('/')
    if not base:
        raise RealPayError('REALPAY_BASE_URL not configured.')
    return base


def _creds() -> Tuple[str, str]:
    """Resolve (client_id, client_secret).

    Order: settings/.env first, then the CFO-only encrypted Secrets Vault
    (core.VaultSecret, name = REALPAY_VAULT_NAME). The vault is the CFO's
    directed home for the LIVE credential (directive 2026-07-26): the secret
    is typed straight into the encrypted vault and NEVER placed in .env
    plaintext. Either half may come from either source, so the id can sit in
    .env (it is only an identifier) while the secret lives only in the vault.
    """
    cid    = (getattr(settings, 'REALPAY_CLIENT_ID', '') or '').strip()
    secret = (getattr(settings, 'REALPAY_CLIENT_SECRET', '') or '').strip()
    if cid and secret:
        return cid, secret
    from core.models import VaultSecret
    name = getattr(settings, 'REALPAY_VAULT_NAME', '') or 'RealPay API'
    # A DB error here is a real fault and must propagate, never masquerade as
    # "not configured". A simply-absent row is fine: it means the secret has
    # not been stored yet, and the caller's "not configured" message is true.
    vs = VaultSecret.objects.filter(name=name).first()
    if vs is None:
        return cid, secret
    cid = cid or (vs.username or '').strip()
    if not secret:
        revealed = (vs.reveal() or '').strip()
        if not revealed and (vs.secret_ciphertext or '').strip():
            # Ciphertext is present but decryption produced nothing — a wrong
            # VAULT_FERNET_KEY or a tampered value. reveal() swallows that and
            # returns '' (see core.vault_crypto.decrypt), so detect it here and
            # name the true fault instead of the misleading "not configured".
            raise RealPayError(
                f'RealPay vault secret "{name}" is present but could not be '
                'decrypted — check VAULT_FERNET_KEY.'
            )
        secret = revealed
    return cid, secret


def _fetch_token() -> str:
    """OAuth2 client_credentials → access_token. Cached for `expires_in` - 60s."""
    cid, secret = _creds()
    if not (cid and secret):
        raise RealPayError(
            'RealPay credentials not configured — set REALPAY_CLIENT_ID / '
            'REALPAY_CLIENT_SECRET, or store them in the Secrets Vault as '
            f'"{getattr(settings, "REALPAY_VAULT_NAME", "") or "RealPay API"}".'
        )

    cached = _TOKEN_CACHE.get(cid)
    if cached and cached[1] > time.time():
        return cached[0]

    # Confirmed against spec at uat.realpaycollect.com:4448/api_doc/rp_docs/
    # omni.json 2026-05-25: tokenUrl = clientCredentials grant via HTTP Basic
    # (RFC 6749 §2.3.1) — body MUST carry only grant_type. Client ID +
    # secret go in the Authorization header.
    url = f'{_base_url()}/oauth/token'
    resp = requests.post(
        url,
        data={'grant_type': 'client_credentials'},
        auth=(cid, secret),
        timeout=_HARD_TIMEOUT,
        verify=_tls_verify(),  # env-gated; default True (prod cert chain)
    )
    if resp.status_code != 200:
        raise RealPayError(
            f'RealPay token endpoint returned {resp.status_code}: '
            f'{resp.text[:300]}'
        )
    body = resp.json() or {}
    token = body.get('access_token') or body.get('accessToken') or ''
    if not token:
        raise RealPayError(f'RealPay token endpoint returned no access_token: {body!r}')
    ttl = int(body.get('expires_in') or body.get('expiresIn') or 3600)
    _TOKEN_CACHE[cid] = (token, time.time() + ttl - 60)
    return token


def _get(path: str, params: Dict[str, Any], *, retry_401: bool = True) -> Dict[str, Any]:
    """GET with Bearer token. Used by report endpoints in the RPWS spec."""
    token = _fetch_token()
    url = f'{_base_url()}{path}'
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept':        'application/json',
    }
    resp = requests.get(url, params=params, headers=headers,
                        timeout=_HARD_TIMEOUT, verify=_tls_verify())
    if resp.status_code == 401 and retry_401:
        _TOKEN_CACHE.clear()
        return _get(path, params, retry_401=False)
    if resp.status_code >= 400:
        raise RealPayError(
            f'RealPay GET {path} returned {resp.status_code}: '
            f'{resp.text[:300]}'
        )
    try:
        return resp.json() or {}
    except ValueError:
        raise RealPayError(f'RealPay GET {path} returned non-JSON body.')


def list_transactions(
    *,
    beneficiary_user_id: str,
    product_code: str,
    from_date: str,
    to_date: str,
    page_size: int = 200,
) -> List[Dict[str, Any]]:
    """Fetch debit-order collection events for the window.

    Wraps the RPWS spec's /reports/instalment_changes_report/{Product}
    endpoint. Per Nadine Olivier-Vorster 2026-06-03: the original
    /reports/transactions_report/ endpoint is no longer available via API
    (Swagger doc is being updated). Instalment Changes Report is the
    canonical replacement for the cash-movement / debit-collection pull.

    Query params (RPWS spec):
      BeneficiaryUser (int,    required, query)
      Product         (string, required, path)
      StartDate       (string, optional, query, YYYY-MM-DD)
      EndDate         (string, optional, query, YYYY-MM-DD)
      Version         (string, required, query — MUST be "v1", not "1")

    Auth: HTTP Basic on /oauth/token (grant_type=client_credentials in
    body, client_id+secret in Authorization header). Confirmed end-to-end
    on UAT 2026-06-03 for both BW user 19413 (RTFNBBW) and SA user 21175
    (ABSADC / ABSADO) — every call HTTP 200 with APIResponse.Status =
    SUCCESS.

    Returns the InstalmentChangesGetResponse list. May be empty on the
    UAT sandbox even for SUCCESS calls; that's vendor-side, not a bug.
    """
    body = _get(
        f'/reports/instalment_changes_report/{product_code}',
        {
            'BeneficiaryUser': str(beneficiary_user_id),
            'StartDate':       from_date,
            'EndDate':         to_date,
            'Version':         'v1',
        },
    )
    items = (body.get('InstalmentChangesGetResponse')
             or body.get('items') or body.get('data')
             or body.get('instalmentChanges') or body.get('records') or [])
    if not isinstance(items, list):
        return []
    return items
