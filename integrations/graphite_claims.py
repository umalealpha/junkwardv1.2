"""
integrations/graphite_claims.py

Read-only feed: pull the claims register from Graphite V2 (GET /api/v1/claims)
into omni's local mirror (integrations.GraphiteClaim), so the Claims tab can
show every claim on one screen with filters + export — instead of going
claim-by-claim in Graphite (Bokani 2026-06-24, "the same way we simplified the
RealPay tab").

Reuses the SAME Graphite Finance API base + token as the payments feed
(GRAPHITE_FINANCE_API_BASE / GRAPHITE_FINANCE_API_TOKEN). No GL involvement —
this is a read-only mirror. Idempotent: upserts on graphite_id, so claim status
changes are picked up on the next sweep.

Claim row shape (verified live 2026-06-24):
  id, claim_number, claim_type, status, claim_handler, created_at,
  registered_claim (date), customer{id,name,company_name,is_company,cellphone},
  policy{id,policy_number,product_name}.
Pagination: meta{total, per_page, current_page, last_page}; page-based (?page=N).
"""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterator, Optional

import requests
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from integrations.graphite_finance import (
    GraphiteFinanceConfig,
    GraphiteFinanceNotConfigured,
    GraphiteFinanceAPIError,
)

CLAIMS_PATH = 'api/v1/claims'


def claims_configured() -> bool:
    return GraphiteFinanceConfig.from_settings().is_configured


class GraphiteClaimsClient:
    def __init__(self, cfg: Optional[GraphiteFinanceConfig] = None):
        self.cfg = cfg or GraphiteFinanceConfig.from_settings()

    def list_page(self, page: int = 1, *, max_retries: int = 5) -> Dict[str, Any]:
        if not self.cfg.is_configured:
            raise GraphiteFinanceNotConfigured(
                'Graphite Finance API not configured. Set '
                'GRAPHITE_FINANCE_API_BASE and GRAPHITE_FINANCE_API_TOKEN.'
            )
        url = self.cfg.api_base.rstrip('/') + '/' + CLAIMS_PATH
        headers = {'Accept': 'application/json',
                   'Authorization': f'Bearer {self.cfg.token}'}
        attempt = 0
        while True:
            try:
                resp = requests.get(url, params={'page': page},
                                    headers=headers, timeout=self.cfg.timeout)
            except requests.RequestException as e:
                raise GraphiteFinanceAPIError(599, f'Network error: {e}')
            # Graphite throttles bulk sweeps (HTTP 429 "Too Many Attempts").
            # Honour Retry-After, else exponential backoff, then retry.
            if resp.status_code == 429 and attempt < max_retries:
                try:
                    wait = float(resp.headers.get('Retry-After') or 0) or (2 ** attempt)
                except (TypeError, ValueError):
                    wait = 2 ** attempt
                time.sleep(min(wait, 30.0))
                attempt += 1
                continue
            if resp.status_code != 200:
                raise GraphiteFinanceAPIError(resp.status_code, resp.text[:300])
            return resp.json()

    def iter_claims(self, max_pages: int = 1000, *, page_delay: float = 0.35) -> Iterator[dict]:
        """Yield every claim row, sweeping pages 1..last_page. A small inter-page
        delay keeps the bulk sweep under Graphite's rate limit."""
        page, last = 1, 1
        while page <= last and page <= max_pages:
            payload = self.list_page(page)
            meta = payload.get('meta') or {}
            try:
                last = int(meta.get('last_page') or 1)
            except (TypeError, ValueError):
                last = page
            for row in (payload.get('data') or []):
                yield row
            page += 1
            if page <= last:
                time.sleep(page_delay)

    def fetch_detail(self, claim_id, *, max_retries: int = 5) -> Dict[str, Any]:
        """GET /api/v1/claims/{id} — the per-claim detail that carries
        total_reserve / total_payment / balance / date_of_loss. Same 429
        backoff as list_page. Returns the decoded `data` node (or {})."""
        if not self.cfg.is_configured:
            raise GraphiteFinanceNotConfigured('Graphite Finance API not configured.')
        url = self.cfg.api_base.rstrip('/') + f'/{CLAIMS_PATH}/{claim_id}'
        headers = {'Accept': 'application/json',
                   'Authorization': f'Bearer {self.cfg.token}'}
        attempt = 0
        while True:
            try:
                resp = requests.get(url, headers=headers, timeout=self.cfg.timeout)
            except requests.RequestException as e:
                raise GraphiteFinanceAPIError(599, f'Network error: {e}')
            if resp.status_code == 429 and attempt < max_retries:
                try:
                    wait = float(resp.headers.get('Retry-After') or 0) or (2 ** attempt)
                except (TypeError, ValueError):
                    wait = 2 ** attempt
                time.sleep(min(wait, 30.0))
                attempt += 1
                continue
            if resp.status_code != 200:
                raise GraphiteFinanceAPIError(resp.status_code, resp.text[:300])
            body = resp.json()
            return body.get('data', body) if isinstance(body, dict) else {}


def _to_date(v):
    return parse_date(str(v)[:10]) if v else None


def _to_dt(v):
    return parse_datetime(str(v)) if v else None


def row_to_defaults(row: Dict[str, Any]) -> Dict[str, Any]:
    cust = row.get('customer') or {}
    pol = row.get('policy') or {}
    is_co = bool(cust.get('is_company'))
    name = (cust.get('company_name') if is_co else cust.get('name')) or cust.get('name') or ''
    return {
        'claim_number':        (row.get('claim_number') or '')[:64],
        'claim_type':          (row.get('claim_type') or '')[:64],
        'status':              (row.get('status') or '')[:32],
        'claim_handler':       (row.get('claim_handler') or '')[:128],
        'customer_name':       (name or '')[:255],
        'customer_graphite_id': cust.get('id'),
        'is_company':          is_co,
        'policy_number':       (pol.get('policy_number') or '')[:64],
        'product_name':        (pol.get('product_name') or '')[:128],
        'registered_date':     _to_date(row.get('registered_claim')),
        'graphite_created_at': _to_dt(row.get('created_at')),
    }


def ingest_row(row: Dict[str, Any]) -> tuple[Any, bool]:
    """Upsert one claim on graphite_id. Returns (obj, created)."""
    from integrations.models import GraphiteClaim
    gid = row.get('id')
    if gid is None:
        return None, False
    obj, created = GraphiteClaim.objects.update_or_create(
        graphite_id=gid, defaults=row_to_defaults(row),
    )
    return obj, created


def _money(v) -> Decimal:
    """Graphite stores reserve/payment/balance as whole Pula (int). Map to
    Decimal pula (NOT cents — verified: raw 1390 == P1,390.00)."""
    if v in (None, ''):
        return Decimal('0')
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal('0')


def _damage_cause(detail: Dict[str, Any]) -> str:
    """What happened, for Bokani's 'Damage Cause' column. Graphite's
    `description_of_loss` is the real narrative ('THE INSURED HIT A COW') but is
    only filled on accident-type claims; fall back to the claim_type category
    (Accident / Glass / Fire) so every row says something. NOTE: `type_of_loss`
    is a numeric code in Graphite ('1','2') — never use it as a label."""
    desc = detail.get('description_of_loss')
    if desc and str(desc).strip():
        return str(desc).strip()[:200]
    for key in ('claim_type', 'event_name', 'reason'):
        v = detail.get(key)
        if v and str(v).strip():
            return str(v).strip()[:200]
    return ''


def detail_defaults(detail: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'date_of_loss':     _to_date(detail.get('date_of_loss')),
        'total_reserve':    _money(detail.get('total_reserve')),
        'total_payment':    _money(detail.get('total_payment')),
        'balance':          _money(detail.get('balance')),
        'damage_cause':     _damage_cause(detail),
        'detail_synced_at': timezone.now(),
    }


def enrich_claim(client: 'GraphiteClaimsClient', obj) -> bool:
    """Fetch the claim detail and update its money + date-of-loss fields.
    Returns True on success. Caller handles pacing/backoff between claims."""
    detail = client.fetch_detail(obj.graphite_id)
    if not detail:
        return False
    from integrations.models import GraphiteClaim
    GraphiteClaim.objects.filter(pk=obj.pk).update(**detail_defaults(detail))
    return True
