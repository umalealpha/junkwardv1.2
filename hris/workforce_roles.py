"""
hris/workforce_roles.py

Who carries the lighter MANAGER weekday hours requirement (4.5h vs 6.5h),
CFO directive 2026-07-23: "claims manager, underwriting manager, Bharath,
exco, fm — and that's all."

The set is CLOSED and explicit on purpose. A person is on the manager rate iff
ANY of:
  1. job_title is exactly 'Claims Manager' or 'Underwriting Manager';
  2. job_title is a 'Chief ...' officer  → EXCO / C-suite (CEO, CFO, CHCO);
  3. their email local-part is in the allowlist — Bharath, the Finance Manager
     (Oprah), and the EXCO / C-suite login accounts. The allowlist is EXTENDED
     (never shrunk) by the env var OMNI_HALFDAY_MANAGERS (comma-separated email
     local-parts) so HR / the CFO can add a person with NO deploy.

Deliberately NOT on the manager rate (they stay 6.5h): Operations Manager,
Parts Manager, Senior Manager, Internal Audit Manager, team leaders, and the
`finance_manager` PERMISSION title — that title is held by several people for
JE approval and is NOT the Finance Manager position, so it is not used here.

Pure attribute reads + env; no query is required to classify a person, so this
never crashes a brief (any error → False = the normal 6.5h rule).
"""
from __future__ import annotations

import os

# Exact job titles (normalised, lower-cased) that carry the manager rate.
MANAGER_JOB_TITLES = {'claims manager', 'underwriting manager'}

# Bharath + the Finance Manager + the EXCO / C-suite login accounts, by email
# local-part. Env OMNI_HALFDAY_MANAGERS adds to (never removes from) this set.
_DEFAULT_MANAGER_LOCAL_PARTS = {
    'bbalasubramanian',   # Bharath Balasubramanian
    'omogomotsi',         # Goabaone Oprah Mogomotsi — Finance Manager (FM)
    'excoboard',          # EXCO board / CFO working account
    'aiyer',              # Arun Iyer — CEO
    'arjuniyer',          # Arjun Iyer — COO
    'pganesharajah',      # Prathap Ganesharajah — CFO
    'cfo',                # CFO mailbox
}


def _norm(s: str) -> str:
    return ' '.join((s or '').split()).lower()


def manager_local_parts() -> set:
    """The email-local-part allowlist, extended by OMNI_HALFDAY_MANAGERS."""
    extra = {p.strip().lower()
             for p in (os.environ.get('OMNI_HALFDAY_MANAGERS') or '').split(',')
             if p.strip()}
    return set(_DEFAULT_MANAGER_LOCAL_PARTS) | extra


def is_manager_hours_employee(emp) -> bool:
    """True if this payroll.Employee is on the 4.5h manager weekday rate."""
    try:
        if emp is None:
            return False
        jt = _norm(getattr(emp, 'job_title', ''))
        if jt in MANAGER_JOB_TITLES:
            return True
        if jt.startswith('chief '):          # EXCO / C-suite officer
            return True
        email = (getattr(emp, 'email', '') or '').lower()
        local = email.split('@', 1)[0] if email else ''
        return bool(local) and local in manager_local_parts()
    except Exception:  # noqa: BLE001 — classification must never crash a brief
        return False


def is_manager_hours_profile(profile) -> bool:
    """True if this HRISProfile's employee is on the 4.5h manager weekday rate."""
    return is_manager_hours_employee(getattr(profile, 'employee', None))


# Roles that are not on Time Doctor at all, so a missing tracker match for them
# means "not applicable", never "broken tracker" (CFO instruction, 11 Aug 2026).
# COO added same day — the CFO extended the brief to Arjun (COO) after the
# ceo/cfo change went in.
NO_TRACKER_TITLES = {'ceo', 'cfo', 'coo'}


def no_tracker_title(profile) -> str:
    """The authoritative role for tracker purposes: `UserProfile.title`.

    Deliberately NOT `Employee.job_title`. job_title is free text that grants
    nothing and varies by entity; UserProfile.title is the field the rest of the
    system already trusts for role decisions. Returns '' when it cannot be read —
    the caller then treats the person as tracked, which is the safe default
    (they keep the existing behaviour rather than silently losing their hours).
    """
    try:
        emp = getattr(profile, 'employee', None)
        user = getattr(emp, 'user', None)
        if user is None:
            return ''
        from core.models import UserProfile
        up = UserProfile.objects.filter(user=user).only('title').first()
        return (getattr(up, 'title', '') or '').strip().lower()
    except Exception:  # noqa: BLE001 — classification must never crash a brief
        return ''
