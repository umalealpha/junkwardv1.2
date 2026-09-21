"""bonu/access.py — who may open the BONU section.

CFO 2026-08-11: *"give patience access to BONU asap she is bonu team lead"*.

Patience Phesodi's title is `operations`, and BONU was gated on
`UserProfile.can_view_financials` — a property derived from the title. So the only
way to let her in through the old gate was to make her finance, which would also
hand her the general ledger, the financial reports, the dashboards and the audit
log. She leads the BONU team; she does not need the GL.

So BONU access is now: **the financials gate OR a named BONU-team member.** The
named list is a code constant plus an env override (`BONU_TEAM_EMAILS`,
comma-separated) so the CFO can add or remove a BONU team member with NO deploy —
the same no-deploy pattern as `agent_portal.access.manager_emails` and the /aware
whitelist.

This widens BONU only. It grants nothing else, anywhere.
"""
from __future__ import annotations

import os

#: BONU team members who are not finance staff. Email-matched, lower-cased.
BONU_TEAM_EMAILS = {
    # CFO directive 2026-08-11 — BONU team lead, title `operations`.
    'pphesodi@alphadirect.co.bw',      # Patience Phesodi
    # CFO directive 2026-09-09 — Kelvin Kimani (IT) wrote the Legal claim-intake
    # and bill-capture spec that shipped that day, and could not open either of
    # the screens he specified: his title is `operations`, so
    # `can_view_financials` is False and the BONU gate refused him. Same
    # position Patience was in above, and the same answer: name him here rather
    # than make him finance, which would hand him the general ledger, the
    # financial reports and the audit log to test a legal-bills screen.
    'kkimani@alphadirect.co.bw',       # Kelvin Kimani
    # CFO directive 2026-09-15 — *"kindly give him full access to BONU"*. Karabo
    # Borupile's title is `claims_intern`, so `can_view_financials` is False and
    # the BONU gate refused him. Named here for the same reason as the two above:
    # making him finance to open BONU would also hand him the general ledger, the
    # financial reports and the audit log.
    'kborupile@alphadirect.co.bw',     # Karabo Borupile
    # CFO directive 2026-09-15 — Kelvin Kimani requested BONU Legal access for
    # Mbako Salani, who takes the legal work over from Kutlo Keitumele. The
    # request said Finance; Omni has his title as `operations`, so
    # `can_view_financials` is False and the BONU gate refused him. Named here
    # for the same reason as the three above: making him finance to open BONU
    # would also hand him the general ledger, the financial reports and the
    # audit log.
    'msalani@alphadirect.co.bw',       # Mbako Salani
}


def bonu_team_emails() -> set[str]:
    """The constant set plus any BONU_TEAM_EMAILS env extras."""
    base = {e.lower() for e in BONU_TEAM_EMAILS}
    extra = os.environ.get('BONU_TEAM_EMAILS', '')
    base |= {e.strip().lower() for e in extra.split(',') if e.strip()}
    from core.models import named_module_emails
    base |= named_module_emails('bonu')
    return base


def is_bonu_team(user) -> bool:
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return (getattr(user, 'email', '') or '').strip().lower() in bonu_team_emails()


def can_view_bonu(user) -> bool:
    """Superuser, or finance/management, or a named BONU team member."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    if is_bonu_team(user):
        return True
    from core.models import get_user_profile
    prof = get_user_profile(user)
    return bool(prof and prof.can_view_financials)


#: Call-centre agents who OPEN and track legal claims but must see NO financials.
#: A claim intake screen needs the person who answers the member's call — usually
#: an `operations` title — yet handing them `can_view_financials` would open the
#: whole BONU book (spend, savings, the ledger tiles) to them. So intake has its
#: own narrow gate: the BONU viewers, PLUS a named call-centre list, extendable
#: with NO deploy via BONU_INTAKE_EMAILS — the same pattern as bonu_team_emails.
BONU_INTAKE_EMAILS: set[str] = set()


def bonu_intake_emails() -> set[str]:
    """The constant set plus any BONU_INTAKE_EMAILS env extras."""
    base = {e.lower() for e in BONU_INTAKE_EMAILS}
    extra = os.environ.get('BONU_INTAKE_EMAILS', '')
    base |= {e.strip().lower() for e in extra.split(',') if e.strip()}
    from core.models import named_module_emails
    base |= named_module_emails('bonu_intake')
    return base


def is_bonu_intake(user) -> bool:
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return (getattr(user, 'email', '') or '').strip().lower() in bonu_intake_emails()


def can_capture_claim(user) -> bool:
    """Open / track a legal claim. Anyone who can VIEW BONU (finance / BONU team /
    superuser), OR a named call-centre agent. An intake-only agent gets the case
    register and nothing financial — that separation is the whole point."""
    if can_view_bonu(user):
        return True
    return is_bonu_intake(user)
