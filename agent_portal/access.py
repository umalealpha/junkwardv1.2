"""agent_portal/access.py — two-tier Agent Portal access (CFO 2026-07-07).

Motlatsi Molefe's requirement: only five named managers may open and manage the
Agent Portal; every other signed-in user may see ONLY their own Agent Profile
(read), nothing else. Passwords are handled by the staff email-login
(core/staff_login_views.py) — this module only decides WHO, by the signed-in
user's email. It never touches passwords.

The manager list is a code constant PLUS an env override
(AGENT_PORTAL_MANAGER_EMAILS, comma-separated) so a manager can be added or
removed with NO deploy — same no-deploy pattern as the /aware whitelist.
"""
from __future__ import annotations

import os

from rest_framework.permissions import BasePermission

# The five named managers (CFO directive, verified against the live user table
# 2026-07-07). Motlatsi + Bakang Taote are on the @insurance.co.bw entity.
MANAGER_EMAILS = {
    'pganesharajah@alphadirect.co.bw',   # Prathap Ganesharajah (CFO)
    'mmolefe@insurance.co.bw',           # Motlatsi Molefe
    'btaote@insurance.co.bw',            # Bakang Taote
    'bmhusiwa@alphadirect.co.bw',        # Bakang Mhusiwa
    # CFO directive 2026-07-30: Bakang signs in on the Unicoin entity address
    # too, and only his @alphadirect account was listed — so the portal 403'd
    # him whenever he used @insurance.co.bw. Both of his accounts now pass.
    'bmhusiwa@insurance.co.bw',          # Bakang Mhusiwa (Unicoin entity)
    'bbalasubramanian@alphadirect.co.bw',# Bharath Balasubramanian
    # CFO directive 2026-07-22: CEO + COO get portal access so they can view /
    # reveal the (masked) bank accounts. Reveal itself is limited to CFO/CEO/COO
    # in bank_unlock.can_reveal.
    'aiyer@alphadirect.co.bw',           # Arun Iyer (CEO)
    'arjuniyer@alphadirect.co.bw',       # Arjun Iyer (COO)
}


def manager_emails() -> set[str]:
    """Managers = the constant set + any AGENT_PORTAL_MANAGER_EMAILS env extras
    (comma-separated). All lower-cased for a case-insensitive match."""
    base = {e.lower() for e in MANAGER_EMAILS}
    extra = os.environ.get('AGENT_PORTAL_MANAGER_EMAILS', '')
    base |= {e.strip().lower() for e in extra.split(',') if e.strip()}
    return base


def is_manager(user) -> bool:
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return (getattr(user, 'email', '') or '').strip().lower() in manager_emails()


class IsAgentPortalManager(BasePermission):
    """Only the five named managers pass. Everyone else is blocked from every
    management/data endpoint; their sole entry is the `access` and `my-profile`
    actions, which override permission_classes to IsAuthenticated."""
    message = 'The Agent Portal is restricted to authorised managers.'

    def has_permission(self, request, view):
        return is_manager(request.user)

    def has_object_permission(self, request, view, obj):
        return is_manager(request.user)


def agent_for_user(user):
    """Best-effort match of a signed-in user to their Agent record, so a
    non-manager sees only their OWN profile. Match on full name (Agent.name is
    the natural key) case-insensitively; return None if there is no clean match
    (then the profile is simply empty — we never show one person another
    person's commission)."""
    from .models import Agent
    full = (user.get_full_name() or '').strip()
    if not full:
        return None
    return Agent.objects.filter(name__iexact=full).first()
