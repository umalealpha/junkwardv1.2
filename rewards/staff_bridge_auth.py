"""rewards/staff_bridge_auth.py — Nexus staff bridge (CFO 2026-07-14).

Lets an Alpha Direct EMPLOYEE who signed into the Alpha Nexus app (customer
email-OTP login) use the normal omni APIs — leave, approvals, refunds, spend —
from inside the app, without a second login and without duplicating any
business logic.

How: the app sends its customer bearer token to /api/v1/*. This authenticator
resolves that token to a CustomerSession -> RewardMember -> the ACTIVE Django
User with the same email. If every step matches, the request proceeds as that
staff user (all existing permission checks apply unchanged). If any step does
not match — unknown token, expired session, or a normal customer with no staff
account — it returns None and the other authenticators take over.

Security posture:
  * A customer with a Gmail simply has no matching User -> no staff access.
  * The mapping requires an is_active User; disabled staff lose app access
    the moment their account is disabled.
  * MUST be listed BEFORE AzureJWTAuthentication in settings: Azure raises on
    a malformed (non-JWT) Bearer token, which would kill the chain before this
    class could claim the token. This class only claims tokens that actually
    exist in the CustomerSession table, so real Azure JWTs fall through to it
    untouched (a JWT is never a stored session-token hash).
"""
from __future__ import annotations

import hashlib

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication


class NexusStaffAuthentication(BaseAuthentication):
    keyword = 'Bearer'

    def authenticate_header(self, request):
        """Return a WWW-Authenticate value so a failed sign-in answers 401.

        DRF takes this from the FIRST authenticator in the chain, and this class
        is first. BaseAuthentication returns None, which makes DRF downgrade
        every authentication failure to 403 — so a dead DRF token came back as
        403 "Invalid token." instead of 401. The web app's stale-token recovery
        keys on the 401, so it never fired: the dead key stayed in localStorage
        and the user was locked out of every page with no way back to the sign-in
        screen (4-Aug-2026: a staff member could not submit a spend request, and
        ~600 calls in two hours failed this way). Real permission denials
        are unaffected — they never consult this method.
        """
        return self.keyword

    def authenticate(self, request):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.lower().startswith('bearer '):
            return None
        raw = header.split(' ', 1)[1].strip()
        # JWTs (Azure SSO) have two dots; customer session tokens never do.
        # Skip early so we don't do a DB lookup for every SSO request.
        if not raw or raw.count('.') == 2:
            return None
        token_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()

        from rewards.models import CustomerSession
        sess = (CustomerSession.objects.select_related('member')
                .filter(token_hash=token_hash, revoked=False).first())
        if sess is None:
            return None                       # not a customer token — fall through
        if sess.expires_at < timezone.now():
            return None
        member = sess.member
        email = (getattr(member, 'email', '') or '').strip()
        if not email:
            return None
        # Defence in depth (CFO 2026-07-14): the App-Store review demo account
        # signs in with a fixed code (rewards.customer_auth). It must NEVER be
        # bridged to a staff Django user, even if one is later created with that
        # email — the reviewer only ever sees the demo customer, never omni.
        import os
        rev = (os.environ.get('NEXUS_REVIEW_EMAIL', '') or '').strip().lower()
        if rev and email.lower() == rev:
            return None
        from django.contrib.auth.models import User
        matches = list(User.objects.filter(email__iexact=email, is_active=True)
                       .order_by('pk')[:2])
        if not matches:
            return None                       # ordinary customer — no staff account
        if len(matches) > 1:
            # Two active staff share this email — ambiguous; fail closed rather
            # than silently bridge to an arbitrary one.
            return None
        return (matches[0], None)
