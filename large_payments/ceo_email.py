"""The authorisation email to the CEO, and its no-login buttons.

CFO 2026-09-12, the constraint that shaped everything here: **"Arun won't use
omni."** So THE EMAIL IS THE WHOLE INTERFACE. Every button works with no login,
nothing requires him to open Omni, and the task raised for him is a RECORD and a
chase — never the way he acts. A design that needed him to sign in would be a
design that never got used.

WHY NO-LOGIN IS THE RIGHT CALL HERE, STATED RATHER THAN ASSUMED.
core/magic_action.py carries the CFO's own 2026-08-12 rule: low-risk actions are
one-click, "money approvals are NOT — those use a view-only link + normal
sign-in". This is deliberately classified as low-risk, and the reasoning is on
the record so nobody has to guess later:
  * Omni moves no money, ever. The CFO releases each payment himself in the FNB
    app with two-factor.
  * these payments have ALREADY reached FNB before the request is raised — the
    CFO settled that on 2026-09-11 — so the CEO's authorisation is a RECORD, not
    a release gate. His click debits nothing and releases nothing.
  * refusing costs nothing either: it returns the request, it cannot claw money
    back, and it frees the payments to go on a corrected request.
If that ever stops being true — if a future piece makes the CEO's click actually
release money — this classification MUST be revisited, because then it becomes
exactly the money approval the 2026-08-12 rule excludes.

THE EXPOSURE, STATED PLAINLY RATHER THAN GLOSSED: anyone who can read the CEO's
mailbox — a delegate, a forwarding rule, a compromise — can press Approve for the
72 hours the link lives. The CFO was told this in those words on 2026-09-12 and
answered "LETS SKIP THIS, EMAIL IS ENOUGH". It is his decision, it is recorded,
and it is not to be re-raised. core/magic_action.py carries the matching note.

THE DOCUMENT GOES IN THE BODY, IN FULL.
The CFO was angry on 2026-09-01 when an authorisation carried a short summary
table with the real detail in an attachment. The whole document goes in the body
as HTML tables. An attachment may ride along; the body must stand alone.

WHAT MUST NOT GO TO THE CEO.
Data-quality findings — a claim missing from Graphite, a placeholder insured
name, a status mismatch — are NOT in this email. They go to Wangu Moses and Kago
Tshutlhedi as their own urgent note. The CEO's email stays the tables, the
figures and the signatures. A CEO asked to adjudicate someone's data entry is a
CEO who stops reading these.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from html import escape

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

log = logging.getLogger(__name__)
User = get_user_model()

NAVY = '#0D1B2A'
ORANGE = '#F4A623'

#: Standing cc list, CFO directive 2026-09-10 — all five on EVERY large-payment
#: email. Kept here as the default and resolvable from settings so it changes
#: without a deploy.
DEFAULT_CEO_EMAIL = 'aiyer@alphadirect.co.bw'
DEFAULT_CC = [
    'pbeka@alphadirect.co.bw',        # Paul Beka
    'btendani@alphadirect.co.bw',     # Bontle Tendani
    'ktshutlhedi@alphadirect.co.bw',  # Kago Tshutlhedi — Finance Manager
    'pkago@alphadirect.co.bw',        # Pako Kago — Financial Controller
    'wmoses@alphadirect.co.bw',       # Wangu Moses — Claims Manager
]

#: Who the CEO may put a question to, by role. Named accounts, never job titles:
#: Omni titles are unreliable (see the notebook), so a title lookup would send a
#: claims question to whoever happens to be labelled claims_manager today.
QUESTION_TARGETS = [
    ('wmoses@alphadirect.co.bw',     'Wangu Moses (Claims)'),
    ('ktshutlhedi@alphadirect.co.bw', 'Kago Tshutlhedi (Finance)'),
    ('pkago@alphadirect.co.bw',      'Pako Kago (Financial Control)'),
    ('btendani@alphadirect.co.bw',   'Bontle Tendani (Finance)'),
    ('pbeka@alphadirect.co.bw',      'Paul Beka'),
]


def ceo_email_address() -> str:
    return getattr(settings, 'LARGE_PAYMENT_CEO_EMAIL', '') or DEFAULT_CEO_EMAIL


def cc_addresses() -> list[str]:
    configured = getattr(settings, 'LARGE_PAYMENT_CC_EMAILS', None)
    return list(configured) if configured else list(DEFAULT_CC)


def ceo_user():
    """The CEO's Omni account, for signing his no-login links.

    Matched on the EMAIL, not the title: any cfo/ceo-titled profile is an Omni
    administrator and a mailbox can be re-titled, so a title match is not an
    identity. Returns None if he has no active account — the caller then sends a
    plain email with no buttons rather than silently sending unusable links.
    """
    return (User.objects
            .filter(email__iexact=ceo_email_address(), is_active=True)
            .first())


def _money(v) -> str:
    return f'{Decimal(v):,.2f}'


def _cell(v, *, align='left', bold=False, small=False) -> str:
    weight = 'font-weight:600;' if bold else ''
    size = 'font-size:12px;' if small else 'font-size:13px;'
    return (f'<td style="padding:7px 9px;border-bottom:1px solid #E5E7EB;'
            f'text-align:{align};{weight}{size}color:#1F2A37;">{v}</td>')


def _th(v, *, align='left') -> str:
    return (f'<th style="padding:7px 9px;text-align:{align};font-size:11px;'
            f'letter-spacing:.04em;text-transform:uppercase;color:#6B7280;'
            f'border-bottom:2px solid {NAVY};">{v}</th>')


def _button(href: str, label: str, *, bg: str, fg: str = '#FFFFFF') -> str:
    return (f'<a href="{escape(href, quote=True)}" '
            f'style="display:inline-block;padding:12px 20px;margin:4px 6px 4px 0;'
            f'background:{bg};color:{fg};text-decoration:none;border-radius:6px;'
            f'font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            f'font-weight:600;">{escape(label)}</a>')


def build_document_html(req, *, links: dict | None = None) -> str:
    """The authorisation document, in full, as the email body.

    `links` maps action -> magic URL. When it is None (no CEO account, or a
    preview) the buttons are omitted and the document still reads correctly —
    the document is the point; the buttons are how he answers.
    """
    lines = list(req.lines.all())
    total = sum((ln.amount for ln in lines), Decimal('0.00'))
    today = timezone.localtime().strftime('%d %B %Y')

    rows = []
    for ln in lines:
        insured = ln.insured_name or ''
        claim = ln.claim_number or ''
        loss = (ln.loss_description or '').strip()
        if len(loss) > 90:
            loss = loss[:87] + '…'
        # ESCAPE THE VALUE, THEN fall back to the dash — never escape the dash
        # itself. escape('&mdash;') is '&amp;mdash;', which renders as the literal
        # text "&mdash;". Payee is EMPTY on the live G2026004923 records, so the
        # very first real email would have printed "&mdash;" down a whole column
        # of a CEO authorisation (Fable 5.1, 2026-09-12).
        rows.append(
            '<tr>'
            + _cell(escape(claim) or '&mdash;', bold=True)
            + _cell(escape(insured) or '&mdash;')
            + _cell(escape(ln.payee or '') or '&mdash;')
            + _cell(escape(ln.policy_number or '') or '&mdash;', small=True)
            + _cell(ln.date_of_loss.strftime('%d %b %Y') if ln.date_of_loss else '&mdash;',
                    small=True)
            + _cell(escape(loss) or '&mdash;', small=True)
            + _cell(_money(ln.amount), align='right', bold=True)
            + '</tr>')

    button_block = ''
    if links:
        button_block = f'''
        <div style="margin:26px 0 8px;padding:18px;background:#F9FAFB;
                    border:1px solid #E5E7EB;border-radius:8px;">
          <div style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;
                      color:#6B7280;margin-bottom:10px;">Your decision &mdash; no sign-in needed</div>
          {_button(links['approve_all'], 'Approve all ' + str(len(lines)) + ' payments', bg='#1B7A3B')}
          {_button(links['reject'], 'Refuse this request', bg='#C53030')}
          {_button(links['more_detail'], 'Ask for more detail', bg=NAVY)}
          <div style="margin-top:12px;font-size:13px;color:#1F2A37;">
            Or put a question to one person:
          </div>
          <div style="margin-top:6px;">
            {''.join(_button(links['ask'][email], name, bg='#FFFFFF', fg=NAVY)
                     for email, name in QUESTION_TARGETS if email in links['ask'])}
          </div>
          <div style="margin-top:12px;font-size:12px;color:#6B7280;line-height:1.5;">
            Each button opens a page showing what it will do and asks you to confirm,
            so nothing happens by opening this email or by a link scanner following it.
            Every link works once and expires in three days.
          </div>
        </div>'''

    flagged = [ln for ln in lines if ln.flags]
    note_block = ''
    if flagged:
        # Deliberately a COUNT, not the findings. The detail goes to Wangu and
        # Kago; the CEO is told only that Finance is on it, so he knows the
        # figures are not being presented as flawless.
        note_block = (
            f'<p style="font-size:13px;color:#92400E;background:#FEF3C7;'
            f'border:1px solid #FDE68A;border-radius:6px;padding:10px 12px;">'
            f'{len(flagged)} of these payments have a query raised with Claims and '
            f'Finance on the supporting claim record. The amounts and payees above '
            f'are as loaded and verified in the banking platform.</p>')
    # That sentence is only true because notify_claims_queries() below actually
    # raises it. It said so before anything did (Fable 5.1, 2026-09-12) — a
    # sentence in front of the CEO that nothing in the code makes true.

    return f'''<div style="font-family:Arial,Helvetica,sans-serif;color:#1F2A37;
                           max-width:920px;">
  <div style="border-top:6px solid {ORANGE};padding-top:14px;">
    <div style="font-family:Georgia,'Book Antiqua',serif;font-size:21px;
                font-weight:bold;color:{NAVY};">Alpha Direct Insurance Company</div>
    <div style="font-size:17px;color:{NAVY};margin-top:2px;">Payment Authorisation</div>
  </div>

  <table style="margin-top:14px;font-size:13px;border-collapse:collapse;">
    <tr><td style="padding:2px 14px 2px 0;color:#6B7280;">To</td>
        <td style="padding:2px 0;">Mr Arun Iyer &ndash; Chief Executive Officer</td></tr>
    <tr><td style="padding:2px 14px 2px 0;color:#6B7280;">Date</td>
        <td style="padding:2px 0;">{today}</td></tr>
    <tr><td style="padding:2px 14px 2px 0;color:#6B7280;">Subject</td>
        <td style="padding:2px 0;">Payment Approval &ndash; Claims Payments</td></tr>
    <tr><td style="padding:2px 14px 2px 0;color:#6B7280;">Reference</td>
        <td style="padding:2px 0;">{escape(req.ref)}</td></tr>
  </table>

  <p style="font-size:14px;line-height:1.6;margin-top:16px;">Mr Iyer,</p>
  <p style="font-size:14px;line-height:1.6;">
    Please find below the payment authorisation request for your review and
    approval. All payments have been loaded and verified in the banking platform.
  </p>

  {note_block}

  <div style="font-size:12px;text-transform:uppercase;letter-spacing:.04em;
              color:#6B7280;margin:22px 0 6px;">
    Claims Account Payments Loaded &amp; Verified
  </div>
  <table style="width:100%;border-collapse:collapse;">
    <thead><tr>
      {_th('Claim')}{_th('Insured')}{_th('Payee')}{_th('Policy')}
      {_th('Date of loss')}{_th('Loss')}{_th('Amount (BWP)', align='right')}
    </tr></thead>
    <tbody>{''.join(rows)}</tbody>
    <tfoot><tr>
      <td colspan="6" style="padding:9px;text-align:right;font-weight:700;
                             font-size:13px;border-top:2px solid {NAVY};">
        Total Claims Payments ({len(lines)})</td>
      <td style="padding:9px;text-align:right;font-weight:700;font-size:14px;
                 border-top:2px solid {NAVY};">{_money(total)}</td>
    </tr></tfoot>
  </table>

  {button_block}

  <div style="margin-top:24px;font-size:12px;color:#6B7280;line-height:1.6;
              border-top:1px solid #E5E7EB;padding-top:12px;">
    Prepared and submitted by {escape(req.raised_by.get_full_name()
                                      or req.raised_by.username)},
    authorised by the Chief Financial Officer, and raised from Omni.
    These payments are already loaded at the bank; your approval is recorded as
    the CEO authorisation of this request and does not itself release any money.
  </div>
  <div style="margin-top:10px;font-size:11px;color:#9CA3AF;">
    Alpha Direct Insurance Company &nbsp;|&nbsp; Confidential &nbsp;|&nbsp;
    For Internal Use Only
  </div>
</div>'''


def build_links(req) -> dict | None:
    """The CEO's no-login links, or None when he has no active Omni account."""
    ceo = ceo_user()
    if ceo is None:
        log.warning('large_payments: no active account for %s — sending %s with no '
                    'buttons rather than dead links', ceo_email_address(), req.ref)
        return None

    from core.magic_action import make_action_link
    rid = str(req.id)
    links = {
        'approve_all': make_action_link(ceo, 'lp_approve_all', r=rid,
                                        label=f'Approve {req.ref}'),
        'reject':      make_action_link(ceo, 'lp_reject', r=rid,
                                        label=f'Refuse {req.ref}'),
        'more_detail': make_action_link(ceo, 'lp_more_detail', r=rid,
                                        label=f'More detail on {req.ref}'),
        'ask': {},
    }
    for email, name in QUESTION_TARGETS:
        links['ask'][email] = make_action_link(ceo, 'lp_ask', r=rid, a=email,
                                               label=f'Ask {name}')
    return links


def send_to_ceo(req) -> tuple[bool, str]:
    """Send the authorisation to the CEO. Returns (ok, message).

    THE TRAP THIS FUNCTION EXISTS TO AVOID: `aiyer@alphadirect.co.bw` is in
    core.notifications._NEVER_CC and is STRIPPED FROM TO AND CC unless the sender
    is told otherwise. On 2026-08-10 the CFO asked for Arun on an IT ticket, the
    send returned SUCCESS, and Arun was simply not on the message. So this passes
    allow_named_exec=True, and then RECORDS who the send actually reached instead
    of assuming it matched the intent.
    """
    from core.notifications import send_html_with_cfo_cc

    links = build_links(req)
    html = build_document_html(req, links=links)
    to = [ceo_email_address()]
    cc = cc_addresses()

    subject = (f'Payment Authorisation Request – Claims Payments – '
               f'{timezone.localtime().strftime("%d %B %Y")} – {req.ref}')

    try:
        sent = send_html_with_cfo_cc(
            subject, html, to,
            cc=cc,
            text_fallback=('This authorisation request is an HTML email. Open it in '
                           'Outlook to see the payment table and the approval '
                           'buttons.'),
            # Without this, the CEO is silently removed from his own email.
            allow_named_exec=True,
            # Never the internal "do not reply, log it in Omni" banner: this email
            # ASKS HIM A QUESTION and he answers by pressing a button or replying.
            no_reply=False,
        )
    except Exception as exc:                              # noqa: BLE001
        log.exception('large_payments: sending %s to the CEO failed', req.ref)
        return False, f'The email did not go out: {exc}'

    if not sent:
        return False, ('The mail server accepted nothing — the authorisation was '
                       'NOT sent. Nothing has been changed.')

    # What we ASKED the sender to deliver to. Omni's sender also auto-ccs
    # excoboard@, which is not listed here, and it can still drop an address for
    # its own reasons — so this is the intent, not a delivery receipt. "Accepted"
    # is not "delivered" (Fable 5.1, 2026-09-12).
    recipients = to + cc
    req.sent_recipients = recipients
    req.sent_to_ceo_at = timezone.now()
    req.send_error = ''
    req.status = req.Status.SENT_TO_CEO
    req.save(update_fields=['sent_recipients', 'sent_to_ceo_at', 'send_error',
                            'status', 'updated_at'])

    if not links:
        return True, ('Sent, but WITHOUT the approval buttons: there is no active '
                      f'Omni account for {ceo_email_address()}, so no no-login '
                      'link could be signed. He can still reply by email.')
    return True, f'Sent to {ceo_email_address()} and {len(cc)} others.'


#: The data-quality findings go HERE, never to the CEO (largepayment skill,
#: step 4b). Named accounts, not titles.
QUERY_RECIPIENTS = [
    ('wmoses@alphadirect.co.bw',      'Wangu Moses'),
    ('ktshutlhedi@alphadirect.co.bw', 'Kago Tshutlhedi'),
]


def notify_claims_queries(req) -> int:
    """Raise the claim queries with Claims and Finance. Returns tasks raised.

    The CEO's email tells him "N of these have a query raised with Claims and
    Finance". Until this existed, that sentence was simply not true — the module
    docstring promised the note and nothing sent it (Fable 5.1, 2026-09-12). A
    claim that a control ran, when no control ran, is worse than saying nothing.

    Best effort: a failure here must never undo the CFO's approval or stop the
    authorisation reaching the CEO.
    """
    from django.contrib.auth import get_user_model
    from core.models import OmniTask
    from .permissions import cfo_user

    flagged = [ln for ln in req.lines.all() if ln.flags]
    if not flagged:
        return 0

    body = [f'{len(flagged)} payment(s) on authorisation {req.ref} carry a query '
            f'on the supporting claim record. The CEO has been sent the '
            f'authorisation and has been told only that a query exists — the '
            f'detail is below and is yours to clear.', '']
    for ln in flagged:
        body.append(f'{ln.claim_number or ln.payment_ref} — {ln.payee or "payee not stated"} '
                    f'— BWP {ln.amount:,.2f}')
        for f in ln.flags:
            body.append(f'    • {f.get("message", "")}')
        body.append('')
    body.append('Answer to the CFO today. Do not reply to the CEO directly unless '
                'he asked you himself.')

    raised = 0
    cfo = cfo_user()
    User = get_user_model()
    for email, name in QUERY_RECIPIENTS:
        person = User.objects.filter(email__iexact=email, is_active=True).first()
        if person is None:
            log.warning('large_payments: no active account for %s — no query task', email)
            continue
        try:
            OmniTask.objects.create(
                assigner=cfo or person, assignee=person,
                title=f'URGENT: claim queries before payment release — {req.ref}',
                body=chr(10).join(body),
                priority=OmniTask.Priority.URGENT,
                due_at=timezone.localtime().date(),
                source='large_payment_query',
            )
            raised += 1
        except Exception:                                 # noqa: BLE001
            log.exception('large_payments: could not raise the query task for %s', email)
    return raised
