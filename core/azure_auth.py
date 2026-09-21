"""
core/azure_auth.py — DRF authentication backend for Azure AD-issued JWTs.

Verifies Bearer tokens issued by login.microsoftonline.com for our tenant,
resolves the carrier to a Django User (creating one on first sign-in), and
auto-grants SUPER_ADMIN to any caller whose token carries the `omni-admins`
group claim.

DORMANT until AZURE_SSO_ENABLED=True is set in the env. While disabled, this
file imports cleanly but the auth class refuses every request (so we don't
accidentally enable a half-configured SSO flow). DRF's existing Token +
Session auth keeps working in parallel.

Activation procedure: see infra/azure-sso-setup.md.

Verification of the JWT follows the standard pattern:
  1. Pull the JWKS from https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys
  2. Cache the keys in-memory (24h)
  3. Validate signature, iss, aud, exp on every request
  4. Read oid + email + groups claims; look up / create User; refresh role
     assignments from group claims
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import authentication, exceptions

try:
    import jwt
    from jwt.algorithms import RSAAlgorithm
    _JWT_AVAILABLE = True
except ImportError:  # pragma: no cover — keeps the module importable when PyJWT isn't installed
    jwt = None
    RSAAlgorithm = None
    _JWT_AVAILABLE = False

logger = logging.getLogger(__name__)
User = get_user_model()


# ---------------------------------------------------------------------------
# JWKS cache (process-wide, 24 h TTL)
# ---------------------------------------------------------------------------

_JWKS_LOCK = threading.Lock()
_JWKS_CACHE: dict = {'fetched_at': 0, 'keys': {}}
_JWKS_TTL_SECONDS = 24 * 60 * 60


def _jwks_url() -> str:
    tenant = getattr(settings, 'AZURE_TENANT_ID', '') or ''
    return f'https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys'


def _get_signing_key(kid: str):
    """Return a public key suitable for jwt.decode() for the given kid."""
    if not _JWT_AVAILABLE:
        raise exceptions.AuthenticationFailed('PyJWT is not installed; cannot verify Azure tokens.')

    now = time.time()
    with _JWKS_LOCK:
        cache_stale = now - _JWKS_CACHE['fetched_at'] > _JWKS_TTL_SECONDS
        if cache_stale or kid not in _JWKS_CACHE['keys']:
            try:
                resp = requests.get(_jwks_url(), timeout=5)
                resp.raise_for_status()
                jwks = resp.json()
            except Exception as e:
                logger.warning('AzureJWT: JWKS fetch failed: %s', e)
                raise exceptions.AuthenticationFailed('Cannot fetch Azure signing keys.') from e
            _JWKS_CACHE['keys'] = {
                k['kid']: RSAAlgorithm.from_jwk(k) for k in jwks.get('keys', []) if k.get('kid')
            }
            _JWKS_CACHE['fetched_at'] = now
        key = _JWKS_CACHE['keys'].get(kid)
    if key is None:
        raise exceptions.AuthenticationFailed(f'Unknown signing key id: {kid}')
    return key


# ---------------------------------------------------------------------------
# Token verification + user resolution
# ---------------------------------------------------------------------------

def _verify_token(token: str) -> dict:
    """Verify signature, iss, aud, exp. Return the decoded claims dict."""
    if not _JWT_AVAILABLE:
        raise exceptions.AuthenticationFailed('PyJWT not installed.')

    tenant   = getattr(settings, 'AZURE_TENANT_ID', '')
    audience = getattr(settings, 'AZURE_API_AUDIENCE', '')
    if not (tenant and audience):
        raise exceptions.AuthenticationFailed('Azure SSO settings are incomplete.')

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as e:
        raise exceptions.AuthenticationFailed(f'Bad token header: {e}') from e
    kid = unverified_header.get('kid')
    if not kid:
        raise exceptions.AuthenticationFailed('Token header missing kid.')

    signing_key = _get_signing_key(kid)
    # Accept both v1 (sts.windows.net) and v2 (login.microsoftonline.com)
    # issuer formats. Tenants that haven't set accessTokenAcceptedVersion=2
    # in the app manifest issue v1 tokens by default.
    valid_issuers = {
        f'https://login.microsoftonline.com/{tenant}/v2.0',
        f'https://sts.windows.net/{tenant}/',
    }
    # Accept both audience forms: api://<client_id> (v2 convention) and
    # <client_id> bare GUID (what v1 tokens carry in their aud claim).
    valid_audiences = [audience]
    if audience.startswith('api://'):
        valid_audiences.append(audience[len('api://'):])

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=['RS256'],
            audience=valid_audiences,
            options={
                'require': ['exp', 'iat', 'iss', 'aud'],
                'verify_iss': False,   # checked manually below against the set
            },
        )
    except jwt.ExpiredSignatureError as e:
        raise exceptions.AuthenticationFailed('Token expired.') from e
    except jwt.InvalidAudienceError as e:
        raise exceptions.AuthenticationFailed('Token audience mismatch.') from e
    except jwt.PyJWTError as e:
        raise exceptions.AuthenticationFailed(f'Token rejected: {e}') from e

    iss = claims.get('iss', '')
    if iss not in valid_issuers:
        raise exceptions.AuthenticationFailed('Token issuer mismatch.')

    return claims


def _user_from_claims(claims: dict):
    """Payroll gate, then find-or-create the User (CFO rule 19-Sep-2026).

    The gate runs OUTSIDE the atomic block on purpose: a blocked sign-in raises,
    and inside the transaction that would also roll back its own audit row.
    "No payroll, no Omni": UniCoin agents (insurance.co.bw) not on payroll are
    refused now; other staff not on payroll are reported to HR until
    SSO_NEW_LOGIN_POLICY='enforce'. Existing logins are never affected.
    """
    email = (claims.get('preferred_username') or claims.get('upn')
             or claims.get('unique_name') or claims.get('email') or '').lower()
    if email and not User.objects.filter(email__iexact=email).exists():
        from core.login_gate import decide_new_login, record
        verdict, reason = decide_new_login(email)
        record(email, verdict, reason)
        if verdict == 'block':
            raise exceptions.AuthenticationFailed(
                'Omni access is only for staff on payroll. Please ask Human Resources.'
            )
    return _user_from_claims_atomic(claims)


@transaction.atomic
def _user_from_claims_atomic(claims: dict):
    """Find or create the Django User from validated token claims."""
    oid          = claims.get('oid') or claims.get('sub')
    # v1 tokens use 'upn' / 'unique_name', v2 uses 'preferred_username'.
    # Check all so we work with either token version.
    email        = (
        claims.get('preferred_username')
        or claims.get('upn')
        or claims.get('unique_name')
        or claims.get('email')
        or ''
    ).lower()
    given_name   = claims.get('given_name') or ''
    family_name  = claims.get('family_name') or claims.get('surname') or ''
    groups       = set(claims.get('groups') or [])

    if not (oid and email):
        # Diagnostic: dump the available claim keys so we know what Azure issued.
        # NOT a security leak — keys only, no values.
        logger.warning(
            'AzureJWT: missing oid or email. ver=%s iss=%s claim_keys=%s',
            claims.get('ver'), claims.get('iss'), sorted(claims.keys()),
        )
        raise exceptions.AuthenticationFailed(
            f'Token missing oid or email claim. version={claims.get("ver")} '
            f'keys={sorted(claims.keys())}'
        )

    # Username: prefer the local-part of email, append OID suffix if collision risk.
    # Look up by email first to find an existing user (e.g. pganesharajah seeded earlier).
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        username_base = email.split('@')[0][:30] or oid[:30]
        # Avoid collision
        candidate = username_base
        suffix = 1
        while User.objects.filter(username=candidate).exists():
            suffix += 1
            candidate = f'{username_base}{suffix}'[:30]
        user = User.objects.create_user(
            username  = candidate,
            email     = email,
            first_name= given_name[:150],
            last_name = family_name[:150],
        )
        user.set_unusable_password()
        user.save(update_fields=['password'])

    # Keep email + names in sync (in case they changed in Azure AD)
    fields_to_update = []
    if user.email.lower() != email:
        user.email = email
        fields_to_update.append('email')
    if given_name and user.first_name != given_name[:150]:
        user.first_name = given_name[:150]
        fields_to_update.append('first_name')
    if family_name and user.last_name != family_name[:150]:
        user.last_name = family_name[:150]
        fields_to_update.append('last_name')
    if fields_to_update:
        user.save(update_fields=fields_to_update)

    # Ensure UserProfile exists. Legacy endpoints (/api/v1/user-profiles/me/)
    # 404 without one, which breaks the TopBar and other shared components.
    # Bug 2026-07-15 (Oprah's access-control test): this used to default the
    # legacy `title` to ACCOUNTANT, which grants full financial-data access
    # (core.models.UserProfile.can_view_financials) to EVERY new SSO login —
    # including interns — until someone manually corrects it. Two interns
    # (kmojela, kmudabuka) reached live accounting data this way on first
    # login. OPERATIONS_STAFF (the new-RBAC role default just below) already
    # carries no special access; OPERATIONS (the matching legacy title) is
    # the safe equivalent — mirrors how Babusi Rasenyai's account (title=
    # operations) was correctly blocked in the same test.
    from core.models import UserProfile, UserRoleAssignment
    profile, _ = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            'role':             UserProfile.Role.OPERATIONS_STAFF,
            'title':            UserProfile.Title.OPERATIONS,
            'is_administrator': False,
            'is_active':        True,
        },
    )

    # Sync the legacy admin flag from the new RBAC layer so the Sidebar
    # 'Administration' group, the /settings/users page, and all other
    # legacy gates that check can_administer_users keep working.
    # Anyone holding an active role at level <= 1 (SUPER_ADMIN or EXECUTIVE)
    # is treated as a legacy administrator.
    has_admin_role = UserRoleAssignment.objects.filter(
        user=user, revoked_at__isnull=True,
        role__is_active=True, role__level__lte=1,
    ).exists()
    fields = []
    if has_admin_role and not profile.is_administrator:
        profile.is_administrator = True
        fields.append('is_administrator')
    # NOTE (CFO directive 2026-06-29): an app-admin role grants the legacy
    # is_administrator flag ONLY — it must NOT auto-assign the CFO *finance*
    # title. Conflating the two re-stamped non-CFO admins (e.g. the external
    # integration account) as CFO on every sign-in, re-inflating the JE
    # approver pool. The real CFO is protected by the owner-email hard-pin
    # below; every other title is managed explicitly by the CFO.
    if fields:
        profile.save(update_fields=fields + ['updated_at'])

    # ── Zero-company-access self-heal (CFO directive 2026-07-13) ─────────
    # CompanyScopedViewSetMixin hides EVERY scoped record from a user with
    # no UserCompanyAccess rows, so a freshly provisioned (or duplicated)
    # account sees an empty omni — POs 404, vendors vanish, dashboards
    # blank. That produced a steady stream of "I can't open / can't add"
    # complaints (Bonang Lentswe's blentswe@ login being the 10th). Staff
    # signing in from an internal domain get view-only access to the
    # default company (ADIC) so the app is usable on day one; wider grants
    # stay an explicit CFO/admin action in /settings.
    _INTERNAL_DOMAINS = (
        '@alphadirect.co.bw', '@insurance.co.bw', '@alphadirect.co.za',
    )
    if (email.lower().endswith(_INTERNAL_DOMAINS)
            and not user.company_access.exists()):
        from core.models import Company, UserCompanyAccess
        default_co = Company.objects.filter(code__iexact='ADIC').first()
        if default_co:
            UserCompanyAccess.objects.get_or_create(
                user=user, company=default_co,
                defaults={
                    'can_view': True, 'can_write': False,
                    'notes': 'auto: zero-access self-heal on SSO sign-in',
                },
            )

    # ── CFO immutable-superuser hard-pin ─────────────────────────────────
    # The CFO is the owner of this system and must NEVER be locked out
    # under any circumstance — no transient endpoint failure, no migration
    # accident, no missing role assignment. On every sign-in, force the
    # CFO's Django flags + UserProfile back to a known-good superuser
    # state. Identifier is the email claim from Azure AD, configurable via
    # the OMNI_OWNER_EMAILS env var (comma-separated). Defaults to the
    # production CFO mailbox.
    owner_emails_raw = getattr(settings, 'OMNI_OWNER_EMAILS', '') \
        or 'pganesharajah@alphadirect.co.bw'
    owner_emails = {e.strip().lower() for e in owner_emails_raw.split(',') if e.strip()}
    if email.lower() in owner_emails:
        user_dirty = []
        if not user.is_superuser:
            user.is_superuser = True
            user_dirty.append('is_superuser')
        if not user.is_staff:
            user.is_staff = True
            user_dirty.append('is_staff')
        if not user.is_active:
            user.is_active = True
            user_dirty.append('is_active')
        if user_dirty:
            user.save(update_fields=user_dirty)

        prof_dirty = []
        if profile.title != UserProfile.Title.CFO:
            profile.title = UserProfile.Title.CFO
            prof_dirty.append('title')
        if not profile.is_administrator:
            profile.is_administrator = True
            prof_dirty.append('is_administrator')
        if not profile.is_active:
            profile.is_active = True
            prof_dirty.append('is_active')
        if prof_dirty:
            profile.save(update_fields=prof_dirty + ['updated_at'])

        # Grant SUPER_ADMIN role if it exists and the owner doesn't
        # already hold it. Idempotent — never duplicates.
        try:
            from core.models import Role
            super_admin = Role.objects.filter(code='SUPER_ADMIN', is_active=True).first()
            if super_admin:
                already = UserRoleAssignment.objects.filter(
                    user=user, role=super_admin, revoked_at__isnull=True,
                ).exists()
                if not already:
                    from core.rbac_service import assign_role
                    try:
                        assign_role(
                            target_user=user, role=super_admin, granted_by=None,
                            justification='Auto-pin: omni owner / CFO immutable role.',
                            bypass_hierarchy=True,
                        )
                    except Exception as e:
                        logger.warning(
                            'AzureJWT: owner SUPER_ADMIN auto-grant for %s failed: %s',
                            email, e,
                        )
        except Exception as e:  # never block sign-in on this
            logger.warning('AzureJWT: owner role-pin error for %s: %s', email, e)

    # Auto-grant SUPER_ADMIN if user is in the configured admin group.
    # Auto-grant is deliberately one-way: removing from group does NOT auto-revoke.
    # An admin must explicitly revoke via the UI / API to leave an audit trail.
    admin_group = getattr(settings, 'AZURE_ADMIN_GROUP_ID', '') or ''
    if admin_group and admin_group in groups:
        from core.models import Role, UserRoleAssignment
        from core.rbac_service import assign_role
        super_admin = Role.objects.filter(code='SUPER_ADMIN').first()
        if super_admin:
            already = UserRoleAssignment.objects.filter(
                user=user, role=super_admin, revoked_at__isnull=True,
            ).exists()
            if not already:
                try:
                    assign_role(
                        target_user=user,
                        role=super_admin,
                        granted_by=None,
                        justification=f'Auto-granted via omni-admins group membership '
                                      f'(Azure AD group {admin_group}) on first sign-in.',
                        bypass_hierarchy=True,
                    )
                except Exception as e:  # never block sign-in on this
                    logger.warning('AzureJWT: SUPER_ADMIN auto-grant for %s failed: %s', email, e)

    return user


# ---------------------------------------------------------------------------
# DRF authentication class
# ---------------------------------------------------------------------------

class AzureJWTAuthentication(authentication.BaseAuthentication):
    """
    Accept `Authorization: Bearer <jwt>` from Microsoft for our tenant.

    Returns (User, None) — DRF's standard 2-tuple. If the header is absent
    or doesn't look like a Bearer JWT, returns None so DRF falls through to
    Token / Session auth.

    Disabled (returns None unconditionally) when AZURE_SSO_ENABLED is False.
    """
    keyword = 'Bearer'

    def authenticate(self, request):
        if not getattr(settings, 'AZURE_SSO_ENABLED', False):
            return None

        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.lower().startswith(self.keyword.lower() + ' '):
            return None

        token = auth_header.split(' ', 1)[1].strip()
        if not token:
            raise exceptions.AuthenticationFailed('Empty Bearer token.')

        claims = _verify_token(token)
        user   = _user_from_claims(claims)
        if not user.is_active:
            raise exceptions.AuthenticationFailed('User account is disabled.')
        return (user, None)

    def authenticate_header(self, request):
        # Tells DRF what to put in the WWW-Authenticate header on 401.
        return f'{self.keyword} realm="omni"'
