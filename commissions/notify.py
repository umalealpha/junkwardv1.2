"""commissions/notify.py — after a commission is payroll-processed, email the
1st-stage reviewers (Bokani / Tlamelo) to action it, cc the 2nd stage (Pako /
Kago). CFO 2026-07-15.

Server-side send via core.notifications.send_html_with_cfo_cc (Microsoft Graph on
prod). Recipients come from the stage rosters' env emails — set
COMMISSIONS_STAGE1_EMAILS (to) and COMMISSIONS_STAGE2_EMAILS (cc); without a
stage-1 address the notify is skipped (nothing to send to).
"""
from __future__ import annotations

import html as _html

from .access import STAGE1, STAGE2, _stage_emails

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def _body_html(sub) -> str:
    e = _html.escape
    money = lambda v: f"BWP {float(v or 0):,.2f}"
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:620px;margin:0 auto;">
  <div style="background:{NAVY};padding:18px 22px;border-radius:10px 10px 0 0;">
    <div style="font-family:'Book Antiqua',Palatino,Georgia,serif;color:#fff;font-size:20px;font-weight:600;">Commission processed</div>
    <div style="color:{ORANGE};font-size:11px;letter-spacing:.08em;margin-top:2px;">ALPHA DIRECT · MONTHLY AGENT COMMISSION</div>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:none;border-radius:0 0 10px 10px;padding:20px 22px;color:#1e293b;font-size:14px;line-height:1.55;">
    <p>Hi Bokani and Tlamelo,</p>
    <p>The commission below has been approved and payroll has processed it. Please use this to action the payment.</p>
    <table style="border-collapse:collapse;margin:14px 0;font-size:13px;">
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Agent</td><td style="padding:4px 0;font-weight:600;">{e(sub.agent.name)}</td></tr>
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Group</td><td style="padding:4px 0;">{e(sub.group.name)}</td></tr>
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Month</td><td style="padding:4px 0;">{e(sub.period_label)}</td></tr>
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Gross</td><td style="padding:4px 0;">{money(sub.gross_commission)}</td></tr>
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Withholding</td><td style="padding:4px 0;">&minus;{money(sub.withholding_amount)}</td></tr>
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Net payable</td><td style="padding:4px 0;font-weight:700;color:{ORANGE};">{money(sub.net_payable)}</td></tr>
    </table>
    <p>The payout details are in the attached file.</p>
    <p style="color:#64748b;font-size:12px;">Pako and Kago are copied for the record.</p>
    <p style="margin-top:18px;">Regards,<br/>Alpha Direct — Commissions</p>
  </div>
</div>"""


def notify_payroll_done(sub) -> dict:
    """Email TO the 1st-stage reviewers, CC the 2nd, with the payout attached.
    Returns {'sent': n, ...}; sent=0 (no send) when no stage-1 email is set."""
    to = sorted(_stage_emails(STAGE1))
    cc = sorted(_stage_emails(STAGE2))
    if not to:
        return {'sent': 0, 'reason': 'no COMMISSIONS_STAGE1_EMAILS configured'}
    from . import service
    csv_text = service.submission_payout_csv(sub)
    from core.notifications import send_html_with_cfo_cc
    n = send_html_with_cfo_cc(
        subject=f"Commission processed — {sub.agent.name} · {sub.period_label}",
        html=_body_html(sub),
        to=to,
        cc=cc,
        text_fallback=(f"{sub.agent.name} commission for {sub.period_label} is payroll-processed. "
                       f"Net BWP {float(sub.net_payable or 0):,.2f}. Payout details attached."),
        attachments=[(f"commission_{sub.group.key}_{sub.period_label}_{sub.agent.name}.csv".replace(' ', '_'),
                      csv_text.encode('utf-8'), 'text/csv')],
    )
    return {'sent': n, 'to': to, 'cc': cc}


def _agent_statement_html(sub) -> str:
    """The agent's OWN monthly statement, as an HTML email body: their policy
    lines + gross/withholding/net. This IS the statement (no PDF) so an agent can
    see exactly what their commission is based on (CFO 2026-08-22, month-close)."""
    e = _html.escape
    money = lambda v: f"BWP {float(v or 0):,.2f}"
    # Client/policyholder NAMES are deliberately NOT in the email — an outbound
    # statement is an un-anonymised export otherwise (C5, panel 2026-08-22). Policy
    # number + commission still show the agent what each line is based on. Sort in
    # Python so the prefetched `lines` cache is used (no per-agent query — K5).
    lines = sorted(sub.lines.all(), key=lambda l: (l.policy_number or ''))
    rows = ''.join(
        f"<tr><td style='padding:3px 10px 3px 0;'>{e(l.policy_number or '')}</td>"
        f"<td style='padding:3px 0;text-align:right;'>{money(l.commission_amount)}</td></tr>"
        for l in lines)
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:620px;margin:0 auto;">
  <div style="background:{NAVY};padding:18px 22px;border-radius:10px 10px 0 0;">
    <div style="font-family:'Book Antiqua',Palatino,Georgia,serif;color:#fff;font-size:20px;font-weight:600;">Your commission statement</div>
    <div style="color:{ORANGE};font-size:11px;letter-spacing:.08em;margin-top:2px;">ALPHA DIRECT · {e(sub.period_label)}</div>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:none;border-radius:0 0 10px 10px;padding:20px 22px;color:#1e293b;font-size:14px;line-height:1.55;">
    <p>Hi {e(sub.agent.name)},</p>
    <p>Your commission for <b>{e(sub.period_label)}</b> has been approved. Here is what it is based on:</p>
    <table style="border-collapse:collapse;margin:12px 0;font-size:13px;width:100%;">
      <tr style="color:#64748b;text-align:left;"><th style="padding:3px 10px 3px 0;">Policy</th><th style="padding:3px 0;text-align:right;">Commission</th></tr>
      {rows}
    </table>
    <table style="border-collapse:collapse;margin:10px 0;font-size:13px;">
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Gross</td><td style="padding:4px 0;">{money(sub.gross_commission)}</td></tr>
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Withholding</td><td style="padding:4px 0;">&minus;{money(sub.withholding_amount)}</td></tr>
      <tr><td style="padding:4px 14px 4px 0;color:#64748b;">Net payable</td><td style="padding:4px 0;font-weight:700;color:{ORANGE};">{money(sub.net_payable)}</td></tr>
    </table>
    <p style="color:#64748b;font-size:12px;">Questions? Reply to this email.</p>
    <p style="margin-top:16px;">Regards,<br/>Alpha Direct — Commissions</p>
  </div>
</div>"""


def email_agent_statement(sub) -> dict:
    """Email ONE agent their own statement. Finance-only send (no CFO cc on each
    agent's mail). Returns {'sent': n}; sent=0 when the agent has no email."""
    to = [sub.agent.email] if getattr(sub.agent, 'email', '') else []
    if not to:
        return {'sent': 0, 'reason': 'agent has no email'}
    from core.notifications import send_html_with_cfo_cc
    n = send_html_with_cfo_cc(
        subject=f"Your commission statement — {sub.period_label}",
        html=_agent_statement_html(sub),
        to=to,
        text_fallback=(f"Your {sub.period_label} commission is approved. "
                       f"Net BWP {float(sub.net_payable or 0):,.2f}. See Omni for detail."),
        cc_cfo=False,   # each agent's own statement — do NOT cc the CFO/EXCO on every one
    )
    return {'sent': n, 'to': to}


def notify_commission_awaiting_review(sub) -> dict:
    """Tell the reviewers for the stage this submission is NOW at, each with a
    one-tap Approve link (CFO 2026-09-12: exco and managers work off site).

    The `commission_approve` email handler existed before this and nothing ever
    produced a link for it — a registered action with no sender is dead code that
    the test suite reads as delivered (/fabe 2026-09-13, class L32). This is the
    sender.

    The link is personal: `make_action_link` signs the reviewer's own user id, and
    tapping it runs `service.review`, the same call the Omni page makes — so the
    stage, the maker-checker rule, the you-signed-this-earlier rule and the
    pays-you rule are all re-checked at the moment of the tap, not at the moment
    the email was written. A reviewer who is no longer entitled to sign gets the
    refusal, not the approval.

    Declining opens Omni. A decline needs a reason typed in, and a link that
    silently rejects on one tap is a link that rejects by accident.
    """
    from .access import stage_of, stage_users

    stage = stage_of(sub)
    if not stage:
        return {'sent': 0, 'reason': 'not awaiting review'}

    from core.approval_pack import app_link_html, build_pack, pack_html
    from core.magic_action import make_action_link
    from core.notifications import send_html_with_cfo_cc
    from django.conf import settings

    base = (getattr(settings, 'OMNI_PUBLIC_BASE_URL', '')
            or 'https://omni.alphadirect.co.bw').rstrip('/')
    agent = getattr(getattr(sub, 'agent', None), 'name', '') or 'an agent'
    gross = f'BWP {float(sub.gross_commission or 0):,.2f}'
    net = f'BWP {float(sub.net_payable or 0):,.2f}'

    # The whole basis of the commission — policy lines, rates, the withholding,
    # the review_flags sanity checks and last month's gross — rendered ONCE and
    # reused for every reviewer. Before 2026-09-20 this email carried four lines
    # and the only way to see the detail was to sign in to the desktop site,
    # which the CFO reads on his phone: "expecting a busy cfo to go inside the
    # web version to check is unreasonable".
    detail = pack_html(build_pack('commissions', sub.id))
    # build_pack swallows a builder error by design, so `detail` can come back
    # empty. Falling through with the happy-path wording would send "everything
    # it is based on is below" followed by nothing — a WORSE email than the one
    # being replaced. Best-effort must degrade to what was there, never below it.
    if detail:
        lead = ('A commission is waiting for your review. Everything it is based on is '
                'below &mdash; you do not need to open Omni to check it.')
    else:
        lead = 'A commission is waiting for your review.'
        detail = f"""
  <table style="border-collapse:collapse;margin:12px 0;font-size:14px;">
    <tr><td style="padding:2px 12px 2px 0;color:#64748b;">Agent</td><td>{_html.escape(agent)}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#64748b;">Period</td><td>{_html.escape(sub.period_label or '')}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#64748b;">Gross</td><td>{gross}</td></tr>
    <tr><td style="padding:2px 12px 2px 0;color:#64748b;">Net payable</td><td>{net}</td></tr>
  </table>"""

    sent, to = 0, []
    submitter_id = getattr(sub, 'submitted_by_id', None)
    for u in stage_users(stage):
        # Never ask someone to approve their own submission. review() would
        # refuse it anyway; sending the link is just an invitation to be refused.
        if submitter_id and u.id == submitter_id:
            continue
        if not (u.email or '').strip():
            continue
        link = make_action_link(u, 'commission_approve', sub_id=str(sub.id),
                               label=f'{agent} · {sub.period_label}')
        html = f"""
<div style="font-family:Arial,Helvetica,sans-serif;color:#0D1B2A;max-width:620px;">
  <p>Hello {_html.escape(u.first_name or u.get_username())},</p>
  <p>{lead}</p>
  {detail}
  {app_link_html(base)}
  <p style="margin:18px 0;">
    <a href="{link}" style="background:#F47C20;color:#ffffff;text-decoration:none;
       padding:10px 18px;border-radius:6px;font-weight:bold;display:inline-block;">
      Approve this commission
    </a>
  </p>
  <p style="font-size:13px;">
    To send it back instead, open it in Omni — a decline needs a reason:
    <a href="{base}/commissions">{base}/commissions</a>
  </p>
  <p style="color:#64748b;font-size:12px;">
    The link above is yours alone and works for 72 hours. Your approval is checked
    at the moment you tap it, exactly as it would be in Omni.
  </p>
  <p style="margin-top:18px;">Regards,<br/>Alpha Direct — Commissions</p>
</div>"""
        n = send_html_with_cfo_cc(
            subject=f'Commission for your review — {agent} · {sub.period_label}',
            html=html,
            to=[u.email],
            text_fallback=(f'{agent} commission for {sub.period_label} is waiting for your '
                           f'review. Gross {gross}, net {net}. Open {base}/commissions'),
            # cc_cfo=False. This email carries a one-tap Approve link signed FOR
            # THIS PERSON, and the default cc is the shared excoboard@ mailbox -
            # so every reviewer's personal link, including the CFO's final-stage
            # one, would have landed in a mailbox several people read. A signed
            # link is only as personal as its delivery. (/fabe 2026-09-13, held
            # the deploy; same shape as the staff-loan email, which got it right.)
            cc_cfo=False,
        )
        sent += n or 0
        to.append(u.email)
    return {'sent': sent, 'to': to, 'stage': stage}
