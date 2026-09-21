"""Internal Audit access control — the independence layer.

Per the Internal Audit Module spec (GIAS Standards 2.1/2.2, 5.1/5.2) the
function must hold EXCLUSIVE create/edit rights over audit content. Finance/IT
build and run the platform but hold no standing edit access to audit content.

Two rosters, both by email, both extendable via env without a deploy:

  EDITORS  — Internal Audit (CAE + auditors). Full create/edit/approve.
             Seed: the Internal Auditor. Extend via INTERNAL_AUDIT_EDITOR_EMAILS.

  VIEWERS  — CEO, COO, CFO and the Audit/EXCO board. VIEW-ONLY on issued
             content + the dashboard. Extend via INTERNAL_AUDIT_VIEWER_EMAILS.

INDEPENDENCE RULE (load-bearing): a Django superuser is NOT automatically an
editor. Superusers/platform admins may READ (for support and the dashboard)
but the write path checks the EDITOR roster only — so "Finance can't edit its
way out of a finding" holds even for the person who runs the box. Seeding /
schema is done at the platform layer (management command, admin), which is the
one place platform-admin power is legitimate per the spec's access matrix.
"""
from __future__ import annotations

import os

# --- Internal Audit — full create/edit/approve on audit content -------------
INTERNAL_AUDIT_EDITOR_EMAILS = {
    'omogomotsi@alphadirect.co.bw',      # Oprah Mogomotsi — Internal Auditor (CAE)
    # Non-human QA render-bot for the pre-ship "eyes-on" gate (CFO blessed
    # 2026-07-24). Editor rights let the automated screenshot harness open the
    # edit-flow modals (New engagement / New finding) at laptop width to catch
    # layout bugs before staff do. It must only ever touch TEST-* engagements;
    # Oprah remains the sole HUMAN editor, so the independence control stands.
    'omni@alphadirect.co.bw',            # Omni QA bot — eyes-on gate only
}

# --- View-only: CEO, COO, CFO, Audit Committee / EXCO -----------------------
INTERNAL_AUDIT_VIEWER_EMAILS = {
    'aiyer@alphadirect.co.bw',           # Arun Iyer — CEO
    'arjuniyer@alphadirect.co.bw',       # Arjun Iyer — COO
    'cfo@alphadirect.co.bw',             # CFO mailbox
    'pganesharajah@alphadirect.co.bw',   # Prathap Ganesharajah — CFO
    'excoboard@alphadirect.co.bw',       # EXCO / Audit Committee board mailbox
}


def _extra(env_name: str) -> set[str]:
    raw = os.environ.get(env_name, '')
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def editor_emails() -> set[str]:
    return {e.lower() for e in INTERNAL_AUDIT_EDITOR_EMAILS} | _extra('INTERNAL_AUDIT_EDITOR_EMAILS')


def viewer_emails() -> set[str]:
    return {e.lower() for e in INTERNAL_AUDIT_VIEWER_EMAILS} | _extra('INTERNAL_AUDIT_VIEWER_EMAILS')


def _email_of(user) -> str:
    return (getattr(user, 'email', '') or getattr(user, 'username', '') or '').lower()


def _identifiers(user) -> set[str]:
    """Every identifier a user might present, so a roster entry matches whether
    the SSO layer populated .email or .username, and whether it carried the full
    address or just the local-part. All @alphadirect.co.bw, so local-part is
    a safe key."""
    ids: set[str] = set()
    for raw in (getattr(user, 'email', ''), getattr(user, 'username', '')):
        v = (raw or '').strip().lower()
        if not v:
            continue
        ids.add(v)
        ids.add(v.split('@', 1)[0])   # local-part
    return ids


def _roster_keys(emails: set[str]) -> set[str]:
    keys: set[str] = set()
    for e in emails:
        keys.add(e)
        keys.add(e.split('@', 1)[0])
    return keys


def is_editor(user) -> bool:
    """True only for the Internal Audit roster. Superuser status does NOT grant
    edit rights — that is the point of the independence control."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    return bool(_identifiers(user) & _roster_keys(editor_emails()))


def can_view(user) -> bool:
    """Editors, view-only execs, and platform superusers (support) may read."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    allowed = _roster_keys(editor_emails()) | _roster_keys(viewer_emails())
    return bool(_identifiers(user) & allowed)
