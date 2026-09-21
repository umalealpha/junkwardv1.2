"""
core/access_check.py — "can this person actually open that Omni page?"

CFO 2026-09-05: a request went out asking two staff to sign an SOP in Omni.
Both could, as it turned out — but the CFO's point stands: nothing should ASK a
person to do something in Omni unless Omni has first checked that they can get
there. This is that check, in one place, so the email senders (Omni's own
notifications and the CFO-mailbox sender tool) can refuse to send and say why.

It is deliberately conservative and READ-ONLY: it answers ok / not ok with a
plain-English reason. It never grants anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.contrib.auth import get_user_model

_OMNI_LINK = re.compile(r'https?://omni\.alphadirect\.co\.bw(/[^\s"\'<>)]*)?', re.I)


@dataclass
class AccessVerdict:
    email: str
    ok: bool
    reason: str
    path: str = ""

    def as_dict(self) -> dict:
        return {
            "email": self.email,
            "ok": self.ok,
            "reason": self.reason,
            "path": self.path,
        }


def omni_paths_in(text: str) -> list[str]:
    """Every Omni page path mentioned in a piece of text (deduplicated, in order)."""
    seen, out = set(), []
    for m in _OMNI_LINK.finditer(text or ""):
        path = (m.group(1) or "/").split("?")[0].split("#")[0] or "/"
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _user_for(email: str):
    User = get_user_model()
    e = (email or "").strip().lower()
    if not e:
        return None
    return User.objects.filter(email__iexact=e, is_active=True).first()


def can_reach(email: str, path: str = "/") -> AccessVerdict:
    """Does the person behind *email* have a working Omni login, and does that
    login reach *path*? Gates mirror the real ones:

      /hris/…, /payroll/…, /hr-analytics/…  → core.hris_access.user_can_access_hris
      /api/v1/timedoctor/tracking-setup, /hris/tracking-setup → workforce _can_link
      /sop-bank, /my-omni, /dashboard, everything else → any active login

    A person with an employee record but NO login is 'not ok' — they cannot sign
    in, so asking them to click anything in Omni is pointless.
    """
    user = _user_for(email)
    if user is None:
        from payroll.models import Employee

        emp = Employee.objects.filter(email__iexact=(email or "").strip()).first()
        if emp is not None:
            return AccessVerdict(
                email,
                False,
                f"{emp.full_name} is on the staff list but has no active Omni "
                "login yet — they cannot sign in to do this.",
                path,
            )
        return AccessVerdict(
            email, False, "No Omni login exists for this address.", path
        )
    if not (user.has_usable_password() or _is_sso_capable(user)):
        return AccessVerdict(
            email,
            False,
            "This login has no password set and cannot sign in with Microsoft.",
            path,
        )

    p = (path or "/").lower()
    if p.startswith(
        (
            "/hris/tracking-setup",
            "/api/v1/timedoctor/tracking-setup",
            "/api/v1/timedoctor/confirm-match",
        )
    ):
        from hris.workforce_views import _can_link

        if not _can_link(user):
            return AccessVerdict(
                email,
                False,
                "Only the CFO, Arun, Arjun and the HR team can open the "
                "who-tracks panel.",
                path,
            )
        return AccessVerdict(email, True, "Has access.", path)
    if p.startswith(("/hris", "/payroll", "/hr-analytics")):
        from core.hris_access import user_can_access_hris

        if not user_can_access_hris(user):
            # Self-service HRIS pages ARE open to every employee.
            if p.startswith(
                (
                    "/hris/leave",
                    "/hris/my",
                    "/hris/leave-encashment",
                    "/hris/payslips",
                    "/hris/monthly-feedback",
                )
            ):
                return AccessVerdict(email, True, "Has access (self-service).", path)
            return AccessVerdict(
                email, False, "This login does not have HR module access.", path
            )
    return AccessVerdict(email, True, "Has access.", path)


def _is_sso_capable(user) -> bool:
    """A Microsoft-SSO staff login has no Django password but signs in fine. Any
    alphadirect group domain with a real local part counts."""
    email = (getattr(user, "email", "") or "").lower()
    return bool(email) and email.split("@")[-1] in {
        "alphadirect.co.bw",
        "alphadirect.co.zm",
        "alphadirect.co.za",
        "insurance.co.bw",
        "theriskco.com",
        "quantum.co.bw",
        "motorliquidators.co.bw",
    }


def check_recipients(emails, text: str, paths=None) -> list[AccessVerdict]:
    """One verdict per recipient per Omni page mentioned in *text* (or *paths*).
    No Omni page in the text → no verdicts (nothing to check)."""
    paths = list(paths) if paths else omni_paths_in(text)
    out: list[AccessVerdict] = []
    for e in emails or []:
        for path in paths or []:
            out.append(can_reach(e, path))
    return out
