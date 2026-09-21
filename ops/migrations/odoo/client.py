"""
XML-RPC client wrapper for the Odoo source system.

Reads credentials from env (ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PASSWORD).
Never accepts credentials via argument. Fails closed if any env var is missing.

Security audit 2026-06-09 — bandit B411. Monkey-patch xmlrpc.client via
defusedxml.xmlrpc so the underlying XML parser refuses external entity
/ DTD / billion-laughs payloads. Belt-and-braces — Odoo is a trusted
endpoint, but the patch is free.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Iterator

# Harden xmlrpc.client BEFORE importing ServerProxy from it.
try:
    from defusedxml.xmlrpc import monkey_patch as _defuse_xmlrpc
    _defuse_xmlrpc()
except ImportError:    # pragma: no cover — defusedxml is in requirements.txt
    pass

from xmlrpc.client import ServerProxy, Fault, ProtocolError    # noqa: E402  # nosec B411 — defusedxml.xmlrpc.monkey_patch() above neutralises the XML-RPC parser; bandit's static analyzer can't see runtime patching

logger = logging.getLogger(__name__)


class OdooConfigError(RuntimeError):
    """Raised when required env vars are missing or malformed."""


class OdooAuthError(RuntimeError):
    """Raised when Odoo authenticate() returns no uid."""


class OdooClient:
    """Authenticated XML-RPC client. One instance per migration run."""

    DEFAULT_BATCH_SIZE = 500
    RETRY_DELAYS_SEC = (1, 2, 4)   # exponential backoff

    def __init__(self):
        url      = os.environ.get('ODOO_URL', '').strip()
        db       = os.environ.get('ODOO_DB', '').strip()
        user     = os.environ.get('ODOO_USER', '').strip()
        password = os.environ.get('ODOO_PASSWORD', '')

        missing = [k for k, v in {
            'ODOO_URL': url, 'ODOO_DB': db,
            'ODOO_USER': user, 'ODOO_PASSWORD': password,
        }.items() if not v]
        if missing:
            raise OdooConfigError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Configure in /etc/alpha-finance/.env and restart the container."
            )

        if not url.startswith(('https://', 'http://')):
            url = 'https://' + url

        self.url      = url.rstrip('/')
        self.db       = db
        self.user     = user
        self._password = password
        self._uid     = None
        self._common  = ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)
        self._models  = ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> int:
        """Return the Odoo uid for the configured user, or raise."""
        uid = self._with_retry(
            self._common.authenticate, self.db, self.user, self._password, {},
        )
        if not uid:
            raise OdooAuthError(
                f"Odoo authentication failed for user '{self.user}' on '{self.url}/{self.db}'."
            )
        self._uid = uid
        logger.info("Authenticated to %s/%s as uid=%s", self.url, self.db, uid)
        return uid

    # ------------------------------------------------------------------
    # search_read with pagination
    # ------------------------------------------------------------------

    def search_read(
        self,
        model: str,
        domain: list,
        fields: list[str],
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        order: str = 'id asc',
    ) -> Iterator[dict]:
        """
        Paginated generator over Odoo records.

        Each yielded value is a dict shaped by `fields`. Stops automatically
        when the source has no more rows.
        """
        if self._uid is None:
            self.authenticate()

        offset = 0
        while True:
            chunk = self._with_retry(
                self._models.execute_kw,
                self.db, self._uid, self._password,
                model, 'search_read',
                [domain],
                {
                    'fields': fields,
                    'offset': offset,
                    'limit': batch_size,
                    'order': order,
                },
            )
            if not chunk:
                return
            yield from chunk
            if len(chunk) < batch_size:
                return
            offset += batch_size

    def search_count(self, model: str, domain: list) -> int:
        """Return the count of matching records (no row data)."""
        if self._uid is None:
            self.authenticate()
        return self._with_retry(
            self._models.execute_kw,
            self.db, self._uid, self._password,
            model, 'search_count', [domain],
        )

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    def _with_retry(self, fn, *args, **kwargs):
        last_exc = None
        for attempt, delay in enumerate([0, *self.RETRY_DELAYS_SEC]):
            if delay:
                time.sleep(delay)
            try:
                return fn(*args, **kwargs)
            except (ProtocolError, socket.error, OSError) as exc:
                last_exc = exc
                logger.warning(
                    "Odoo XML-RPC call failed (attempt %s/%s): %s",
                    attempt + 1, len(self.RETRY_DELAYS_SEC) + 1, exc,
                )
            except Fault as exc:
                # Server-side application error — re-raise immediately.
                raise
        raise RuntimeError(
            f"Odoo XML-RPC call exhausted retries: {last_exc}"
        ) from last_exc
