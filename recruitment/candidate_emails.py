"""
recruitment/candidate_emails.py — emails sent to job applicants (the public).

These go to external candidates, so: no internal "do-not-reply" banner
(no_reply=False) and no CFO CC (cc_cfo=False). Every function is best-effort and
never raises — a mail failure must not break an application or a stage change.
"""
from __future__ import annotations

import logging

from core.notifications import send_html_with_cfo_cc

log = logging.getLogger("recruitment.candidate_emails")

_NAVY = "#1D3270"
_ORANGE = "#F47C20"
_FROM = "Alpha Direct Careers <omni@alphadirect.co.bw>"


def _first_name(full_name: str) -> str:
    return (full_name or "").strip().split(" ")[0] or "there"


def _wrap(heading: str, paragraphs: list[str]) -> str:
    body = "".join(
        f'<p style="margin:0 0 14px;color:#334155;font-size:15px;line-height:1.6">{p}</p>'
        for p in paragraphs
    )
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto">
  <div style="background:{_NAVY};padding:18px 22px;border-radius:12px 12px 0 0">
    <span style="color:#fff;font-weight:800;font-size:18px">Alpha Direct</span>
    <span style="color:{_ORANGE};font-weight:700;font-size:18px"> Careers</span>
  </div>
  <div style="background:#fff;border:1px solid #e6e9ef;border-top:none;border-radius:0 0 12px 12px;padding:24px 22px">
    <h1 style="margin:0 0 16px;color:{_NAVY};font-size:19px">{heading}</h1>
    {body}
    <p style="margin:18px 0 0;color:#334155;font-size:15px">Regards,<br>Alpha Direct HR</p>
  </div>
  <p style="text-align:center;color:#94a3b8;font-size:12px;margin:14px 0">
    Alpha Direct Insurance, Gaborone, Botswana
  </p>
</div>"""


def send_ack(candidate, req, application=None) -> None:
    """Instant 'we received your application' to the applicant.

    When `application` is given, embed the no-login status link (CFO 2026-08-26,
    H2) so the candidate can check where their application stands at any time.
    """
    email = (getattr(candidate, "email", "") or "").strip()
    if not email:
        return
    name = _first_name(candidate.full_name)
    title = req.title
    paras = [
        f"Hi {name},",
        f"Thank you for applying for the {title} role at Alpha Direct Insurance.",
        "We have your CV and our HR team will review it. If your experience fits "
        "the role, we will contact you on the email or phone you gave us.",
    ]
    status_line = ""
    if application is not None:
        from .candidate_status import status_url
        link = status_url(application)
        paras.append(
            f'You can check the status of your application at any time here: '
            f'<a href="{link}" style="color:{_ORANGE};font-weight:700">Check my application status</a>.')
        status_line = f"\nCheck your application status: {link}\n"
    paras.append("You do not need to do anything else for now.")
    html = _wrap("Your application has been received", paras)
    text = (f"Hi {name},\n\nThank you for applying for the {title} role at Alpha "
            "Direct Insurance. We have your CV and our HR team will review it. If "
            f"your experience fits, we will contact you.\n{status_line}\nRegards,\nAlpha Direct HR")
    try:
        send_html_with_cfo_cc(
            subject=f"Your application has been received: {title}",
            html=html, to=[email], text_fallback=text,
            from_email=_FROM, no_reply=False, cc_cfo=False,
        )
    except Exception:  # noqa: BLE001
        log.exception("ack email failed for %s", email)


def send_decline(candidate, req) -> None:
    """Courteous decline when an applicant is moved to 'rejected'."""
    email = (getattr(candidate, "email", "") or "").strip()
    if not email:
        return
    name = _first_name(candidate.full_name)
    title = req.title
    html = _wrap(
        "Update on your application",
        [
            f"Hi {name},",
            f"Thank you for your interest in the {title} role at Alpha Direct "
            "Insurance, and for the time you put into your application.",
            "After careful review, we will not be taking your application further "
            "on this occasion. We wish you well, and we encourage you to apply for "
            "future roles that match your experience.",
        ],
    )
    text = (f"Hi {name},\n\nThank you for your interest in the {title} role at Alpha "
            "Direct Insurance. After careful review, we will not be taking your "
            "application further on this occasion. We wish you well.\n\nRegards,\n"
            "Alpha Direct HR")
    try:
        send_html_with_cfo_cc(
            subject=f"Update on your application: {title}",
            html=html, to=[email], text_fallback=text,
            from_email=_FROM, no_reply=False, cc_cfo=False,
        )
    except Exception:  # noqa: BLE001
        log.exception("decline email failed for %s", email)
