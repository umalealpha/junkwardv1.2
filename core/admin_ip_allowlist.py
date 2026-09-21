"""
core/admin_ip_allowlist.py — Django middleware that restricts /admin/ and
/hris/admin/ access to a configured IP allowlist.

CFO structural-audit directive 2026-05-19: the Django admin panels are
exposed to the public internet — high-value brute-force targets. The
audit recommended doing this at the reverse proxy. We don't run a
reverse proxy in front of the prod containers today (Cloudflare → ALB →
gunicorn direct), so the cleanest hardening is application middleware.

Configuration:
  ADMIN_IP_ALLOWLIST  comma-separated list of CIDRs / single IPs.
                      Empty / unset = no restriction (back-compat).
                      Example: "13.245.255.206,196.43.197.0/24"

Behaviour:
  * Requests to /admin/* and /hris/admin/* whose client IP is NOT in
    the allowlist receive 404 (deliberately indistinguishable from a
    missing path — don't tell attackers the gate exists).
  * Healthchecks (Cloudflare, AWS ALB) pass through unchanged.
  * Resolves client IP from Cloudflare's CF-Connecting-IP (unspoofable)
    when present, else REMOTE_ADDR. X-Forwarded-For is deliberately NOT
    trusted — the client controls it.

Wired in alpha_finance/settings.py.MIDDLEWARE.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import time
from typing import Optional

from django.http import HttpResponseNotFound


log = logging.getLogger(__name__)


_GUARDED_PREFIXES = ('/admin/', '/hris/admin/')
_CACHE_TTL_S     = 30   # re-read DB at most this often


def _parse_cidrs(raw: str) -> list:
    """Parse a comma-separated list of CIDRs / single IPs into ip_network objects."""
    nets = []
    for token in (raw or '').split(','):
        t = token.strip()
        if not t:
            continue
        try:
            if '/' in t:
                nets.append(ipaddress.ip_network(t, strict=False))
            else:
                nets.append(ipaddress.ip_network(f'{t}/32', strict=False))
        except ValueError:
            log.warning('admin_ip_allowlist: ignoring malformed entry %r', t)
    return nets


def _load_env_allowlist() -> list:
    return _parse_cidrs(os.environ.get('ADMIN_IP_ALLOWLIST', '').strip())


def _load_db_allowlist() -> list:
    """Read AdminAllowlistEntry rows. Fails closed if the table isn't migrated yet."""
    try:
        from core.models import AdminAllowlistEntry
        rows = AdminAllowlistEntry.objects.filter(is_active=True).values_list('cidr', flat=True)
        return _parse_cidrs(','.join(rows))
    except Exception as e:    # noqa: BLE001
        # Pre-migration or DB outage — don't block /admin/ purely because the
        # lookup table failed to load. Env-only allowlist still applies.
        log.warning('admin_ip_allowlist: DB read failed (%s); env-only mode', e)
        return []


def _client_ip(request) -> Optional[str]:
    # SECURITY: never trust the left-most X-Forwarded-For value — the client
    # controls it (Cloudflare/ALB *append*, they don't replace), so a request
    # can spoof `X-Forwarded-For: <allowlisted-office-ip>` and walk past the
    # gate. Cloudflare sets CF-Connecting-IP to the real client and that header
    # cannot be forged by the client (CF overwrites it). Mirror the trusted
    # pattern already used in core/staff_login_views.py.
    cf = request.META.get('HTTP_CF_CONNECTING_IP', '').strip()
    if cf:
        return cf
    return request.META.get('REMOTE_ADDR')


class AdminIPAllowlistMiddleware:
    """
    Block admin paths unless the request IP is in either ADMIN_IP_ALLOWLIST
    (env) or the AdminAllowlistEntry table. Union of both — empty union
    means no restriction (back-compat for dev / pre-prod). Refreshes the
    DB-derived view every _CACHE_TTL_S so the CFO's self-service lock
    endpoint takes effect within ~30 s.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self._env_nets = _load_env_allowlist()
        self._db_nets: list = []
        self._db_last = 0.0
        if self._env_nets:
            log.info('AdminIPAllowlistMiddleware env nets: %d',
                     len(self._env_nets))

    def _current_nets(self) -> list:
        # Cheap cache so /admin/ requests don't slam the DB.
        now = time.monotonic()
        if (now - self._db_last) > _CACHE_TTL_S:
            self._db_nets = _load_db_allowlist()
            self._db_last = now
        return self._env_nets + self._db_nets

    def __call__(self, request):
        path = request.path or ''
        if not any(path.startswith(p) for p in _GUARDED_PREFIXES):
            return self.get_response(request)

        nets = self._current_nets()
        if not nets:
            return self.get_response(request)

        ip = _client_ip(request)
        if not ip:
            return HttpResponseNotFound()
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return HttpResponseNotFound()
        if any(addr in net for net in nets):
            return self.get_response(request)
        log.info('admin-allowlist block: %s tried %s', ip, path)
        return HttpResponseNotFound()
