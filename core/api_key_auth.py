"""
core/api_key_auth.py — DRF authentication + scope enforcement for ApiKey.

CFO directive 2026-05-19: Manus needs a permanent, scope-restricted way
to call /smart-upload/* without a browser-derived MSAL Bearer that
expires every hour.

How it works:
  Authorization: ApiKey <plaintext-64-hex>

`ApiKeyAuthentication` looks the key up by its 12-char prefix, then
verifies the PBKDF2 hash with django.contrib.auth.hashers.check_password.
On success it returns (service_user, api_key_instance) so request.user
becomes the linked service account and request.auth becomes the ApiKey
row (used by ApiKeyScopePermission to enforce scopes).

`ApiKeyScopePermission` runs on every request. If the request was
authenticated by an ApiKey, the path is checked against the key's
allowed_scopes:

  smart-upload  → preview, commit, autodetect, sections, template,
                  companies (GET), accounts (GET)
  read-only     → any GET under /api/v1/
  admin         → no path restriction

If the request wasn't ApiKey-authed, this permission is permissive —
normal session / token / Bearer auth flows through untouched.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.contrib.auth.hashers import check_password
from django.utils import timezone
from rest_framework import authentication, exceptions, permissions


log = logging.getLogger(__name__)


_PREFIX_LEN = 12

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

# Scopes that may ONLY ever read. A key whose allowed_scopes are entirely
# inside this set is refused on any write METHOD at the AUTHENTICATION layer.
#
# Why the authentication layer and not ApiKeyScopePermission: the scope
# permission is installed via REST_FRAMEWORK['DEFAULT_PERMISSION_CLASSES'],
# and DRF's `@permission_classes([...])` REPLACES the defaults rather than
# adding to them. This codebase has ~1,070 views that declare their own
# permission_classes without re-listing ApiKeyScopePermission, so on every
# one of those the scope check — and therefore the "read-only" promise —
# silently did not run. Authentication runs before any view's permissions and
# cannot be overridden, so a read-only key is now genuinely read-only.
# (CFO directive 2026-08-03, raising the HR data-extract key for Unami Butale:
# a person-facing read-only key has to be provably read-only.)
READ_ONLY_SCOPES = frozenset({'read-only', 'notebook', 'hr-extract', 'po-claim-read'})


def _enforce_key_scope(request, api_key) -> None:
    """Scope enforcement for EVERY api key, at the authentication layer.

    `ApiKeyScopePermission` implements this correctly but is a DRF permission
    class, so it only runs where a view lists it. Counted 2026-08-09: **419
    views declare `permission_classes` and 5 of them include it.** DRF's
    `@permission_classes([...])` REPLACES the defaults rather than adding to
    them, so on the rest a key's scope is decorative — a read-only key was not
    actually held to read-only by anything.

    `_enforce_read_only_key` above already closed this for keys whose scopes are
    ALL read-only. It never fired for a key carrying a write scope, which is
    exactly the QC key's position (smart-upload + bulk-upload). This closes the
    remaining case using the same rule the permission class applies, moved to
    where it cannot be skipped.

    Deliberately mirrors ApiKeyScopePermission exactly — superuser and the
    role-based bypass still win, and a key with no scopes at all is still
    refused — so nothing that works today stops working.
    """
    scopes = list(getattr(api_key, 'allowed_scopes', None) or [])
    user = getattr(api_key, 'service_user', None)

    if user is not None and getattr(user, 'is_superuser', False):
        return

    method = (getattr(request, 'method', 'GET') or 'GET').upper()
    required_codes = (('read-all',) if method in ('GET', 'HEAD', 'OPTIONS')
                      else ('cfo-upload', 'smart-upload'))
    if user is not None:
        try:
            from core.models import UserRoleAssignment
            if UserRoleAssignment.objects.filter(
                user=user, revoked_at__isnull=True, role__is_active=True,
                role__permissions__code__in=required_codes,
            ).exists():
                return
        except Exception as exc:    # noqa: BLE001
            # Pre-migration / missing table: log and fall through to the scope
            # check rather than silently granting.
            log.warning('api key scope: role bypass lookup failed (%s) — '
                        'falling through to the path check', exc)

    if not scopes:
        raise exceptions.AuthenticationFailed(
            'This API key has no scopes and cannot be used.')

    path = getattr(request, 'path', '') or ''
    if not _path_allowed_by_scopes(path, scopes, method):
        raise exceptions.AuthenticationFailed(
            'API key does not include the scope required for this endpoint.')


def _enforce_read_only_key(request, api_key) -> None:
    """Front-door guard for a key that carries ONLY read-only scopes.

    Two refusals, both at the authentication layer for the reason given on
    READ_ONLY_SCOPES above — ApiKeyScopePermission cannot be relied on, because
    any view declaring its own permission_classes drops it:

      * any write method                     → refused
      * any path the scope does not grant    → refused

    The second one is not belt-and-braces. Verified 2026-08-03: an `hr-extract`
    key could GET /api/v1/journal-entries/ and receive 200, because that
    viewset declares its own permission_classes and so never ran the scope
    check. The path narrowing only means something if it lives here.

    Write-capable keys (smart-upload / cfo-upload / bulk-upload / admin /
    motor-liquidators) are untouched and keep using ApiKeyScopePermission.
    """
    scopes = set(api_key.allowed_scopes or ())
    if not scopes or not scopes.issubset(READ_ONLY_SCOPES):
        return

    method = (getattr(request, 'method', 'GET') or 'GET').upper()
    path = getattr(request, 'path', '') or ''

    if method not in SAFE_METHODS:
        log.warning('api_key %s (%s) refused %s %s — read-only key',
                    api_key.key_prefix, sorted(scopes), method, path)
        raise exceptions.AuthenticationFailed(
            'This key is read-only — it cannot change anything.'
        )

    if not _path_allowed_by_scopes(path, list(scopes), method):
        log.warning('api_key %s (%s) refused GET %s — outside scope',
                    api_key.key_prefix, sorted(scopes), path)
        raise exceptions.AuthenticationFailed(
            'This key is not allowed to read that part of omni.'
        )


def _client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or ''


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = 'ApiKey'

    def authenticate(self, request):
        auth = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth.startswith(self.keyword + ' '):
            return None
        raw = auth[len(self.keyword) + 1:].strip()
        if not raw:
            return None
        # Late import to avoid circular: ApiKey lives in core.models
        from core.models import ApiKey
        prefix = raw[:_PREFIX_LEN]
        candidates = list(
            ApiKey.objects.filter(key_prefix=prefix, is_active=True)
            .select_related('service_user')
        )
        matched = None
        for k in candidates:
            try:
                if check_password(raw, k.key_hash):
                    matched = k
                    break
            except Exception as e:    # noqa: BLE001
                log.warning('api_key auth: hash check failed (%s)', e)
                continue

        if matched is None:
            # Header was present but didn't match — refuse rather than fall
            # through, so a bad key can't accidentally hit a SessionAuth path.
            raise exceptions.AuthenticationFailed('Invalid API key.')

        # Update telemetry — best-effort, never block the request.
        try:
            matched.last_used_at = timezone.now()
            matched.last_used_ip = _client_ip(request)
            matched.save(update_fields=['last_used_at', 'last_used_ip'])
        except Exception:    # noqa: BLE001
            pass

        # NOTE: these two refusals sit OUTSIDE the hash-check try/except on
        # purpose. AuthenticationFailed subclasses Exception, so raising it
        # inside that block was silently swallowed by `except Exception` and
        # downgraded to `continue` — the request was still refused, but with
        # the wrong reason, and any new guard placed there would be defeated.
        if not matched.service_user.is_active:
            raise exceptions.AuthenticationFailed('Service account disabled.')

        _enforce_read_only_key(request, matched)
        # Same rule as ApiKeyScopePermission, applied where a view
        # cannot drop it by declaring its own permission_classes.
        _enforce_key_scope(request, matched)

        return (matched.service_user, matched)

    def authenticate_header(self, request):
        return self.keyword


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------

# Path patterns each scope grants. Matched with str.startswith() against
# request.path (which is "/api/v1/..." in this codebase).
SCOPE_PATHS = {
    # UniCoin's sign-in codes (CFO decision 2026-08-19). ONE path, and it must be
    # listed here or the key is refused at the AUTHENTICATION layer with a 401 —
    # which reads like a bad key rather than a missing registration. A new scope
    # that is not in this map does not work at all, however correct the view is.
    'unicoin-mail': (
        '/api/v1/mail/unicoin-login-code/',
    ),
    'smart-upload': (
        '/api/v1/smart-upload/preview/',
        '/api/v1/smart-upload/commit/',
        '/api/v1/smart-upload/autodetect/',
        '/api/v1/smart-upload/sections/',
        '/api/v1/smart-upload/template/',
        '/api/v1/companies/',
        '/api/v1/accounts/',
    ),
    # Manus directive 2026-05-20: bulk uploader needs to hit the
    # legacy /admin/cfo-upload-* endpoints AND verify via reads on
    # JE / contacts / assets / bank-accounts / invoices / payments /
    # dashboard / reports. Two new scopes carry that surface:
    #   cfo-upload: write access to the CFO-upload family
    #   bulk-upload: union of smart-upload + cfo-upload + read-all
    'cfo-upload': (
        '/api/v1/admin/cfo-upload-tb/',
        '/api/v1/admin/cfo-upload-gl/',
        '/api/v1/admin/cfo-upload-coa/',
        '/api/v1/admin/cfo-upload-treaty/',
        '/api/v1/admin/cfo-upload-status/',
    ),
    'bulk-upload': (
        '/api/v1/smart-upload/',
        '/api/v1/admin/cfo-upload-',
        '/api/v1/companies/',
        '/api/v1/accounts/',
        '/api/v1/journal-entries/',
        '/api/v1/contacts/',
        '/api/v1/assets/',
        '/api/v1/asset-categories/',
        '/api/v1/bank-accounts/',
        '/api/v1/invoices/',
        '/api/v1/payments/',
        '/api/v1/dashboard/cfo/',
        '/api/v1/reports/',
        '/api/v1/employees/',
        '/api/v1/payslips/',
        '/api/v1/purchase-orders/',
        '/api/v1/goods-receipt-notes/',
        '/api/v1/po-bill-matches/',
        '/api/v1/vendor-bank-accounts/',
    ),
    # Motor Liquidators V3 portal — narrow surface, just the
    # /salvage/external/ family + companies/accounts for context.
    'motor-liquidators': (
        '/api/v1/salvage/external/',
        '/api/v1/companies/',
        '/api/v1/accounts/',
    ),
    # Read-only access to the shared CFO/Claude notebook. Claude uses this
    # to fetch the page in one ~0.2s call at the start of every session.
    'notebook': ('/api/v1/notebook/',),
    # Write access to the SAME page (CFO directive 2026-08-15) — a separate
    # scope/key on purpose, so the read key stays provably read-only (see
    # READ_ONLY_SCOPES below) and a leaked read key can never edit the page.
    'notebook-write': ('/api/v1/notebook/',),
    # PO-in-Graphite doorway (CFO directive 2026-08-24) — the dedicated read-only
    # key Graphite's backend uses to ask "which POs did Omni raise for this
    # claim?". ONE path, and deliberately NOT '/api/v1/purchase-orders/' (which
    # the bulk-upload key holds) — this key must only ever reach the by-claim
    # lookup, never the full PO surface. It is in READ_ONLY_SCOPES above, so the
    # authentication layer refuses it any write method and any other path.
    'po-claim-read': ('/api/v1/purchase-orders/by-claim/',),
    # Graphite's inbound event doorway (2026-08-25, Manus retest P0). ONE path.
    # POST /api/v1/events/ runs EventProcessor synchronously, which raises real
    # customer invoices, vendor bills and credit notes — so this is a WRITE
    # scope and deliberately NOT in READ_ONLY_SCOPES. It is also the ONLY way
    # in: integrations.permissions.IntegrationEventAccess refuses a signed-in
    # human on create, so an event can never be hand-injected from a browser.
    'graphite-events': ('/api/v1/events/',),
    # Claims automation doorway (CFO plan 19-Sep-2026). Graphite pushes claim
    # events and reads back what Omni made of them. A WRITE scope, but it only
    # ever creates drafts, tasks and internal emails — never a payment, never a
    # client letter (those wait for a person). NOT in READ_ONLY_SCOPES.
    'claims-events': ('/api/v1/claims-automation/graphite/',),
    # UniCoin reconciliation doorway (CFO direction 2026-09-01, on Keetile's
    # UniCoin Finance & Debtors Automation plan). ONE path. POST raises a finding
    # that becomes an owned Omni task — it NEVER stops a debit, cancels a mandate
    # or moves money — so it is a narrow WRITE scope and deliberately NOT in
    # READ_ONLY_SCOPES. The detection flow (Power Automate / n8n) holds this key.
    'unicoin-recon': ('/api/v1/unicoin/recon-exceptions/',),
    # HR data extraction (CFO directive 2026-08-03) — lets the Chief Human
    # Capital Officer pull her OWN department's data from a Claude Code
    # session without a browser. Read-only is enforced at the authentication
    # layer (READ_ONLY_SCOPES above), so this list only narrows WHICH reads.
    #
    # Deliberately absent: /api/v1/reports/, /api/v1/journal-entries/,
    # /api/v1/dashboard/, /api/v1/accounts/, /api/v1/bank-accounts/,
    # /api/v1/payments/, /api/v1/invoices/ — HR must not read the accounting
    # department's data. That matches the 2026-07-15 access audit, which took
    # HR_MANAGER out of UserProfile.FINANCIALS_VIEW_TITLES for the same reason.
    # Pay IS included (CFO decision 2026-08-03: HR including pay).
    'hr-extract': (
        '/hris/',
        '/api/v1/employees/',
        '/api/v1/payslips/',
        '/api/v1/payslip-components/',
        '/api/v1/companies/',
        '/api/v1/me/companies/',
        '/api/v1/user-profiles/me/',
    ),
    # Manus QC pickup (CFO directive 2026-08-29) — replaces emailing Manus a QC
    # request. Manus polls the flagged bug-board items and posts its finding back
    # onto the SAME item. Deliberately just two paths: read the board, and the
    # narrow qc-result write (which touches only the qc_* fields, never status /
    # money / anything else). NOT in READ_ONLY_SCOPES because of that one write;
    # the write endpoint is itself locked to the qc_* result fields.
    # One prefix covers both the GET poll (?qc_requested=true) and the POST
    # <id>/qc-result/ write. The other bug-board writes on this prefix are shut
    # at the view layer: create is refused for this key (see BugReportView.post),
    # PATCH status needs a triager, and run-triage needs the CFO.
    'qc-manus': (
        '/api/v1/bug-reports/',
    ),
    # The Omni QC agent on the CFO's Mac (CFO 2026-09-07, idea #6 + #8): files a
    # bug when a page it checks is broken, and reads who still owes monthly
    # feedback so it can nudge managers on Telegram. Two paths, nothing else. The
    # bug create for this scope accepts ONE screenshot (the QC page capture) —
    # see BugReportView.post; every other write on the board stays shut to it.
    'qc-bot': (
        '/api/v1/bug-reports/',
        '/hris/api/performance/monthly/owed/',
        '/api/v1/admin/vault/qc/',   # read qc/-namespaced secrets only (vault_qc_get enforces the prefix)
    ),
    'admin': ('/',),
}


# Surfaces a READ-ONLY key must not reach even on a GET. "Read-only" limits what
# a key can CHANGE; it was never meant to hand over the whole staff file.
# Manus, 2026-08-09: the QC key pulled the entire audit trail — 525,283 rows —
# and was told that proved enforcement worked. Reading everything is a defensible
# design, but it is not the one the key was described as having.
READ_ONLY_DENIED_PREFIXES = (
    '/api/v1/audit-log/',        # who-did-what across the whole company
    '/api/v1/payslips/',         # individual pay
    '/api/v1/employees/',        # the staff file
    '/hris/',                    # HR: leave, documents, disciplinary, performance
    '/api/v1/llm-keys/',         # credential vault
)


def _path_allowed_by_scopes(path: str, scopes: list, method: str) -> bool:
    if 'admin' in scopes:
        return True
    if 'read-only' in scopes and method.upper() in ('GET', 'HEAD', 'OPTIONS'):
        if any(path.startswith(p) for p in READ_ONLY_DENIED_PREFIXES):
            return False
        # The financial reports live under /api/reports/, not /api/v1/reports/.
        # They are entity-level aggregates — no personal data — and a QC reader
        # that cannot open a single one of the 58 is not a reader (Manus
        # 2026-08-09). The deny-list above still applies.
        return path.startswith('/api/v1/') or path.startswith('/api/reports/')
    for scope in scopes:
        for p in SCOPE_PATHS.get(scope, ()):
            if path.startswith(p):
                return True
    return False


class ApiKeyScopePermission(permissions.BasePermission):
    """
    If the request was authenticated by an ApiKey, enforce scope.

    Manus directive 2026-05-20: role-based bypass takes precedence over
    the path-scope list. If the authenticated user holds an active
    UserRoleAssignment whose role carries the matching permission code,
    the request is allowed regardless of the key's allowed_scopes:

      GET / HEAD / OPTIONS  →  permission code 'read-all'
      everything else        →  'cfo-upload' OR 'smart-upload'

    This means a BULK_UPLOADER user can fan out to every endpoint
    without having to widen the key's scope each time we add a route.

    Falls back to the legacy path-scope check when no role match exists.
    Non-ApiKey requests pass through untouched.
    """
    message = 'API key does not include the scope required for this endpoint.'

    def has_permission(self, request, view) -> bool:
        api_key = getattr(request, 'auth', None)
        # Avoid the late import dance by duck-typing
        if api_key is None or not hasattr(api_key, 'allowed_scopes'):
            return True

        # ── Role-based bypass (Manus 2026-05-20 spec) ──────────────────
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            if getattr(user, 'is_superuser', False):
                return True
            method = (request.method or 'GET').upper()
            if method in ('GET', 'HEAD', 'OPTIONS'):
                required_codes = ('read-all',)
            else:
                required_codes = ('cfo-upload', 'smart-upload')
            try:
                from core.models import UserRoleAssignment
                if UserRoleAssignment.objects.filter(
                    user=user,
                    revoked_at__isnull=True,
                    role__is_active=True,
                    role__permissions__code__in=required_codes,
                ).exists():
                    return True
            except Exception:    # noqa: BLE001
                # Pre-migration / table missing: fall through to scope check.
                pass

        # ── Legacy path-scope check ────────────────────────────────────
        scopes = list(api_key.allowed_scopes or [])
        if not scopes:
            return False
        return _path_allowed_by_scopes(request.path, scopes, request.method or 'GET')
