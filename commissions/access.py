"""commissions/access.py — who may act at each stage of the 3-stage chain.

CFO 2026-07-15: agent submits → 1st review (Bokani Makosha / Tlamelo Chimidza)
→ 2nd review (Pako / Kago) → final approval (CFO). Each stage has a roster.

A roster is resolved from (in order): an env allowlist of emails (no-deploy, the
RELIABLE mechanism) + a code list of names (the bridge until emails are set).
Set the exact people via COMMISSIONS_STAGE1_EMAILS / _STAGE2_EMAILS /
_FINAL_EMAILS (comma-separated) — the name lists below are only a fallback and
should be replaced with verified emails before wide use.
"""
from __future__ import annotations

import os

from rest_framework.permissions import BasePermission

STAGE1, STAGE2, FINAL = 'stage1', 'stage2', 'final'
STAGES = (STAGE1, STAGE2, FINAL)
PAYROLL = 'payroll'   # processes an approved submission (not a review stage)

# Which stage acts on a submission in each awaiting-review status.
STATUS_STAGE = {
    'submitted': STAGE1,
    'second_review': STAGE2,
    'final_review': FINAL,
}

# Fallback name rosters (lower-case). Full names match exactly; single tokens
# (e.g. 'pako') match any of the user's name parts.
# Full-name fallback (matched EXACTLY, not by token — a single first name like
# "Pako" is ambiguous on the real directory: it hits a junior claims associate,
# a finance manager and an external user, so those stages are email-pinned only).
_ROSTER_NAMES = {
    STAGE1: {'bokani makosha', 'tlamelo chimidza'},
    STAGE2: set(),                          # email-only — set COMMISSIONS_STAGE2_EMAILS (which Pako + which Kago)
    FINAL: {'prathap ganesharajah'},
    PAYROLL: set(),                         # email-only — set COMMISSIONS_PAYROLL_EMAILS
}
# CFO directive 2026-09-08, given twice and reaffirmed after the separation-of-
# duties consequence was put to him: these five get FULL, unlimited access to the
# commission module — every review stage, payroll and export. Pinned by EMAIL
# (never by first name: "Pako" and "Kago" are two different people — Pako Kago,
# Senior Accountant, and Kago Tshutlhedi, Assistant Finance Manager — and the CFO
# confirmed he means both).
#
# What this grants that a reviewer role does not: FINAL lets any of them approve
# a submission outright from ANY awaiting stage (service.final_approve short-
# circuits, CFO 2026-08-18), and can_export follows FINAL. The two guards that
# still hold for everyone are in service.review(): nobody reviews their own
# submission or one that pays them, and nobody reviews the same submission at two
# stages.
# 🔴 These are EMAIL addresses, and at Alpha Direct the email is NOT the username:
# Rose signs in as 'rose.mokgware' but her address is 'rmokgware@'. Deriving the
# address from the username silently granted nothing to her or to Keetile — the
# roster matched no one and the screen simply refused them (caught on prod
# 2026-09-08, minutes after the first deploy). Read the address off the user
# record; never assume it.
_FULL_ACCESS_EMAILS = {
    # CFO directive 2026-09-16: Tlamelo was the only one of the six on first
    # review alone — and the busiest reviewer on the board (20 of the 29 first
    # reviews). Put to the CFO with the full access matrix in front of him; he
    # chose to level him up rather than cut the others down, and to leave Rose,
    # Keetile and Bokani exactly as the 2026-09-08 directive left them.
    'tchimidza@alphadirect.co.bw',    # Tlamelo Chimidza — 1st-stage reviewer
    'rmokgware@alphadirect.co.bw',    # Rose Mokgware — Accounts Assistant (raised the request)
    'kmokhendo@alphadirect.co.bw',    # Keetile Mokhendo — Senior Accountant
    'pkago@alphadirect.co.bw',        # Pako Kago — Senior Accountant
    'ktshutlhedi@alphadirect.co.bw',  # Kago Tshutlhedi — Assistant Finance Manager
    'bmakosha@alphadirect.co.bw',     # Bokani Makosha — Senior Data Analyst
}

_KNOWN_EMAILS = {
    STAGE1:  set(_FULL_ACCESS_EMAILS),
    STAGE2:  set(_FULL_ACCESS_EMAILS),
    # PAYROLL is NOT part of the full-access grant (CFO 2026-09-16, given
    # after the access matrix was put to him): Keetile Mokhendo, Rose Mokgware,
    # Bokani Makosha and Tlamelo Chimidza must NOT have it. Processing an
    # approved submission into payroll stays with the two Finance people the
    # env allowlist names — Pako Kago and Kago Tshutlhedi — and is listed here
    # too so the roster survives an empty env.
    PAYROLL: {'pkago@alphadirect.co.bw', 'ktshutlhedi@alphadirect.co.bw'},
    FINAL:   {'pganesharajah@alphadirect.co.bw'} | _FULL_ACCESS_EMAILS,   # CFO + the five
}
_ENV_VAR = {
    STAGE1: 'COMMISSIONS_STAGE1_EMAILS',
    STAGE2: 'COMMISSIONS_STAGE2_EMAILS',
    FINAL: 'COMMISSIONS_FINAL_EMAILS',
    PAYROLL: 'COMMISSIONS_PAYROLL_EMAILS',
}


def _stage_emails(stage: str) -> set[str]:
    base = {e.lower() for e in _KNOWN_EMAILS.get(stage, set())}
    base |= {e.strip().lower() for e in os.environ.get(_ENV_VAR[stage], '').split(',') if e.strip()}
    return base


def can_review_stage(user, stage: str) -> bool:
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    if email and email in _stage_emails(stage):
        return True
    full = (user.get_full_name() or '').strip().lower()
    # EXACT full-name match only — no single-token match (too ambiguous on the
    # real directory). Email is the reliable, unambiguous mechanism.
    return bool(full) and full in _ROSTER_NAMES[stage]


def stage_users(stage: str) -> list:
    """Every active user who may review at `stage`.

    Candidates come from the same two sources the roster is defined by (the env
    email allowlist and the fallback name list) and each candidate is then
    confirmed by **can_review_stage** itself — one predicate decides who is a
    reviewer, so this can never drift from what the review action allows
    (the two-parsers trap that once let a payment past its cap).
    """
    from django.contrib.auth.models import User

    emails = _stage_emails(stage)
    names = {n for n in _ROSTER_NAMES.get(stage, set())}
    candidates = {}
    if emails:
        for u in User.objects.filter(is_active=True, email__in=list(emails)):
            candidates[u.id] = u
    if names:
        for u in User.objects.filter(is_active=True):
            full = f'{u.first_name} {u.last_name}'.strip().lower()
            if full in names:
                candidates[u.id] = u
    return [u for u in candidates.values() if can_review_stage(u, stage)]


def user_stages(user) -> set[str]:
    return {s for s in STAGES if can_review_stage(user, s)}


def is_reviewer(user) -> bool:
    """Anyone on any stage roster — may VIEW submissions + the queue."""
    return bool(user_stages(user))


def _export_emails() -> set[str]:
    """Extra people allowed to DOWNLOAD the payout/statement export — an email
    allowlist (the same reliable, no-deploy mechanism the stage rosters use).
    Set COMMISSIONS_EXPORT_EMAILS (comma-separated). This grants export ONLY;
    it does NOT make anyone a reviewer or a final approver (kept separate on
    purpose — download access is not payout sign-off authority)."""
    return {e.strip().lower()
            for e in os.environ.get('COMMISSIONS_EXPORT_EMAILS', '').split(',')
            if e.strip()}


def can_export(user) -> bool:
    """The final approver (CFO) / superuser, plus anyone on the export
    allowlist, downloads the payout file."""
    if bool(getattr(user, 'is_superuser', False)) or can_review_stage(user, FINAL):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    return bool(email) and email in _export_emails()


def is_payroll(user) -> bool:
    """Who may mark an approved submission as payroll-processed."""
    return bool(getattr(user, 'is_superuser', False)) or can_review_stage(user, PAYROLL)


def stage_of(submission) -> str | None:
    """Which stage a submission is awaiting (None if not in a review state)."""
    return STATUS_STAGE.get(submission.status)


class IsCommissionsReviewer(BasePermission):
    message = 'Commission review is restricted to the approval-chain reviewers.'

    def _qc(self, user) -> bool:
        # Read-only QC/screenshot identity may VIEW commissions (CFO directive
        # 2026-08-10). Writes stay blocked globally by core.token_auth for these
        # usernames, so reviewer-only WRITE actions remain refused — this only
        # lets the read-only auditor SEE the reviewer views.
        from core.screenshot_bot import READ_ONLY_USERNAMES
        return getattr(user, 'username', '') in READ_ONLY_USERNAMES

    def has_permission(self, request, view):
        return self._qc(request.user) or is_reviewer(request.user)

    def has_object_permission(self, request, view, obj):
        return self._qc(request.user) or is_reviewer(request.user)


def agent_for_user(user):
    """Match a signed-in user to their CommissionAgent — by email first, then by
    full name. Returns None on no clean match (never another person's data)."""
    from .models import CommissionAgent
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    email = (getattr(user, 'email', '') or '').strip().lower()
    if email:
        a = CommissionAgent.objects.filter(email__iexact=email).first()
        if a:
            return a
    full = (user.get_full_name() or '').strip()
    if not full:
        return None
    return CommissionAgent.objects.filter(name__iexact=full).first()
