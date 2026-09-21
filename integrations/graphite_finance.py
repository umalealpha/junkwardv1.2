"""
integrations/graphite_finance.py

Read-only client for the Graphite V2 Finance API — the machine-to-machine
feed that lets alpha-finance (omni) pull payment transactions from the
Graphite policy/payments system on a schedule.

Source contract (Graphite V2, Laravel):
  GET https://graphite-v2-prod-be.alphadirect.co.bw/api/v1/finance/payment-transactions
    Auth     : Sanctum personal access token, ability `finance:read`
               (Authorization: Bearer <token>)
    Window   : date_from / date_to required (Y-m-d); hard cap 31 days per call
    Paging   : cursor pagination (?cursor=...), default 500 / page, max 1000
    Filters  : partner (=payment_method), status, policy_number,
               reference_number, product_id, is_refund
    Response : { "data": [ {row}, ... ],
                 "meta": { count, per_page, next_cursor, window, has_more } }

This module ONLY talks to the API. Persistence + idempotent upsert lives in
the `ingest` helper here, called by the `pull_graphite_payments` management
command. Nothing in this path posts to the GL — the feed is read-only by
design (CFO directive 2026-06-15: ingest + report + reconcile, no journals).

Settings (read via decouple in settings.py; all default to '' / unset):
  GRAPHITE_FINANCE_API_BASE        e.g. https://graphite-v2-prod-be.alphadirect.co.bw
  GRAPHITE_FINANCE_API_TOKEN       Sanctum token scoped to finance:read
  GRAPHITE_FINANCE_TIMEOUT_SECONDS default 30

NOTE: these are SEPARATE from GRAPHITE_API_BASE / GRAPHITE_API_TOKEN, which
point at the *vehicle-lookup* Graphite endpoint used by salvage. Different
service account, different token, different base path — do not merge them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterator, Optional

import requests
from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contract constants — mirror PaymentTransactionController.php
# ---------------------------------------------------------------------------

ENDPOINT_PATH   = 'api/v1/finance/payment-transactions'
MAX_WINDOW_DAYS = 31     # server rejects diffInDays >= 31
SAFE_CHUNK_DAYS = 30     # we sweep in 30-day inclusive windows to stay clear
MAX_LIMIT       = 1000
DEFAULT_LIMIT   = 500

# Payment-partner values the Reporting Portal filter exposes; `payment_method`
# on a row carries one of these. Used to normalise / validate the --partner arg.
PARTNERS = ('RealPay', 'DPO', 'Orange', 'manual', 'VCS', 'cash')


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class GraphiteFinanceNotConfigured(Exception):
    """Raised when a pull is attempted but env vars aren't set."""


class GraphiteFinanceAPIError(Exception):
    """Raised when Graphite returns a non-2xx response we can't handle."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body        = body
        super().__init__(f'Graphite Finance API HTTP {status_code}: {body[:200]}')


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GraphiteFinanceConfig:
    api_base: str
    token:    str
    timeout:  float

    @classmethod
    def from_settings(cls) -> 'GraphiteFinanceConfig':
        return cls(
            api_base = getattr(settings, 'GRAPHITE_FINANCE_API_BASE', '') or '',
            token    = getattr(settings, 'GRAPHITE_FINANCE_API_TOKEN', '') or '',
            timeout  = float(getattr(settings, 'GRAPHITE_FINANCE_TIMEOUT_SECONDS', 30)),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_base and self.token)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GraphiteFinanceClient:
    """Thin read-only wrapper over the Graphite V2 Finance API.

    Usage:
        client = GraphiteFinanceClient()
        for row in client.iter_payments(date(2026, 5, 1), date(2026, 5, 31)):
            ...
    """

    def __init__(self, cfg: Optional[GraphiteFinanceConfig] = None):
        self.cfg = cfg or GraphiteFinanceConfig.from_settings()

    # ---- one page ----------------------------------------------------------

    def list_payments(
        self,
        date_from: date,
        date_to: date,
        *,
        partner: Optional[str] = None,
        status: Optional[str] = None,
        policy_number: Optional[str] = None,
        reference_number: Optional[str] = None,
        is_refund: Optional[bool] = None,
        limit: int = DEFAULT_LIMIT,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch a single page. Returns the decoded JSON (data + meta)."""
        if not self.cfg.is_configured:
            raise GraphiteFinanceNotConfigured(
                'Graphite Finance API not configured. Set '
                'GRAPHITE_FINANCE_API_BASE and GRAPHITE_FINANCE_API_TOKEN.'
            )

        window = (date_to - date_from).days
        if window < 0:
            raise ValueError('date_to must be >= date_from')
        if window >= MAX_WINDOW_DAYS:
            raise ValueError(
                f'Date window {window}d exceeds server cap of {MAX_WINDOW_DAYS}d; '
                f'use iter_payments() which sweeps in {SAFE_CHUNK_DAYS}-day chunks.'
            )

        limit = max(1, min(int(limit), MAX_LIMIT))
        params: Dict[str, Any] = {
            'date_from': date_from.isoformat(),
            'date_to':   date_to.isoformat(),
            'limit':     limit,
        }
        if partner:          params['partner']          = partner
        if status:           params['status']           = status
        if policy_number:    params['policy_number']    = policy_number
        if reference_number: params['reference_number'] = reference_number
        if is_refund is not None:
            params['is_refund'] = 1 if is_refund else 0
        if cursor:
            params['cursor'] = cursor

        url = self.cfg.api_base.rstrip('/') + '/' + ENDPOINT_PATH
        headers = {
            'Accept':        'application/json',
            'Authorization': f'Bearer {self.cfg.token}',
        }

        try:
            resp = requests.get(url, params=params, headers=headers,
                                timeout=self.cfg.timeout)
        except requests.RequestException as e:
            raise GraphiteFinanceAPIError(599, f'Network error: {e}')

        if not (200 <= resp.status_code < 300):
            raise GraphiteFinanceAPIError(resp.status_code, resp.text)

        try:
            return resp.json()
        except ValueError as e:
            raise GraphiteFinanceAPIError(resp.status_code,
                                          f'Bad JSON body: {e}')

    # ---- cursor sweep over one window -------------------------------------

    def iter_window(
        self,
        date_from: date,
        date_to: date,
        *,
        limit: int = DEFAULT_LIMIT,
        **filters: Any,
    ) -> Iterator[Dict[str, Any]]:
        """Yield every row in [date_from, date_to], following the cursor.

        The window MUST already be within the server cap. Callers that span
        more than SAFE_CHUNK_DAYS should use iter_payments() instead.
        """
        cursor = None
        while True:
            page = self.list_payments(
                date_from, date_to, limit=limit, cursor=cursor, **filters,
            )
            for row in page.get('data', []):
                yield row
            meta = page.get('meta', {}) or {}
            if not meta.get('has_more'):
                break
            cursor = meta.get('next_cursor')
            if not cursor:
                break

    # ---- full sweep, auto-chunked ----------------------------------------

    def iter_payments(
        self,
        date_from: date,
        date_to: date,
        *,
        limit: int = DEFAULT_LIMIT,
        **filters: Any,
    ) -> Iterator[Dict[str, Any]]:
        """Yield every row across an arbitrary date range, chunking into
        SAFE_CHUNK_DAYS windows to respect the server's 31-day cap."""
        for win_from, win_to in iter_windows(date_from, date_to):
            yield from self.iter_window(win_from, win_to, limit=limit, **filters)


# ---------------------------------------------------------------------------
# Window chunking
# ---------------------------------------------------------------------------

def iter_windows(
    date_from: date,
    date_to: date,
    chunk_days: int = SAFE_CHUNK_DAYS,
) -> Iterator[tuple[date, date]]:
    """Split [date_from, date_to] into inclusive sub-windows of at most
    chunk_days days each (e.g. 30-day slabs)."""
    if date_to < date_from:
        raise ValueError('date_to must be >= date_from')
    cur = date_from
    step = timedelta(days=chunk_days - 1)
    while cur <= date_to:
        win_to = min(cur + step, date_to)
        yield cur, win_to
        cur = win_to + timedelta(days=1)


# ---------------------------------------------------------------------------
# Ingest — upsert API rows into GraphitePaymentTransaction (idempotent)
# ---------------------------------------------------------------------------

def _parse_dt(value: Any):
    """Best-effort parse of an ISO-ish datetime/date string into an aware
    datetime, or None. Graphite ships 'YYYY-MM-DD HH:MM:SS' and 'YYYY-MM-DD'."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            dt = datetime.strptime(s, fmt)
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt
        except ValueError:
            continue
    # Last resort: Django's lenient parser
    try:
        from django.utils.dateparse import parse_datetime, parse_date
        dt = parse_datetime(s)
        if dt:
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt, timezone.get_current_timezone())
            return dt
        d = parse_date(s)
        if d:
            return timezone.make_aware(
                datetime(d.year, d.month, d.day),
                timezone.get_current_timezone(),
            )
    except Exception:  # noqa: BLE001 — never let a bad timestamp kill a sweep
        pass
    return None


def row_to_defaults(row: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten one API row into model field defaults (excluding graphite_id,
    which is the lookup key)."""
    policy = row.get('policy') or {}
    dpo    = row.get('dpo') or {}
    status = row.get('status') or ''
    try:
        amount = Decimal(str(row.get('amount') or '0'))
    except (InvalidOperation, ValueError):
        amount = Decimal('0')
    return {
        'policy_number':     row.get('policy_number') or '',
        'reference_number':  row.get('reference_number') or '',
        'amount':            amount,
        'payment_method':    row.get('payment_method') or '',
        'status':            status,
        'status_norm':       status.strip().lower(),
        'is_refund':         bool(row.get('is_refund')),
        'is_reverse':        bool(row.get('is_reverse')),
        'paid_at':           _parse_dt(row.get('paid_at')),
        'paid_on':           _parse_dt(row.get('paid_on')),
        'source_recorded_at': _parse_dt(row.get('recorded_at')),
        'source_updated_at': _parse_dt(row.get('updated_at')),
        'note':              row.get('note') or '',
        'payment_frequency': row.get('payment_frequency') or '',
        'policy_id':         policy.get('id'),
        'product_id':        policy.get('product_id'),
        'plan_id':           policy.get('plan_id'),
        'product_name':      policy.get('product_name') or '',
        'plan_name':         policy.get('plan_name') or '',
        'customer_name':     row.get('customer_name') or '',
        'agent_name':        row.get('agent_name') or '',
        'dpo_trans_id':      dpo.get('trans_id') or '',
        'dpo_company_ref':   dpo.get('company_ref') or '',
        'dpo_token':         dpo.get('token') or '',
        'raw':               row,
    }


def ingest_row(row: Dict[str, Any], *, sync_run=None) -> tuple[Any, bool]:
    """Upsert a single API row. Returns (obj, created). Idempotent on
    graphite_id, so re-pulling a window updates rows in place (picks up status
    changes, refund flips, etc.)."""
    from .models import GraphitePaymentTransaction

    graphite_id = row.get('id')
    if graphite_id is None:
        raise ValueError('row missing "id"')

    defaults = row_to_defaults(row)
    defaults['synced_at'] = timezone.now()
    if sync_run is not None:
        defaults['sync_run'] = sync_run

    obj, created = GraphitePaymentTransaction.objects.update_or_create(
        graphite_id=graphite_id,
        defaults=defaults,
    )
    return obj, created
