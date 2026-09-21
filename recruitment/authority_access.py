"""Who may see / sign an Authority to Recruit (CFO 2026-08-03; tiered 2026-09-02).

These documents carry a named individual's full pay structure, so access stays
tight. Originally it was a flat allow-list of five people. Under Unami's Hiring
SOP the signatories now depend on the role's tier, so access follows the
authority's OWN chain:

* Full-view roles — CEO, CFO, Human Capital — may see EVERY authority.
* Every other signatory (HR Business Partner, Finance Manager, Board Chair, and
  the per-role Hiring Manager) sees ONLY the authorities they are a signatory on
  — so, e.g., the Finance Manager never sees a C-suite package they do not sign.
* A superuser backstop keeps the break-glass / admin account from being locked
  out of a document it must sign.

Still deliberately NOT a broad title/role grant: being "an HR manager" or "an
executive" grants nothing on its own — you must be on the specific chain.
"""
from __future__ import annotations

from django.conf import settings

from .models import SIGNATORY_DIRECTORY, AuthorityToRecruit


def _email(user) -> str:
    return (getattr(user, 'email', '') or '').strip().lower()


def role_emails() -> set[str]:
    """Fixed role-holders who may open the Authorities area at all. Their exact
    per-authority visibility is then narrowed by visible_authorities()."""
    emails = {addr.lower() for _label, addr in SIGNATORY_DIRECTORY.values() if addr}
    cfg = getattr(settings, 'RECRUIT_BOARD_CHAIR', {}) or {}
    if cfg.get('email'):
        emails.add(cfg['email'].strip().lower())
    return emails


# Kept as a name some other code may import; the area allow-list is now role_emails.
def allowed_emails() -> set[str]:
    return role_emails()


# See EVERY authority (including C-suite packages). Everyone else sees only the
# authorities they are a signatory on.
FULL_VIEW_SLUGS = ('ceo', 'cfo', 'human_capital')


def full_view_emails() -> set[str]:
    return {SIGNATORY_DIRECTORY[s][1].lower()
            for s in FULL_VIEW_SLUGS
            if SIGNATORY_DIRECTORY.get(s) and SIGNATORY_DIRECTORY[s][1]}


def _is_hiring_manager_anywhere(email: str) -> bool:
    if not email:
        return False
    return AuthorityToRecruit.objects.filter(hiring_manager_email__iexact=email).exists()


def can_view(user) -> bool:
    """May this user open the Authorities area at all — a superuser backstop, a
    fixed role-holder, or a hiring manager on at least one authority. Ordinary
    staff (including HR staff not on any chain) get nothing."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    email = _email(user)
    return bool(email) and (email in role_emails() or _is_hiring_manager_anywhere(email))


def can_view_authority(user, authority: AuthorityToRecruit) -> bool:
    """May this user see THIS authority's pay data — a full-view role, or an
    actual signatory on it (which includes its own hiring manager)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    email = _email(user)
    if not email:
        return False
    if email in full_view_emails():
        return True
    return any(addr and email == addr for _s, _l, addr in authority.signatory_chain())


def visible_authorities(user):
    """Queryset of authorities this user may list — all for full-view roles and
    the superuser, otherwise only those they are a signatory on."""
    qs = AuthorityToRecruit.objects.select_related('tier').all()
    if getattr(user, 'is_superuser', False):
        return qs
    email = _email(user)
    if email and email in full_view_emails():
        return qs
    ids = [a.id for a in qs if can_view_authority(user, a)]
    return qs.filter(id__in=ids)


def can_sign(user, authority: AuthorityToRecruit) -> tuple[bool, str]:
    """(may_sign, reason_if_not). Only a signatory on THIS authority signs, only
    once, and only while the document is still open."""
    slug = authority.signatory_for(user)
    if not slug:
        return False, 'You are not a signatory on this authority.'
    if authority.status in (AuthorityToRecruit.Status.WITHDRAWN,
                            AuthorityToRecruit.Status.DECLINED):
        return False, f'This authority is {authority.get_status_display().lower()} — it cannot be signed.'
    existing = (authority.approvals or {}).get(slug) or {}
    if existing.get('decision'):
        return False, 'You have already recorded a decision on this authority.'
    return True, ''
