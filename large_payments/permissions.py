"""Who may raise and who may decide a large-payment authorisation request.

CFO 2026-09-11: "Finance approvers + CFO" may raise one; "it sits for MY
approval" — the CFO alone decides.

Both groups are READ FROM taskboard's existing definitions rather than restated
here. The finance approver list moved once already (settings override, 2026-07-23)
and a second copy of it in this module would have quietly gone stale — a person
removed from the payment approvers would have kept the power to raise a CEO
authorisation request, which is the same power wearing a different hat.
"""
from __future__ import annotations

from taskboard.payment_views import (_cfo_user, _first_approver_emails,
                                     _is_cfo)


def cfo_user():
    return _cfo_user()


def is_cfo(user) -> bool:
    """The CFO, OR a superuser — taskboard's definition, used for raising only."""
    return _is_cfo(user)


def finance_approver_emails() -> set[str]:
    """The stage-1 payment approvers, honouring the same settings override."""
    return _first_approver_emails()


def can_raise(user) -> bool:
    """Finance approvers and the CFO. Anyone else gets a plain refusal."""
    if not (user and user.is_authenticated and user.is_active):
        return False
    if is_cfo(user):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    return bool(email) and email in finance_approver_emails()


def can_decide(user) -> bool:
    """The CFO HIMSELF approves or rejects. Not any superuser, not delegated.

    Fable 5.1, 2026-09-11: taskboard's _is_cfo() returns True for ANY superuser,
    so reusing it here would have handed the decision to everyone holding
    superuser in Omni — a group that includes the QC robots. That is acceptable
    for RAISING a request and wrong for signing one off, because the CFO's words
    were "it sits for MY approval". So this leg matches the person, and there is
    no superuser fallback: if the named account does not exist, NOBODY can
    decide, which is the safe way for this to fail.
    """
    if not (user and user.is_authenticated and user.is_active):
        return False
    cfo = cfo_user()
    if not cfo:
        return False
    # _cfo_user() falls back to "any superuser" when the named account is
    # missing. Refuse that fallback here rather than inherit it.
    if (cfo.username or '').lower() != 'pganesharajah':
        return False
    return user.id == cfo.id
