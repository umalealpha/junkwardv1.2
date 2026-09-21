"""Alpha Aware access control — exec whitelist (CFO directive 2026-06-30).

Available ONLY to: CEO, COO, CFO, pganesharajah, Finance Manager,
Claims Manager, Paul Beka. Whitelist is by email; extendable via the
ALPHA_AWARE_EXTRA_EMAILS env (comma-separated) without a deploy.
"""
from __future__ import annotations

import os

# Seeded per the CFO's roster. Claims Manager to be appended via
# ALPHA_AWARE_EXTRA_EMAILS once named.
ALPHA_AWARE_EMAILS = {
    'aiyer@alphadirect.co.bw',           # Arun Iyer — CEO
    'arjuniyer@alphadirect.co.bw',       # Arjun Iyer — COO
    'cfo@alphadirect.co.bw',             # CFO mailbox
    'pganesharajah@alphadirect.co.bw',   # Prathap Ganesharajah
    'excoboard@alphadirect.co.bw',       # EXCO (CFO working account)
    'omogomotsi@alphadirect.co.bw',      # Oprah Mogomotsi — Finance Manager
    'pbeka@alphadirect.co.bw',           # Paul Beka
    'ktshutlhedi@alphadirect.co.bw',     # Kago Tshutlhedi — Assistant Finance Manager (CFO 2026-07-06)
    'pkago@alphadirect.co.bw',           # Pako Kago (CFO 2026-07-06)
    'brasenyai@alphadirect.co.bw',       # Babusi Rasenyai — Operations (CFO 2026-07-07)
}


def allowed_emails() -> set[str]:
    extra = os.environ.get('ALPHA_AWARE_EXTRA_EMAILS', '')
    out = {e.strip().lower() for e in extra.split(',') if e.strip()}
    return {e.lower() for e in ALPHA_AWARE_EMAILS} | out


def user_allowed(user) -> bool:
    # Read-only QC/screenshot identity may VIEW Aware (CFO directive 2026-08-10:
    # the review-only auditor must see everything the CFO sees). Writes stay
    # blocked globally by core.token_auth for these usernames, so this is a
    # read-only grant to the QA tooling identity only — not to real users.
    from core.screenshot_bot import READ_ONLY_USERNAMES
    if getattr(user, 'username', '') in READ_ONLY_USERNAMES:
        return True
    email = (getattr(user, 'email', '') or getattr(user, 'username', '') or '').lower()
    return email in allowed_emails()
