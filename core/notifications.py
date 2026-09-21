"""
core/notifications.py

Email helpers for the approval workflow. Each helper:
  - Picks the right recipient set (approver titles, minus the requester
    and minus any approver who has already signed)
  - Builds a short subject + body with a deep link, sent via
    send_with_cfo_cc (HTML envelope + CFO cc, per house rule)
  - Failures are swallowed and logged so a flaky SMTP can never block
    a JE submission

When settings.NOTIFICATIONS_ENABLED is False, every helper short-circuits.
The default email backend is console (logs to stdout), so dev environments
work without any SMTP configuration.
"""

from __future__ import annotations

import logging
from datetime import time, timedelta
from typing import Iterable, Optional

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.db import transaction
from django.utils import timezone

# CFO directive 2026-06-03: every email going out from omni or on the CFO's
# behalf must be HTML rich-format by default. Outlook only renders our
# table palette / status pills properly in HTML. Plain-text-only is opt-in
# via html_body='' (empty string) per call.
_HTML_DEFAULT_CSS = """
  body { font-family: 'Segoe UI', Arial, sans-serif; color: #1F2937; font-size: 14px; line-height: 1.45; max-width: 900px; }
  h1, h2 { color: #0D1B2A; border-bottom: 2px solid #F4A623; padding-bottom: 6px; margin-top: 24px; }
  h2 { font-size: 17px; }
  h3 { color: #0D1B2A; margin-top: 18px; font-size: 14px; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0 14px; font-size: 13px; }
  th { background: #0D1B2A; color: #fff; padding: 7px 10px; text-align: left; font-weight: 600; }
  td { padding: 6px 10px; border-bottom: 1px solid #E5E7EB; vertical-align: top; }
  tr:nth-child(even) td { background: #F9FAFB; }
  .ok    { color: #059669; font-weight: 600; }
  .pend  { color: #92400E; font-weight: 600; }
  .miss  { color: #DC2626; font-weight: 600; }
  code   { background: #F3F4F6; padding: 1px 5px; border-radius: 3px; font-size: 12px; color: #0D1B2A; }
  .small { color: #6B7280; font-size: 12px; }
  .banner { background: #ECFDF5; border-left: 4px solid #059669; padding: 12px 16px; margin: 12px 0; border-radius: 4px; }
  pre { background: #F3F4F6; padding: 10px 12px; border-radius: 4px; font-size: 12px; overflow-x: auto; }
"""


def wrap_plain_as_html(text: str) -> str:
    """Convert a plain-text body into the Alpha Direct HTML envelope.

    Preserves line breaks (each line becomes a <br>) and wraps the whole
    thing in the house style. Used when a caller passed body= without
    html_body= — keeps the HTML-default rule from breaking legacy callers.
    """
    from html import escape
    safe = escape(text or '').replace('\n', '<br>\n')
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{_HTML_DEFAULT_CSS}</style></head>'
        f'<body><div style="white-space: normal;">{safe}</div></body></html>'
    )


log = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(getattr(settings, 'NOTIFICATIONS_ENABLED', True))


# CFO directive 2026-05-25: every outbound email I (Claude) send on
# Prathap's behalf must CC excoboard@ so the CFO has a copy in his
# Inbox. Override via MANDATORY_CFO_CC in settings if needed.
_DEFAULT_CFO_CC = 'excoboard@alphadirect.co.bw'

# CFO directive 2026-05-25 (2): "omni has its own email id" — outbound
# automation should send from omni@alphadirect.co.bw, not excoboard@.
# Override via OMNI_FROM_EMAIL in settings if needed.
_DEFAULT_OMNI_FROM = 'Omni ERP <omni@alphadirect.co.bw>'

# CFO directive 2026-05-25 (3): Arun Iyer and Arjun Iyer are NOT
# decision-makers and must NOT be CC'd on any automated outbound mail
# unless the CFO names them explicitly in the call. Blocklist them
# server-side so a bad caller cannot leak to them.
_NEVER_CC = {
    'aiyer@alphadirect.co.bw',
    'arjuniyer@alphadirect.co.bw',
    # CFO directive 2026-07-23: admin@ is a shared mailbox worked by junior
    # staff — approval notifications must NEVER be routed to it. Stripped from
    # both TO and CC so no workflow email (leave, encashment, incentives,
    # payments, POs, …) can ever land there, whatever a record points at.
    'admin@alphadirect.co.bw',
}


# ─── "Do not reply" banner (CFO directive 2026-08-07) ───────────────────────
# Staff reply to system emails instead of logging the issue, so the answer lands
# in one person's inbox and nothing reaches the fix-it list. Every outbound email
# now carries a red banner telling them not to reply, with a button into Omni.
#
# ON by default. Pass no_reply=False for an email that genuinely asks the
# recipient a question and expects an answer back — a "do not reply" banner on
# an email that asks for a reply is worse than no banner at all.
# INTERNAL RECIPIENTS ONLY (CFO 2026-08-07). A customer, broker or regulator
# cannot log into Omni, so telling them to "log it in Omni" is nonsense — and it
# would look unprofessional on a policy or claims email. The banner is added only
# when EVERY recipient (to + cc + bcc) is on an Alpha Direct domain.
_NO_REPLY_MARK = 'data-omni-noreply'
_DEFAULT_INTERNAL_DOMAINS = [
    'alphadirect.co.bw', 'alphadirect.co.za', 'alphadirect.co.zm',
    'insurance.co.bw', 'motorliquidators.co.bw', 'cuberoute.co.bw',
]


def _domain_of(addr: str) -> str:
    """'Name <a@b.c>' or 'a@b.c' -> 'b.c'."""
    a = (addr or '').strip()
    if '<' in a and '>' in a:
        a = a[a.rfind('<') + 1:a.rfind('>')]
    return a.rpartition('@')[2].strip().lower()


def all_internal(addresses) -> bool:
    """True only if every address given is on an Alpha Direct domain."""
    doms = {d.strip().lower()
            for d in getattr(settings, 'INTERNAL_EMAIL_DOMAINS', _DEFAULT_INTERNAL_DOMAINS)
            if (d or '').strip()}
    addrs = [a for a in (addresses or []) if (a or '').strip()]
    if not addrs:
        return False
    return all(_domain_of(a) in doms for a in addrs)


def no_reply_banner() -> str:
    """The red do-not-reply block, with a button into the Omni bug form."""
    # Straight to the no-login report form — NOT /report-bug, which bounces a
    # phone user to a login screen (CFO 2026-08-07: "make it easy, no login
    # required"). Falls back to the in-app form if signing is unavailable.
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    try:
        from core.bug_quick import quick_bug_url
        target = quick_bug_url()
    except Exception:                                    # noqa: BLE001
        target = f'{base}/report-bug'
    # Centred on the same 620px column as the house card, so it reads as part of
    # the email rather than a stray bar floating above it.
    return (
        f'<table {_NO_REPLY_MARK}="1" role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="border-collapse:collapse;background:#F3F4F6">'
        f'<tr><td align="center" style="padding:20px 12px 0">'
        f'<table role="presentation" width="620" cellpadding="0" cellspacing="0" '
        f'style="width:620px;max-width:100%;border-collapse:collapse">'
        f'<tr><td style="background:#FDECEC;border-left:4px solid #C62828;padding:12px 16px;'
        f'font-family:\'Segoe UI\',Arial,sans-serif;font-size:13px;line-height:1.55;color:#7F1D1D">'
        f'<b style="color:#C62828">Please do not reply to this email.</b> '
        f'A reply here is not tracked and nobody is assigned to it. '
        f'<a href="{target}" '
        f'style="display:inline-block;margin-top:8px;background:#C62828;color:#fff;'
        f'text-decoration:none;font-weight:700;font-size:12px;padding:8px 14px;border-radius:5px">'
        f'CLICK HERE TO REPORT IT &mdash; NO SIGN-IN NEEDED &rarr;</a>'
        f'</td></tr></table></td></tr></table>'
    )


def _with_no_reply(html: str) -> str:
    """Put the banner at the top of the HTML body. Idempotent."""
    if not html or _NO_REPLY_MARK in html:
        return html
    banner = no_reply_banner()
    lower = html.lower()
    i = lower.find('<body')
    if i != -1:
        j = html.find('>', i)
        if j != -1:
            return html[:j + 1] + banner + html[j + 1:]
    return banner + html


def send_with_cfo_cc(
    subject: str,
    body:    str,
    to:      list[str],
    *,
    from_email: Optional[str] = None,
    cc:         Optional[list[str]] = None,
    bcc:        Optional[list[str]] = None,
    html_body:  Optional[str] = None,
    reply_to:   Optional[list[str]] = None,
    no_reply:   bool = True,
) -> int:
    """Send an email and ALWAYS CC the CFO inbox.

    Usage:
        from core.notifications import send_with_cfo_cc
        send_with_cfo_cc(
            subject='Foo',
            body='Hi...',
            to=['vendor@example.com'],
            from_email='pganesharajah@alphadirect.co.bw',
        )

    Returns the integer Django send() returns (1 on success, 0 on
    silent failure). Idempotent on duplicate CC addresses.
    """
    cfo_addr = getattr(settings, 'MANDATORY_CFO_CC', _DEFAULT_CFO_CC)
    never_cc = set(getattr(settings, 'NEVER_CC_EMAILS', _NEVER_CC))

    def _norm(addr: str) -> str:
        return (addr or '').strip().lower()

    # Strip blocklisted addresses from TO and CC. CFO directive 2026-05-25:
    # Arun and Arjun are not decision-makers; never auto-include them.
    to_clean = [a for a in (to or []) if _norm(a) not in {_norm(x) for x in never_cc}]
    cc_list  = [a for a in (cc or []) if _norm(a) not in {_norm(x) for x in never_cc}]

    if cfo_addr and cfo_addr not in cc_list and cfo_addr not in to_clean:
        cc_list.append(cfo_addr)

    from_default = getattr(
        settings, 'OMNI_FROM_EMAIL',
        getattr(settings, 'DEFAULT_FROM_EMAIL', _DEFAULT_OMNI_FROM),
    )

    # CFO directive 2026-06-03 — HTML is the default. If the caller
    # passed html_body=None we auto-wrap `body` in the Alpha Direct
    # house envelope. If the caller wants plain-text only they pass
    # html_body='' (empty string) to opt out.
    if html_body is None:
        html_body = wrap_plain_as_html(body)

    if no_reply and all_internal(to_clean + cc_list + (bcc or [])):
        html_body = _with_no_reply(html_body)

    if html_body:
        msg = EmailMultiAlternatives(
            subject    = subject,
            body       = body or '(see HTML version)',
            from_email = from_email or from_default,
            to         = to_clean,
            cc         = cc_list,
            bcc        = bcc or [],
            reply_to   = reply_to or [],
        )
        msg.attach_alternative(html_body, 'text/html')
    else:
        msg = EmailMessage(
            subject    = subject,
            body       = body,
            from_email = from_email or from_default,
            to         = to_clean,
            cc         = cc_list,
            bcc        = bcc or [],
            reply_to   = reply_to or [],
        )
    return msg.send()


def send_html_with_cfo_cc(
    subject: str,
    html:    str,
    to:      list[str],
    *,
    text_fallback: str = '',
    from_email:    Optional[str] = None,
    cc:            Optional[list[str]] = None,
    bcc:           Optional[list[str]] = None,
    reply_to:      Optional[list[str]] = None,
    attachments:   Optional[list[tuple]] = None,
    cc_cfo:        bool = True,
    no_reply:      bool = True,
    allow_named_exec: bool = False,
) -> int:
    """Preferred entry point for new code (CFO directive 2026-06-03 — HTML
    default for every outbound email).

    `html` is the full HTML body (the caller may inline its own <style>
    block; wrap_plain_as_html() is available for the house template).
    `text_fallback` is the plain-text alternative — defaults to a
    one-liner pointing at the HTML version.

    `attachments` is an optional list of (filename, content_bytes, mime)
    triples passed through to EmailMultiAlternatives.attach().

    CCs MANDATORY_CFO_CC (excoboard@) per house rule, UNLESS cc_cfo=False
    (CFO directive 2026-06-17 — some operational alerts go to Finance only,
    not the CFO/EXCO inbox).
    """
    cfo_addr = getattr(settings, 'MANDATORY_CFO_CC', _DEFAULT_CFO_CC)
    never_cc = set(getattr(settings, 'NEVER_CC_EMAILS', _NEVER_CC))
    norm     = lambda a: (a or '').strip().lower()

    # The 2026-05-25 directive reads "must NOT be CC'd on any automated outbound
    # mail UNLESS the CFO names them explicitly in the call" — see the comment on
    # _NEVER_CC. The second half was never implemented: a named recipient was
    # stripped anyway, silently, with nothing in blocked_addrs to show it. On
    # 2026-08-10 the CFO asked for aiyer@ on an IT ticket, the send returned
    # success, and Arun was simply not on the message — while the email itself
    # said he was copied.
    #
    # So the blocklist stays the DEFAULT, and reaching them takes a deliberate
    # flag: a careless caller still cannot leak to them, and the CFO naming them
    # now works. The guarded backend already behaves this way and its own test
    # says why — NEVER_CC_EMAILS means "do not AUTO-CC as a decision-maker", not
    # "never deliver" (core/tests/test_mail_guard.py).
    if allow_named_exec:
        to_clean = list(to or [])
        cc_list  = list(cc or [])
    else:
        to_clean = [a for a in (to or []) if norm(a) not in {norm(x) for x in never_cc}]
        cc_list  = [a for a in (cc or []) if norm(a) not in {norm(x) for x in never_cc}]
    if cc_cfo and cfo_addr and cfo_addr not in cc_list and cfo_addr not in to_clean:
        cc_list.append(cfo_addr)

    from_default = getattr(
        settings, 'OMNI_FROM_EMAIL',
        getattr(settings, 'DEFAULT_FROM_EMAIL', _DEFAULT_OMNI_FROM),
    )

    msg = EmailMultiAlternatives(
        subject    = subject,
        body       = text_fallback or '(See the HTML version of this email — your client may have hidden it.)',
        from_email = from_email or from_default,
        to         = to_clean,
        cc         = cc_list,
        bcc        = bcc or [],
        reply_to   = reply_to or [],
    )
    _banner = no_reply and all_internal(to_clean + cc_list + (bcc or []))
    msg.attach_alternative(_with_no_reply(html) if _banner else html, 'text/html')
    for fn, content, mime in (attachments or []):
        msg.attach(fn, content, mime)
    return msg.send()


def _approver_emails(*, exclude_user_ids: Iterable[int] = ()) -> list[str]:
    """All active users with an approver title (CFO / FM / FC), with email set."""
    from core.models import UserProfile
    exclude = {uid for uid in exclude_user_ids if uid is not None}
    qs = (
        UserProfile.objects
        .filter(is_active=True, title__in=UserProfile.APPROVAL_TITLES)
        .select_related('user')
        .exclude(user_id__in=exclude)
    )
    return [p.user.email for p in qs if p.user.email and p.user.is_active]


def _admin_emails(*, exclude_user_ids: Iterable[int] = ()) -> list[str]:
    """All system administrators (is_administrator=True or CFO)."""
    from core.models import UserProfile
    exclude = {uid for uid in exclude_user_ids if uid is not None}
    qs = UserProfile.objects.filter(is_active=True).select_related('user').exclude(user_id__in=exclude)
    return [
        p.user.email for p in qs
        if (p.is_administrator or p.title == UserProfile.Title.CFO)
        and p.user.email and p.user.is_active
    ]


def _send(subject: str, body: str, recipients: list[str]):
    if not _enabled():
        return
    if not recipients:
        return
    try:
        # CFO 2026-07-14: this used send_mail() directly — plain text only.
        # Exchange's disclaimer transport rule tries to merge its HTML
        # signature (logo image + footer) into the message and, on a
        # text/plain body, dumps it as raw "[image] <url>" artifacts instead
        # of rendering — that's what showed up on the leave-approval email.
        # send_with_cfo_cc wraps the body in the house HTML envelope
        # (wrap_plain_as_html) so the merge has an HTML part to land in, and
        # CCs the CFO inbox per the standing rule.
        send_with_cfo_cc(subject, body, recipients)
    except Exception as exc:  # noqa: BLE001
        log.warning('Notification send failed: %s', exc)


def _link(path: str) -> str:
    # PUBLIC_BASE_URL is the real setting (defaults to the live omni URL).
    # This used to read APP_BASE_URL — which does not exist in settings — so
    # every notification email carried a dead http://localhost:3001 link
    # (CFO caught it on a leave-approval email, 2026-07-14).
    base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    return f'{base}{path}'


# ---------------------------------------------------------------------------
# Journal entries
# ---------------------------------------------------------------------------

def notify_je_pending_approval(journal_entry):
    """JE submitted for approval (DRAFT -> PENDING_APPROVAL).

    CFO 2026-06-29: no longer emails the whole approver pool. Instead it creates
    an in-app OmniTask for the Finance Manager(s) — which surfaces in /tasks and
    pops up via ARIA / the chat widget. Falls back to the wider approver pool only
    if no Finance Manager is available, so a JE is never left with nobody tasked.
    """
    from core.models import UserProfile, OmniTask

    creator_id   = getattr(journal_entry.created_by, 'id', None)
    submitter    = getattr(journal_entry, 'submitted_by', None)
    submitter_id = getattr(submitter, 'id', None)
    exclude = [i for i in (creator_id, submitter_id) if i is not None]

    def _holders(titles):
        return [
            p.user for p in (
                UserProfile.objects
                .filter(is_active=True, title__in=titles)
                .select_related('user')
                .exclude(user_id__in=exclude)
            )
            if p.user.email is not None and p.user.is_active
        ]

    assignees = _holders([UserProfile.Title.FINANCE_MANAGER])
    if not assignees:                                   # no FM free → wider pool
        assignees = _holders(list(UserProfile.APPROVAL_TITLES))
    assigner = submitter or journal_entry.created_by
    if not assignees or assigner is None:
        return

    rp = ' [RELATED PARTY]' if getattr(journal_entry, 'is_related_party', False) else ''
    title = f'Approve JE {journal_entry.entry_number}{rp}'[:200]
    body = (
        f'{journal_entry.entry_number} ({journal_entry.entry_date}) needs your approval.\n'
        f'Description: {journal_entry.description}\n'
        f'Submitted by: {getattr(submitter, "username", "—")}\n'
        f'Open: {_link("/journal-entries/" + str(journal_entry.id))}\n'
        'You cannot approve a JE you created — segregation of duties.'
    )
    for user in assignees:
        OmniTask.objects.create(
            assigner=assigner, assignee=user, title=title, body=body,
            priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
        )


# ---------------------------------------------------------------------------
# Leave (Kago Tshutlhedi feature request 2026-07-13)
# ---------------------------------------------------------------------------

def notify_leave_pending_approval(leave_request):
    """A leave request was submitted → email the requester's chosen manager a
    RICH approval email (CFO 2026-07-14): balances, sick-after-this-leave,
    pending leave, dashboard tasks at risk, and one-click Approve / Decline
    buttons that work from a phone. Best-effort: no manager on file, no email,
    or notifications disabled → silently no-op. Never raises, so a flaky mailer
    can never block the leave submission itself.

    CFO directive 2026-07-15: the manager alone is sufficient — this no longer
    cc's EXCO (cc_cfo=False). The employee's chosen manager is the recipient
    (hris.leave_email.build_leave_email resolves requested_approver first,
    falling back to HRISProfile.manager for legacy requests).
    """
    if not _enabled():
        return
    try:
        from hris.leave_email import build_leave_email
        mail = build_leave_email(leave_request)
        if not mail:
            return
        # Plain-text fallback for text-only clients.
        emp = leave_request.profile.employee.full_name
        # An auto-raised Time Doctor deduction is created by the 4pm enforce job,
        # NOT applied for by the employee. Saying "has applied" made approvers
        # believe staff had requested it, and staff then saw an approved row with
        # a manager's name on leave they never asked for (bug b7e41c7e).
        from hris.leave_email import is_auto_raised
        if is_auto_raised(leave_request):
            text = (f'Omni has raised an unpaid Time Doctor deduction for {emp} — '
                    f'{leave_request.day_breakdown()}. {emp} did not apply for this. '
                    f'Confirm or decline: {_link("/hris/leave")}')
        else:
            text = (f'{emp} has applied for leave — {leave_request.day_breakdown()}. '
                    f'Approve or decline: {_link("/hris/leave")}')
        send_html_with_cfo_cc(
            mail['subject'], mail['html'], mail['to'],
            text_fallback=text, cc=mail.get('cc'), cc_cfo=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning('Leave approval email failed: %s', exc)


# ---------------------------------------------------------------------------
# Payment approval quorum (Fable audit follow-up, CFO 2026-07-07)
# ---------------------------------------------------------------------------

def notify_payment_submitted(payment, submitter=None):
    """A payment entered the approval queue — raise a HIGH OmniTask for every
    eligible signer (FM/FC + CFO), excluding the creator/submitter (SoD).
    Mirrors the JE-approval OmniTask flow ([[project_je_task_not_email]])."""
    from core.models import OmniTask, UserProfile

    exclude = {i for i in (
        getattr(payment, 'created_by_id', None),
        getattr(payment, 'submitted_for_approval_by_id', None),
    ) if i}
    profiles = (UserProfile.objects
                .filter(is_active=True,
                        title__in=[UserProfile.Title.FINANCE_MANAGER,
                                   UserProfile.Title.FINANCIAL_CONTROLLER,
                                   UserProfile.Title.CFO])
                .select_related('user')
                .exclude(user_id__in=exclude))
    assignees = [p.user for p in profiles if p.user.is_active]
    assigner = submitter or payment.created_by
    if not assignees or assigner is None:
        return

    payee = (payment.payee_name if getattr(payment, 'is_once_off', False)
             else getattr(payment.contact, 'name', '')) or '—'
    title = f'Approve payment {payment.payment_number}'[:200]
    body = (
        f'{payment.payment_number} needs your signature — '
        f'{payment.currency_code_id} {payment.amount:,.2f} to {payee}'
        f'{" (ONE-OFF payee)" if getattr(payment, "is_once_off", False) else ""}.\n'
        f'Quorum: 1 Finance Manager/Controller + 1 CFO/CEO before it posts.\n'
        f'Submitted by: {getattr(assigner, "username", "—")}\n'
        f'Open: {_link("/payments/" + str(payment.id))}\n'
        'You cannot approve a payment you created/submitted — segregation of duties.'
    )
    for user in assignees:
        OmniTask.objects.create(
            assigner=assigner, assignee=user, title=title, body=body,
            priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
        )


def notify_payment_batch_submitted(*, count, company_id=None, submitter=None):
    """One summary OmniTask per approver for a bulk payment upload — instead of
    one task PER payment (a 200-row upload would otherwise bury every approver's
    inbox). Fable audit 2026-07-08."""
    from core.models import OmniTask, UserProfile

    exclude = {getattr(submitter, 'id', None)} - {None}
    profiles = (UserProfile.objects
                .filter(is_active=True,
                        title__in=[UserProfile.Title.FINANCE_MANAGER,
                                   UserProfile.Title.FINANCIAL_CONTROLLER,
                                   UserProfile.Title.CFO])
                .select_related('user')
                .exclude(user_id__in=exclude))
    assignees = [p.user for p in profiles if p.user.is_active]
    if not assignees or submitter is None:
        return
    title = f'Approve {count} uploaded payment(s)'[:200]
    body = (
        f'{count} payment(s) were uploaded in one batch and need approval.\n'
        f'Submitted by: {getattr(submitter, "username", "—")}\n'
        f'Open the approvals queue: {_link("/payments/approvals")}'
    )
    for user in assignees:
        OmniTask.objects.create(
            assigner=submitter, assignee=user, title=title, body=body,
            priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
        )


def close_payment_approval_tasks(payment, outcome: str):
    """Quorum met / rejected / withdrawn — close the open signature tasks so
    approvers' inboxes don't fill with dead work."""
    from django.utils import timezone as _tz
    from core.models import OmniTask
    (OmniTask.objects
     .filter(title=f'Approve payment {payment.payment_number}'[:200],
             status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
     .update(status=(OmniTask.Status.DONE if outcome == 'approved'
                     else OmniTask.Status.CANCELLED),
             completed_at=_tz.now()))
    # A bulk upload raises ONE summary task per approver ("Approve N uploaded
    # payment(s)", see notify_payment_batch_submitted) that points at the whole
    # approvals queue, not a single payment — so the per-payment match above can
    # never close it, and it used to nag forever after the batch was cleared.
    # Sweep it closed once the queue is empty (best-effort; runs on every
    # payment approve/reject via this helper).
    close_payment_batch_tasks()


def close_payment_batch_tasks():
    """Close the bulk-upload summary tasks ("Approve N uploaded payment(s)")
    once the payment-approval queue has been fully cleared.

    notify_payment_batch_submitted() fires ONE summary task per approver for an
    entire bulk upload (instead of one task per row). Those tasks reference the
    approvals *queue* rather than a single payment, so they cannot be matched to
    a per-payment decision the way close_payment_approval_tasks does. Instead we
    mirror the queue-summary pattern already used by the helpdesk reminder
    (helpdesk_pending_reminder._close_open_tasks): when nothing is left PENDING
    approval, the "clear the queue" nudge is done, so mark it DONE.

    This never fires prematurely: it only closes when NO payment is still
    pending approval. While any payment remains pending the nudge is still valid
    and the task stays open. Best-effort — any failure is swallowed so it can
    never block a payment decision.
    """
    try:
        from django.utils import timezone as _tz
        from core.models import OmniTask
        from payments.models import Payment
        # Queue still has work → the batch nudge is still valid; leave it open.
        if Payment.objects.filter(
                approval_status=Payment.ApprovalStatus.PENDING).exists():
            return
        (OmniTask.objects
         .filter(title__endswith='uploaded payment(s)',
                 status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
         .update(status=OmniTask.Status.DONE, completed_at=_tz.now()))
    except Exception as exc:  # noqa: BLE001
        log.warning('Payment batch task close failed: %s', exc)


def close_je_approval_tasks(journal_entry, outcome: str):
    """A JE has been approved / rejected / returned — close its open 'Approve JE …'
    tasks so approvers stop being nagged about an entry that is already decided.
    Mirrors close_payment_approval_tasks. Matches both title variants (the
    ' [RELATED PARTY]' suffix that notify_je_pending_approval may add)."""
    from django.utils import timezone as _tz
    from core.models import OmniTask
    num = journal_entry.entry_number
    titles = [f'Approve JE {num}'[:200], f'Approve JE {num} [RELATED PARTY]'[:200]]
    (OmniTask.objects
     .filter(title__in=titles,
             status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
     .update(status=(OmniTask.Status.DONE if outcome == 'approved'
                     else OmniTask.Status.CANCELLED),
             completed_at=_tz.now()))


# ---------------------------------------------------------------------------
# Petty cash — route to a task, NOT an inbox (CFO 2026-07-16)
# ---------------------------------------------------------------------------
# The CFO's directive: when a petty-cash voucher is submitted for approval it
# must appear as a TASK for the two petty-cash handlers (Keetile Mokhendo and
# Tlamelo Chimidza), and the CFO wants passive visibility of it. Approval
# authority itself stays with any Finance Manager / Financial Controller / CFO
# (petty_cash.services._can_approve) — the handlers prepare/verify, an FM/FC
# signs off. Named by email+username (belt-and-suspenders) so a rename of the
# handlers is one edit here, mirroring services._PETTY_CASH_FM_REVIEWER_IDS.
_PETTY_CASH_HANDLER_IDS = {
    'kmokhendo@alphadirect.co.bw', 'keetile.mokhendo',   # Keetile Mokhendo (he)
    'tchimidza@alphadirect.co.bw', 'tlamelo.chimidza',   # Tlamelo Chimidza
    'pkago@alphadirect.co.bw', 'pkago',                  # Pako Kago
    'ktshutlhedi@alphadirect.co.bw', 'ktshutlhedi',      # Kago Tshutlhedi
}
# CFO 2026-08-07: the CFO is OFF the per-voucher fan-out. He was getting a NORMAL
# "FYI" task for every single voucher, which buried his dashboard — 40-odd cards
# deep on the day he called it. The only petty-cash item he wants to see is the
# REIMBURSEMENT, which is the step he actually signs; that now surfaces in My
# Approvals instead (core/approvals_views.py). Deliberately empty rather than
# deleted, so the wiring stays obvious if a watcher is ever wanted again.
_PETTY_CASH_VISIBILITY_IDS: set[str] = set()


def _petty_cash_task_title(voucher) -> str:
    return f'Petty cash {voucher.voucher_number} — review'[:200]


def _resolve_omni_users(ident_set):
    """Active users whose (lower) email or username is in ident_set."""
    from django.db.models import Q
    idents = {i.strip().lower() for i in ident_set if i}
    if not idents:
        return []
    return list(User.objects.filter(
        Q(email__in=idents) | Q(username__in=idents), is_active=True))


def notify_petty_cash_pending(voucher, actor=None):
    """A petty-cash voucher entered PENDING_APPROVAL — raise it as a TASK for
    the handlers (HIGH) and give the CFO passive visibility (NORMAL). Named
    recipients (not the FM/FC pool) per CFO 2026-07-16. Best-effort: a failure
    here must never block the submit."""
    try:
        from core.models import OmniTask

        actor_id = getattr(actor, 'id', None)
        # Per-entity override (CFO 2026-08-31): a ring-fenced tin (Unicoin)
        # routes its review task to that tin's named approvers, not the default
        # group handlers. Lazy import to avoid a circular import at module load.
        handler_ids = _PETTY_CASH_HANDLER_IDS
        try:
            from petty_cash.services import (
                _PETTY_CASH_APPROVERS_BY_COMPANY, _company_code)
            override = _PETTY_CASH_APPROVERS_BY_COMPANY.get(
                _company_code(getattr(voucher, 'location', None)))
            if override:
                handler_ids = override
        except Exception as exc:  # noqa: BLE001
            # Routing is best-effort (the outer guard also protects the submit),
            # but the failure must be VISIBLE, not silent — a broken override
            # would otherwise quietly notify the default group instead of the
            # ring-fenced tin's approvers. Approval ENFORCEMENT is unaffected:
            # _can_approve_voucher always applies, so the wrong people still
            # cannot sign — only the review task would misroute.
            log.warning('Petty-cash approver-override routing failed for %s: %s',
                        getattr(voucher, 'voucher_number', '?'), exc)
        handlers = [u for u in _resolve_omni_users(handler_ids)
                    if u.id != actor_id]
        watchers = [u for u in _resolve_omni_users(_PETTY_CASH_VISIBILITY_IDS)
                    if u.id != actor_id and u.id not in {h.id for h in handlers}]
        # Assigner = the submitter; fall back to the voucher creator, then to
        # the first handler (OmniTask.assigner is NOT NULL).
        assigner = actor or getattr(voucher, 'submitted_by', None) \
            or getattr(voucher, 'created_by', None)
        if assigner is None and handlers:
            assigner = handlers[0]
        if assigner is None or not (handlers or watchers):
            return

        title = _petty_cash_task_title(voucher)
        loc = getattr(getattr(voucher, 'location', None), 'name', '') or '—'
        body = (
            f'Petty cash voucher {voucher.voucher_number} needs review — '
            f'BWP {voucher.amount:,.2f} to {voucher.payee or "—"}.\n'
            f'Location: {loc}\n'
            f'For: {(voucher.description or "").strip()[:160] or "—"}\n'
            f'Submitted by: {getattr(assigner, "username", "—")}\n'
            f'Open: {_link("/petty-cash/" + str(voucher.id))}\n'
            'Sign-off is by any Finance Manager, Financial Controller or CFO.'
        )
        rows = ([(u, OmniTask.Priority.HIGH) for u in handlers]
                + [(u, OmniTask.Priority.NORMAL) for u in watchers])
        for user, prio in rows:
            OmniTask.objects.create(
                assigner=assigner, assignee=user, title=title, body=body,
                priority=prio, status=OmniTask.Status.PENDING,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning('Petty-cash pending task fan-out failed: %s', exc)


def close_petty_cash_tasks(voucher, outcome: str):
    """Voucher approved / rejected — close the open review tasks so the
    handlers' (and CFO's) task lists don't hold dead work. Mirrors
    close_payment_approval_tasks."""
    try:
        from django.utils import timezone as _tz
        from core.models import OmniTask
        (OmniTask.objects
         .filter(title=_petty_cash_task_title(voucher),
                 status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
         .update(status=(OmniTask.Status.DONE if outcome == 'approved'
                         else OmniTask.Status.CANCELLED),
                 completed_at=_tz.now()))
    except Exception as exc:  # noqa: BLE001
        log.warning('Petty-cash task close failed: %s', exc)


# ---------------------------------------------------------------------------
# Development Dialogue — employee submit routes a review task to their reviewer
# (CFO 2026-07-27). When a staff member signs off their own self-assessment it
# must land as a TASK for their reviewer, which by itself surfaces in the
# reviewer's omni Tasks, the Nexus Staff Portal "My tasks", AND their Morning
# Brief (all read core.OmniTask by assignee — see reference_nexus_staff_portal).
# The task auto-closes when the reviewer signs. Best-effort: a failure here must
# never block the employee's sign-off.
# ---------------------------------------------------------------------------
def _dialogue_task_title(row) -> str:
    # Period in the title keeps it human-readable AND unique per dialogue, so a
    # person's new-period review never collides with a still-open earlier one
    # (idempotency + close both key off this title).
    period = (row.period or '').strip()
    base = f"Review & sign {row.name}'s Development Dialogue"
    # Period disambiguates periods and stays human-readable; if a dialogue has no
    # period, fall back to the unique ref so two records never share a title.
    suffix = period or str(row.ref)
    return f"{base} — {suffix}"[:200]


def _dialogue_reviewer_user(row):
    """The active omni user who reviews this dialogue: the owner's HR line-
    manager first; else a name match against the free-text supervisor. Returns
    None (no task raised) if the reviewer can't be resolved or is inactive."""
    from hris.models import HRISProfile  # noqa: F401  (ensures app is loaded)
    from payroll.models import Employee

    owner = Employee.objects.filter(email__iexact=(row.email or '').strip()).first()
    prof = getattr(owner, 'hris_profile', None) if owner else None
    mgr = getattr(prof, 'manager', None) if prof else None
    mgr_email = (getattr(mgr, 'email', '') or '').strip().lower()
    if mgr_email:
        u = User.objects.filter(email__iexact=mgr_email, is_active=True).first()
        if u:
            # The line-manager link is deliberate HR data and cross-entity
            # reporting is legitimate in the group, so we ROUTE it (never drop) —
            # but log a cross-entity manager for audit (DeepSeek omni-review).
            oc = getattr(owner, 'company_id', None)
            mc = getattr(mgr, 'company_id', None)
            if oc is not None and mc is not None and oc != mc:
                log.info('Dialogue reviewer is a cross-entity line manager '
                         '(owner company %s vs manager company %s) — routing per '
                         'the HR reporting line.', oc, mc)
            return u
    # Fallback: match the supervisor free-text to an active user by shared name
    # tokens (>=2, so first+surname must agree — handles "Arun P Iyer" vs the
    # account's "Arun Iyer"). Never guesses on a single common first name.
    # Company-scoped (DeepSeek omni-review 2026-07-27): a same-named person could
    # exist in another group entity; a fuzzy name match must never route one
    # entity's confidential appraisal to a namesake in another. So when we know
    # the owner's company, only accept a namesake in that same company.
    # The fuzzy fallback ALWAYS requires a known owner company — never a
    # company-less name match (DeepSeek omni-review). If we can't establish the
    # owner's entity, we raise no task rather than risk a cross-entity namesake.
    sup_tokens = {t for t in (row.supervisor or '').strip().lower().split() if len(t) > 1}
    owner_company_id = getattr(owner, 'company_id', None)
    if len(sup_tokens) >= 2 and owner_company_id is not None:
        for u in User.objects.filter(is_active=True):
            full_tokens = {t for t in (u.get_full_name() or '').strip().lower().split() if len(t) > 1}
            if len(full_tokens & sup_tokens) < 2:
                continue
            emp = (Employee.objects.filter(user=u).first()
                   or Employee.objects.filter(email__iexact=(u.email or '').strip()).first())
            if getattr(emp, 'company_id', None) != owner_company_id:
                continue  # namesake in a different entity (or unknown) — skip
            log.info('Dialogue reviewer resolved by supervisor-name fallback: '
                     '%r -> user %s (%s).', row.supervisor, u.username, u.email)
            return u
    return None


def notify_dialogue_submitted(row, employee_user):
    """Employee signed off their Development Dialogue — raise a HIGH review task
    for their reviewer and email them a nudge. Idempotent: skips if an open
    review task for this dialogue already exists (re-sign / double-click safe)."""
    try:
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from core.models import OmniTask

        reviewer = _dialogue_reviewer_user(row)
        if reviewer is None or reviewer.id == getattr(employee_user, 'id', None):
            log.info('Dialogue submit: no distinct active reviewer for %s — no task raised.',
                     row.email or row.name)
            return

        title = _dialogue_task_title(row)
        already = OmniTask.objects.filter(
            assignee=reviewer, title=title,
            status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS],
        ).exists()
        if already:
            return

        assigner = employee_user or reviewer
        body = (
            f'{row.name} has submitted their Development Dialogue self-assessment '
            f'for {row.period or "the current period"} and it is ready for your review.\n'
            f'Open it, add your manager scores and sign it off:\n'
            f'{_link("/hris/team-dialogues")}'
        )
        OmniTask.objects.create(
            assigner=assigner, assignee=reviewer, title=title, body=body,
            priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
            due_at=(_tz.localdate() + _td(days=7)),
            source='dev_dialogue',
        )

        # Direct email nudge to the reviewer only — NO scores in the body, and no
        # CFO cc (appraisals are confidential to owner + reviewer + HR).
        if reviewer.email and _enabled():
            try:
                subject = f'Development Dialogue to review — {row.name}'
                text = (
                    f'{row.name} has submitted their Development Dialogue '
                    f'for {row.period or "the current period"}.\n\n'
                    f'Review and sign it here: {_link("/hris/team-dialogues")}'
                )
                msg = EmailMultiAlternatives(subject, text, to=[reviewer.email])
                msg.attach_alternative(wrap_plain_as_html(text), 'text/html')
                msg.send()
            except Exception as exc:  # noqa: BLE001
                log.warning('Dialogue reviewer email failed: %s', exc)
    except Exception as exc:  # noqa: BLE001
        log.warning('Dialogue submit task fan-out failed: %s', exc)


def close_dialogue_review_tasks(row, signer):
    """A manager/moderator signed the dialogue — close THIS signer's own open
    review task so it drops off their omni Tasks / Nexus / Morning Brief. `signer`
    is REQUIRED and the close is always scoped to it (assignee), so one
    manager/moderator signing never clears the task still waiting on the actual
    assigned reviewer, and a missing signer can never blanket-close everyone."""
    if signer is None:
        return
    try:
        from django.utils import timezone as _tz
        from core.models import OmniTask
        (OmniTask.objects
         .filter(title=_dialogue_task_title(row), assignee=signer,
                 status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
         .update(status=OmniTask.Status.DONE, completed_at=_tz.now()))
    except Exception as exc:  # noqa: BLE001
        log.warning('Dialogue task close failed: %s', exc)


# ---------------------------------------------------------------------------
# Company-card bills — Finance asks a cardholder to explain a charge, or a
# charge is missing its receipt (CFO 2026-09-05). Reuses the OmniTask inbox, so
# the nudge surfaces for free in /tasks, the Nexus "My tasks" tile and the daily
# Morning Brief, and a direct email. ONE bundled task per holder ("N bills to
# explain"), refreshed as items open and close, so a cardholder is never buried
# under one task per charge. The exec answers on their phone; when their open
# count hits zero the task auto-closes.
# ---------------------------------------------------------------------------

_CARD_TASK_SOURCE = 'card_explain'
_CARD_LINK = '/m/staff/card-spend'


def refresh_card_task(holder, by=None):
    """Keep the holder's single 'company-card bills to explain' task in step with
    their real open count. Zero open items closes it. No email here — the callers
    that should nudge (a Finance query / Nudge everyone) send the email."""
    try:
        from datetime import timedelta as _td
        from django.utils import timezone as _tz
        from core.models import OmniTask
        from company_cards import services as svc

        if holder is None:
            return
        n = svc.open_item_count(holder)
        existing = OmniTask.objects.filter(
            assignee=holder, source=_CARD_TASK_SOURCE,
            status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS],
        ).first()
        if n == 0:
            if existing:
                existing.status = OmniTask.Status.DONE
                existing.completed_at = _tz.now()
                existing.save(update_fields=['status', 'completed_at', 'updated_at'])
            return
        title = 'Company-card bills to explain'
        body = (f'You have {n} company-card charge(s) that still need a receipt or '
                f'a few words on what they were for.\n'
                f'Open them on your phone and clear them:\n{_link(_CARD_LINK)}')
        if existing:
            existing.title = title
            existing.body = body
            existing.save(update_fields=['title', 'body', 'updated_at'])
        else:
            OmniTask.objects.create(
                assigner=(by or holder), assignee=holder, title=title, body=body,
                priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
                due_at=(_tz.localdate() + _td(days=3)), source=_CARD_TASK_SOURCE)
    except Exception as exc:  # noqa: BLE001
        log.warning('Card task refresh failed: %s', exc)


def notify_card_query(spend, by=None):
    """Finance has a question about a specific charge. Email the cardholder the
    question and refresh their bills-to-explain task."""
    try:
        holder = spend.card.holder
        if holder is None:
            return
        q = (spend.finance_note or '').strip()
        if getattr(holder, 'email', '') and _enabled():
            try:
                subject = f'Company card — Finance has a question ({spend.card.label})'
                text = (
                    f'Finance has a question about a charge on {spend.card.label}:\n'
                    f'{spend.spent_on} · {spend.merchant or "—"} · BWP {spend.amount}\n'
                    + (f'\nQuestion: {q}\n' if q else '')
                    + f'\nOpen it, attach the receipt and say what it was for:\n'
                      f'{_link(_CARD_LINK)}'
                )
                msg = EmailMultiAlternatives(subject, text, to=[holder.email])
                msg.attach_alternative(wrap_plain_as_html(text), 'text/html')
                msg.send()
            except Exception as exc:  # noqa: BLE001
                log.warning('Card-query email failed: %s', exc)
        refresh_card_task(holder, by)
    except Exception as exc:  # noqa: BLE001
        log.warning('Card query notify failed: %s', exc)


def nudge_card_holder(holder, by=None) -> int:
    """Bundled 'you have N bills to explain' nudge — the button that replaces
    Finance's monthly chase email. Returns the open count (0 = nothing sent)."""
    try:
        from company_cards import services as svc
        n = svc.open_item_count(holder)
        if n <= 0:
            return 0
        if getattr(holder, 'email', '') and _enabled():
            try:
                subject = 'Company card — bills to explain'
                text = (f'You have {n} company-card charge(s) that still need a '
                        f'receipt or a few words on what they were for.\n'
                        f'Please clear them here:\n{_link(_CARD_LINK)}')
                msg = EmailMultiAlternatives(subject, text, to=[holder.email])
                msg.attach_alternative(wrap_plain_as_html(text), 'text/html')
                msg.send()
            except Exception as exc:  # noqa: BLE001
                log.warning('Card nudge email failed: %s', exc)
        refresh_card_task(holder, by)
        return n
    except Exception as exc:  # noqa: BLE001
        log.warning('Card nudge failed: %s', exc)
        return 0


# ---------------------------------------------------------------------------
# Import batches (asset / recovery / payroll — same shape)
# ---------------------------------------------------------------------------

def notify_import_partially_approved(batch, kind_label: str, deep_link_path: str):
    """
    Sent when a bulk-import batch reaches PARTIALLY_APPROVED, i.e. one
    approver has signed and a *different* approver is needed to commit.
    """
    creator_id = getattr(batch.created_by, 'id', None)
    first_id   = getattr(batch.first_approved_by, 'id', None)
    recipients = _approver_emails(exclude_user_ids=[creator_id, first_id])
    if not recipients:
        return

    subject = f'[Alpha Finance] {kind_label} import awaits second approval'
    body = (
        f'A {kind_label.lower()} import batch ({batch.file_name or batch.id}) '
        f'has the first approval and needs a second sign-off before it can be committed.\n\n'
        f'Uploaded by:    {getattr(batch.created_by, "username", "—")}\n'
        f'First approver: {getattr(batch.first_approved_by, "username", "—")}\n\n'
        f'Open: {_link(deep_link_path)}'
    )
    _send(subject, body, recipients)


# ---------------------------------------------------------------------------
# Payroll sign-off (CFO directive 2026-07-26 — option A)
# ---------------------------------------------------------------------------

def _payroll_side_task_title(company, period, side) -> str:
    who = 'HR' if side == 'hr' else 'Finance'
    return f'Payroll sign-off ({who}) — {company.name} {period.period_name}'[:200]


def _payroll_side_signers(side):
    from payroll.signoff_service import (
        payroll_finance_signers, payroll_hr_signers,
    )
    return payroll_hr_signers() if side == 'hr' else payroll_finance_signers()


def notify_payroll_side_due(period, company, side: str):
    """Put the sign-off on the HR (Unami/Dorothy) or Finance (Kago/Pako)
    signers' dashboards + daily reminder emails (CFO directive 2026-07-28:
    dual sign-off, CFO out of the loop). Idempotent — one open task per person
    per company-month-side."""
    try:
        from core.models import OmniTask
        from payroll.signoff_models import live_payroll_totals

        signers = _payroll_side_signers(side)
        if not signers:
            log.warning('Payroll %s chase: no signers resolved.', side)
            return 0
        who = 'HR' if side == 'hr' else 'Finance'
        title = _payroll_side_task_title(company, period, side)
        t = live_payroll_totals(period, company)
        body = (
            f'{company.name} payroll for {period.period_name} needs your {who} '
            f'sign-off. Payslips are NOT released until BOTH HR and Finance sign.\n'
            f'People: {t["headcount"]}\n'
            f'Gross: BWP {t["gross"]:,.2f}\n'
            f'Net pay: BWP {t["net"]:,.2f}\n'
            f'Check the figures, then sign: {_link("/payroll/sign-off")}'
        )
        made = 0
        for user in signers:
            if OmniTask.objects.filter(
                    assignee=user, title=title,
                    status__in=[OmniTask.Status.PENDING,
                                OmniTask.Status.IN_PROGRESS]).exists():
                continue
            OmniTask.objects.create(
                assigner=user, assignee=user, title=title, body=body,
                priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
            )
            made += 1
        _send(
            f'[Alpha Finance] Payroll {who} sign-off needed — '
            f'{company.name} {period.period_name}',
            body,
            [u.email for u in signers if u.email],
        )
        return made
    except Exception as exc:  # noqa: BLE001
        log.warning('Payroll %s chase failed: %s', side, exc)
        return 0


def close_payroll_side_tasks(company, period, side: str):
    """That side signed (or the month closed) — clear its task."""
    try:
        from django.utils import timezone as _tz
        from core.models import OmniTask
        (OmniTask.objects
         .filter(title=_payroll_side_task_title(company, period, side),
                 status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
         .update(status=OmniTask.Status.DONE, completed_at=_tz.now()))
    except Exception as exc:  # noqa: BLE001
        log.warning('Payroll %s task close failed: %s', side, exc)


def _payroll_signer_emails():
    from payroll.signoff_service import (
        payroll_finance_signers, payroll_hr_signers,
    )
    return list({u.email for u in (payroll_hr_signers() + payroll_finance_signers())
                 if u.email})


def notify_payroll_signoff_complete(row):
    """Both sides signed — tell the HR + Finance signers it is released."""
    try:
        _send(
            f'[Alpha Finance] Payroll signed off — {row.company.name} '
            f'{row.period.period_name}',
            (f'{row.company.name} payroll for {row.period.period_name} '
             f'({row.headcount} people, BWP {row.net_total:,.2f} net) is fully '
             f'signed off by HR and Finance. Payslips are now released to staff.\n\n'
             f'HR: {(row.hr_signed_by.get_full_name() or row.hr_signed_by.email) if row.hr_signed_by else "-"}\n'
             f'Finance: {(row.fin_signed_by.get_full_name() or row.fin_signed_by.email) if row.fin_signed_by else "-"}'),
            _payroll_signer_emails(),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning('Payroll complete notify failed: %s', exc)


def notify_payroll_signoff_rejected(row):
    """Sent back for a fix — email the signers now (the daily chase re-raises
    both sides once it is fixed)."""
    try:
        _send(
            f'[Alpha Finance] Payroll sent back — {row.company.name} '
            f'{row.period.period_name}',
            (f'{row.company.name} {row.period.period_name} payroll was sent back: '
             f'{row.rejection_reason}\n\n'
             f'Fix it, then HR and Finance both sign again: '
             f'{_link("/payroll/sign-off")}'),
            _payroll_signer_emails(),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning('Payroll rejected notify failed: %s', exc)


# ---------------------------------------------------------------------------
# Asset sign-offs
# ---------------------------------------------------------------------------

def notify_signoff_overdue(signoff):
    """Sent (typically by a daily cron) when a sign-off goes overdue."""
    recipients = _admin_emails()
    if not recipients:
        return
    subject = f'[Alpha Finance] Asset sign-off OVERDUE — {signoff.period_label}'
    body = (
        f'{signoff.get_kind_display()} for {signoff.period_label} is overdue '
        f'(was due {signoff.due_date}).\n\n'
        f'Open: {_link("/assets/signoffs")}'
    )
    _send(subject, body, recipients)


# ---------------------------------------------------------------------------
# Asset disposals
# ---------------------------------------------------------------------------

def notify_disposal_pending(disposal):
    """Sent when an asset disposal is requested and awaits approval."""
    requester_id = getattr(disposal.requested_by, 'id', None)
    recipients = _approver_emails(exclude_user_ids=[requester_id])
    if not recipients:
        return
    subject = f'[Alpha Finance] Asset disposal awaiting approval — {disposal.asset.tag_number}'
    body = (
        f'{disposal.asset.tag_number} ({disposal.asset.name}) — disposal requested '
        f'by {getattr(disposal.requested_by, "username", "—")}.\n\n'
        f'Type:        {disposal.get_disposal_type_display()}\n'
        f'Date:        {disposal.disposal_date}\n'
        f'Proceeds:    BWP {disposal.proceeds}\n'
        f'NBV:         BWP {disposal.nbv_at_disposal}\n'
        f'Gain/(Loss): BWP {disposal.gain_loss}\n\n'
        f'Open: {_link("/assets/" + str(disposal.asset_id))}\n\n'
        'Note: the requester cannot approve their own disposal.'
    )
    _send(subject, body, recipients)


# ---------------------------------------------------------------------------
# Leave decision → tell the EMPLOYEE (CFO 2026-08-07)
# ---------------------------------------------------------------------------

def notify_leave_decided(leave_request):
    """A leave request was approved / declined / cancelled → email the person
    who applied. Until this existed the only leave email went to the approver,
    so staff never learned the answer (CFO 2026-08-07: "if the manager approved
    a leave, the leave is not reporting back to employee").

    Best-effort and never raises — a mail failure must not roll back a decision.
    """
    if not _enabled():
        return
    try:
        from hris.leave_email import build_leave_decision_email
        mail = build_leave_decision_email(leave_request)
        if not mail:
            return
        emp = leave_request.profile.employee.full_name
        text = (f'{emp}: your leave ({leave_request.day_breakdown()}) was '
                f'{leave_request.get_status_display().lower()}. '
                f'Details: {_link("/hris/leave")}')
        send_html_with_cfo_cc(
            mail['subject'], mail['html'], mail['to'],
            text_fallback=text, cc=mail.get('cc'), cc_cfo=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning('Leave decision email failed: %s', exc)


# ---------------------------------------------------------------------------
# Long-overdue-task gate → ask the CEO / CFO to countersign (CFO 2026-08-07)
# ---------------------------------------------------------------------------

def notify_exec_signoff_required(signoff):
    """Someone with work more than 2 days overdue applied for leave / a loan /
    an incentive. Raise a HIGH OmniTask for each signer AND email them a
    one-click sign link, so it can be cleared from a phone."""
    from core.models import OmniTask
    from hris import exec_signoff_service as svc
    from hris.exec_signoff_actions import action_url

    signers = svc.signer_users(cfo_only=signoff.cfo_only)
    if not signers:
        log.warning('Exec sign-off %s raised but no active CEO/CFO login found.', signoff.pk)
        return

    what = signoff.get_module_display().lower()
    # Why it landed on their desk. Discretionary leave has nothing to do with
    # overdue work, so it must not be described that way (CFO 2026-09-10).
    from hris.exec_signoff_models import ExecSignoff as _ES
    because = ('applied for discretionary leave, which the CFO signs off'
               if signoff.kind == _ES.Kind.LEAVE_POLICY
               else f'applied for {what} while carrying overdue work')
    for signer in signers:
        try:
            OmniTask.objects.create(
                assigner=signoff.applicant, assignee=signer,
                title=f'Sign off: {signoff.applicant_name} — {what}',
                # Links the task back to the sign-off so ONE signature can close
                # every signer's copy (see exec_signoff_service._close_signer_tasks).
                source=svc.signoff_task_source(signoff),
                body=(f'{signoff.applicant_name} {because}.\n\n'
                      f'{signoff.reason}\n\nSign or decline: {action_url(signoff, signer)}'),
                priority=OmniTask.Priority.HIGH, status=OmniTask.Status.PENDING,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning('Exec sign-off task for %s failed: %s', signer, exc)

    if not _enabled():
        return
    try:
        from hris.exec_signoff_email import build_exec_signoff_email
        for signer in signers:
            mail = build_exec_signoff_email(signoff, signer)
            if not mail:
                continue
            send_html_with_cfo_cc(
                mail['subject'], mail['html'], mail['to'],
                text_fallback=(f'{signoff.applicant_name} {because}. '
                               f'Sign or decline: {action_url(signoff, signer)}'),
                cc=None, cc_cfo=False,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning('Exec sign-off email failed: %s', exc)


def notify_approved_leave_cancelled(leave_request):
    """The employee cancelled leave the manager had already approved.

    The manager planned around those days being off. They approved it, so they
    are told it is back on — otherwise the first they know is the person
    turning up (CFO queue item 3, 2026-08-08). Best-effort; never raises.
    """
    if not _enabled():
        return
    try:
        approver = getattr(leave_request, 'approver', None)
        to = (getattr(approver, 'email', '') or '').strip()
        if not to:
            return
        from django.conf import settings
        from django.utils.html import escape
        emp = leave_request.profile.employee.full_name
        lt = leave_request.leave_type.name if leave_request.leave_type_id else 'Leave'
        base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
        NAVY, ORANGE = '#0D1B2A', '#F4A623'
        inner = (
            f'<h2 style="margin:0 0 6px;font-size:20px;color:{NAVY};">'
            f'{escape(emp)} cancelled their leave</h2>'
            f'<p style="color:#6B7280;font-size:14px;margin:0 0 12px;">'
            f'You approved this one. They cancelled it before it started, so they '
            f'will be at work as normal.</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:14px;">'
            f'<tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;width:42%;">Leave type</td>'
            f'<td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">{escape(lt)}</td></tr>'
            f'<tr><td style="padding:7px 0;border-bottom:1px solid #EEF0F3;color:#6B7280;">Was booked for</td>'
            f'<td style="padding:7px 0;border-bottom:1px solid #EEF0F3;font-weight:600;">'
            f'{escape(leave_request.day_breakdown())}</td></tr></table>'
            f'<p style="margin:18px 0 0;"><a href="{base}/hris/leave" style="display:block;'
            f'text-align:center;background:{NAVY};color:#fff;text-decoration:none;padding:14px;'
            f'border-radius:10px;font-weight:700;">Open the leave screen</a></p>')
        html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1"></head>'
                f'<body style="margin:0;background:#F3F4F6;font-family:\'Segoe UI\',Arial,sans-serif;">'
                f'<div style="max-width:600px;margin:0 auto;padding:20px 12px;">'
                f'<div style="background:#fff;border-radius:14px;overflow:hidden;">'
                f'<div style="background:{NAVY};padding:20px 26px;">'
                f'<div style="color:{ORANGE};font-size:18px;font-weight:700;">'
                f'Alpha Direct · Leave Cancelled</div></div>'
                f'<div style="padding:22px 26px;">{inner}</div></div>'
                f'<p style="text-align:center;color:#9CA3AF;font-size:11px;margin-top:14px;">'
                f'Omni ERP — omni.alphadirect.co.bw</p></div></body></html>')
        send_html_with_cfo_cc(
            f'Leave cancelled: {emp} — {lt} ({leave_request.day_breakdown()})',
            html, [to],
            text_fallback=(f'{emp} cancelled the {lt} you approved '
                           f'({leave_request.day_breakdown()}). They will be at work.'),
            cc=None, cc_cfo=False)
    except Exception as exc:  # noqa: BLE001
        log.warning('Leave-cancelled notice to the approver failed: %s', exc)


# ---------------------------------------------------------------------------
# Offboarding — tell IT and Payroll a leaver has been terminated
# ---------------------------------------------------------------------------
# Dorothy Ikgopoleng, 2026-09-11, answering the Terminate Employee follow-up
# ("should the system automatically let IT and Payroll know?"):
#   "Perhaps they can receive a prompt so that they are able to ensure all
#    offboarding checks are done on their end."
# So a termination now raises ONE task per side with that side's own checklist,
# plus one email. HR is NOT notified — HR is the one doing the terminating.
# Best-effort throughout: a flaky mailer or a missing role must never block an
# exit being recorded.

# How long each side has to finish its checks, and the house 16:00 cut-off.
_OFFBOARDING_DUE_DAYS = 7
_OFFBOARDING_DUE_TIME = time(16, 0)

_IT_CHECKLIST = [
    'Disable the omni / Microsoft 365 sign-in on the last working day.',
    'Forward or delegate the mailbox, then close it per the retention rule.',
    'Remove them from Graphite, FNB, Time Doctor and any other system login.',
    'Confirm Asset Control shows every item returned — a leaver still holding '
    'kit cannot be terminated, so this should already be clear.',
    'Wipe and re-image the returned kit before it is re-issued.',
]

_PAYROLL_CHECKLIST = [
    'Stop the salary from the next pay run — check they are not still in the draft run.',
    'Work out the final pay: days worked, notice, and any leave still owed.',
    'Settle or recover any staff loan, salary advance or company-card balance.',
    'Stop the medical aid, pension and any other deduction from the leaving date.',
    'Issue the final payslip and the PAYE / tax certificate.',
]


def _it_offboarding_users():
    """The same IT owners the Help Desk queue routes to.

    This used to look for whoever held the IT_MANAGER role, with the IT mailbox
    as a fallback. Wrong on both counts (CFO 2026-09-11: "Sechele is the IT guy
    he is not a manager") — nobody holds that role, so every prompt was landing
    on the fallback, and it reached one person instead of the pair Omni already
    treats as IT everywhere else.
    """
    from core.it_queue import it_owner_users
    return it_owner_users()


def _payroll_offboarding_users():
    from payroll.signoff_service import payroll_finance_signers
    return list(payroll_finance_signers())


def _offboarding_task_title(employee, side: str) -> str:
    # The employee number is part of the title on purpose: it is what makes the
    # duplicate-suppression below safe when two leavers share a name.
    number = (getattr(employee, 'employee_number', '') or str(employee.pk))
    return f'Offboarding ({side}): {employee.full_name} ({number})'


def notify_offboarding_started(employee, *, termination_date, reason: str,
                               actor=None) -> int:
    """A leaver was terminated — prompt IT and Payroll with their checklists.

    Returns the number of tasks created. Never raises, and each side is handled
    independently: a failure prompting IT must not cost Payroll its prompt.
    """
    from core.models import OmniTask

    reason_label = (reason or '').replace('_', ' ').strip() or 'not stated'
    made = 0
    for side, resolve, checklist in (
        ('IT', _it_offboarding_users, _IT_CHECKLIST),
        ('Payroll', _payroll_offboarding_users, _PAYROLL_CHECKLIST),
    ):
        try:
            users = resolve()
            if not users:
                log.warning('Offboarding notice: no %s recipients resolved.', side)
                continue
            title = _offboarding_task_title(employee, side)
            # Count the window from TODAY when the exit is being recorded late.
            # A past termination date is allowed (HR is clearing a backlog of
            # leavers), and anchoring on it alone would raise a task that is
            # born overdue and nags on the first login for work nobody was
            # ever asked to do.
            due_on = (max(termination_date, timezone.localdate())
                      + timedelta(days=_OFFBOARDING_DUE_DAYS))
            steps = '\n'.join(f'{i}. {step}' for i, step in enumerate(checklist, 1))
            body = (
                f'{employee.full_name} has left Alpha Direct.\n'
                f'Last working day: {termination_date}\n'
                f'Reason: {reason_label}\n\n'
                f'Your {side} offboarding checks:\n{steps}\n\n'
                f'Due {due_on} — mark this task done once every step above is finished.\n'
                f'{_link("/payroll/employees")}'
            )
            # Own savepoint: a database error raised while writing these tasks
            # must not poison the caller's transaction — swallowing it outside a
            # savepoint would leave that transaction aborted and silently roll
            # the termination itself back.
            fresh = []
            with transaction.atomic():
                for user in users:
                    if OmniTask.objects.filter(
                            assignee=user, title=title,
                            status__in=[OmniTask.Status.PENDING,
                                        OmniTask.Status.IN_PROGRESS,
                                        OmniTask.Status.PARTIAL,
                                        OmniTask.Status.BLOCKED]).exists():
                        continue
                    OmniTask.objects.create(
                        assigner=actor if getattr(actor, 'is_authenticated', False) else user,
                        assignee=user, title=title, body=body,
                        priority=OmniTask.Priority.HIGH,
                        status=OmniTask.Status.PENDING,
                        source='offboarding',
                        # CHASED, by the CFO's decision 2026-09-11 ("remind them
                        # until it is done"). A due date is what puts a task into
                        # sweep_due_and_overdue + the daily reminder email — both
                        # skip undated tasks. Marking it Done by ANY path clears
                        # the nag on the next sweep, so this cannot become one of
                        # the reminders nothing ever closes.
                        due_at=due_on,
                        due_time=_OFFBOARDING_DUE_TIME,
                    )
                    fresh.append(user)
            made += len(fresh)
            # Mail only the people actually given a NEW task — a re-fire must not
            # re-send the same checklist to someone already holding it.
            if fresh:
                _send(
                    f'[Alpha Finance] {side} offboarding checks — {employee.full_name}',
                    body,
                    [u.email for u in fresh if u.email],
                )
        except Exception:  # noqa: BLE001
            log.exception('Offboarding %s notice failed for employee %s',
                          side, getattr(employee, 'pk', '?'))
    return made


def notify_staff_loan_awaiting_cfo(app):
    """A staff loan has been submitted and is waiting for the CFO.

    Until 2026-09-12 NOTHING was sent: the request simply appeared in Omni and
    sat there until he happened to look. CFO 2026-09-12: "the exco and managers
    are not alaways not in office they operate off site and then we are making
    their life difficult by asking them to approve things through omni web."
    So the email carries a signed, single-use, no-login Approve button.

    Declining needs a typed reason, so Decline opens the loan in Omni rather
    than being one-tapped away. Best-effort: never raises, never blocks a
    submit.
    """
    if not _enabled():
        return
    try:
        from html import escape as _esc

        from core.magic_action import make_action_link
        from staff_loans.services import cfo_approvers

        # CFO-titled people only - NOT `_is_final_approver`, which is
        # "CFO **or** is_superuser". That would have put a staff member's name,
        # loan amount and stated reason, with a no-login Approve button, in
        # every super-admin mailbox including IT and service accounts
        # (/fabe 2026-09-13; CFO decision the same day: CFO only).
        approvers = [u for u in cfo_approvers() if (u.email or '').strip()]
        # The applicant can never approve their own loan (segregation of duties,
        # enforced again inside cfo_decide) - do not even email them.
        applicant_user_id = getattr(getattr(app, 'employee', None), 'user_id', None)
        approvers = [u for u in approvers if u.id != applicant_user_id]
        if not approvers:
            log.warning('Staff loan %s submitted but no active final approver found.', app.pk)
            return

        who = getattr(getattr(app, 'employee', None), 'full_name', '') or 'An employee'
        amount = f'BWP {app.amount_requested:,.2f}' if app.amount_requested is not None else ''
        term = f'{app.term_months_requested} months' if app.term_months_requested else ''
        detail = ' \u00b7 '.join(b for b in (app.get_loan_type_display(), amount, term) if b)
        subject = f'Staff loan to approve - {who} ({amount})'.strip()

        # The full pack - amount, term, the instalment it works out at and that
        # instalment against the salary on file - so the signer is not asked to
        # approve money off four lines (CFO 2026-09-20).
        from core.approval_pack import app_link_html, build_pack, pack_html
        pack = pack_html(build_pack('staff_loans', app.pk))
        if not pack:
            # The pack could not be built - fall back to the detail lines this
            # email carried before, never to nothing. A best-effort decorator
            # must degrade to what was there, not below it.
            pack = (f'<ul><li>{_esc(detail)}</li>'
                    f'<li>Reason: {_esc((app.reason or "").strip()[:300])}</li></ul>')

        for signer in approvers:
            link = make_action_link(signer, 'loan_approve', label='Approve loan',
                                    loan_id=str(app.pk))
            html = (
                f'<p>{_esc(who)} has applied for a loan.</p>'
                f'{pack}'
                f'{app_link_html("https://omni.alphadirect.co.bw")}'
                f'<p><a href="{_esc(link)}" style="display:inline-block;background:#F4A623;'
                f'color:#0D1B2A;text-decoration:none;padding:11px 22px;border-radius:6px;'
                f'font-weight:700">Approve this loan</a></p>'
                f'<p style="font-size:13px;color:#475467">One tap, no sign-in needed. '
                f'To decline, or to change the amount or the term, open it in Omni: '
                f'<a href="https://omni.alphadirect.co.bw/staff-loans">Staff loans</a> - '
                f'a decline needs a reason.</p>'
                f'<p>Thank you.</p>'
            )
            send_html_with_cfo_cc(
                subject, html, [signer.email],
                text_fallback=f'{who} has applied for a loan ({detail}). Approve: {link}',
                cc=None, cc_cfo=False,
            )
    except Exception as exc:  # noqa: BLE001 - never block a submit
        log.warning('Staff loan approval email failed for %s: %s',
                    getattr(app, 'pk', '?'), exc)
