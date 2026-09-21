"""recruitment/authority_notify.py — tell the signatories an authority is waiting.

CFO directive 2026-08-11. The five-signature instrument shipped (PR #581) with no
notification of any kind: an authority was raised and simply sat there. Both
records on prod at the time this was written — ARG-2026-0001 (Kago Tshutlhedi) and
ATR-2026-0001 (Bokang Bobby Mothibi) — had been `pending` with 0 of 5 signed
because nobody was ever told.

Two rules this module exists to honour:

* **No pay figures in the email.** The authority carries a named individual's full
  package. The notification names the person, the position and the reference, and
  sends the reader into Omni to see the rest — so the confidentiality boundary
  stays inside `authority_access.can_view` and is never widened by an inbox.

* **`allow_named_exec=True` is mandatory here.** Two of the five signatories are
  the CEO (`aiyer@`) and the COO (`arjuniyer@`), both on `core.notifications._NEVER_CC`.
  Without the flag they are stripped from the recipients *silently* while the send
  reports success — which is exactly what happened on 2026-08-10 when the CFO asked
  for aiyer@ on an IT ticket and Arun was simply not on the message. Two of five
  signatories never hearing about a document they must sign is the same bug.
"""
from __future__ import annotations

import logging

from core.notifications import send_html_with_cfo_cc

from .models import AuthorityToRecruit

log = logging.getLogger(__name__)

AUTHORITIES_URL = 'https://omni.alphadirect.co.bw/recruitment/authorities'

_NAVY = '#1D3270'
_ORANGE = '#F47C20'


def _label_for(authority: AuthorityToRecruit, slug: str) -> str:
    for s, label, _addr in authority.signatory_chain():
        if s == slug:
            return label
    return slug


def outstanding_recipients(authority: AuthorityToRecruit) -> list[str]:
    """Email addresses of the signatories who have not yet recorded a decision.

    Read from the authority's OWN chain (tier-driven under Unami's SOP) so every
    signatory on it — including the Finance Manager, the Board Chair and the
    per-role Hiring Manager — is chased, and a signatory present in only one of
    the three places (document, API, tests) is a hole.
    """
    outstanding = set(authority.outstanding_signatories())
    return [addr for slug, _label, addr in authority.signatory_chain()
            if slug in outstanding and addr]


def _action_buttons(authority: AuthorityToRecruit, recipient: str) -> str:
    """Login-free Approve / Decline buttons bound to THIS recipient (CFO
    2026-08-18). Returns '' if the address is not an active omni user, so the
    'Open it in Omni' link below is the fallback. Each recipient's email carries
    only their own signed token — never a shared link."""
    from django.contrib.auth.models import User
    from .authority_actions import action_url
    user = User.objects.filter(email__iexact=(recipient or '').strip(),
                               is_active=True).first()
    if user is None:
        return ''
    url = action_url(authority, user)
    return (
        f'<table role="presentation" width="100%" style="max-width:420px;margin:18px 0 6px"><tr>'
        f'<td style="padding-right:6px">'
        f'<a href="{url}" style="display:block;text-align:center;background:#F4A623;'
        f'color:#0D1B2A;text-decoration:none;padding:13px;border-radius:8px;'
        f'font-weight:bold;font-size:15px">&#10003; Approve</a></td>'
        f'<td style="padding-left:6px">'
        f'<a href="{url}#decline" style="display:block;text-align:center;background:#DC2626;'
        f'color:#fff;text-decoration:none;padding:13px;border-radius:8px;'
        f'font-weight:bold;font-size:15px">&#10007; Decline</a></td>'
        f'</tr></table>'
        f'<p style="color:#555;font-size:10pt;margin:0">Opens a quick page &mdash; no omni '
        f'sign-in needed. Approve, or decline with a reason, right there.</p>')


def _body(authority: AuthorityToRecruit, *, lead: str, recipient: str = '') -> str:
    kind = ('Authority to Recruit' if authority.kind == AuthorityToRecruit.Kind.RECRUIT
            else 'Authority to Regrade')
    chain = authority.signatory_chain()
    signed = len(chain) - len(authority.outstanding_signatories())
    total = len(chain)
    waiting = ', '.join(_label_for(authority, s) for s in authority.outstanding_signatories())
    buttons = _action_buttons(authority, recipient) if recipient else ''
    return f"""<div style="font-family:'Book Antiqua',Palatino,Georgia,serif;font-size:11pt;color:#0D1B2A;line-height:1.45;max-width:640px">
  <div style="background:{_NAVY};color:#fff;padding:12px 18px;font-size:16px;font-weight:bold">
    {kind} &mdash; your signature is needed
  </div>
  <p>{lead}</p>
  <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:10.5pt">
    <tr><td style="color:#555">Reference</td><td><b>{authority.reference}</b></td></tr>
    <tr><td style="color:#555">Person</td><td><b>{authority.person_name}</b></td></tr>
    <tr><td style="color:#555">Position</td><td>{authority.position}</td></tr>
    <tr><td style="color:#555">Entity</td><td>{authority.entity}</td></tr>
    <tr><td style="color:#555">Signed so far</td><td>{signed} of {total}</td></tr>
    <tr><td style="color:#555">Still to sign</td><td>{waiting or '&mdash;'}</td></tr>
  </table>
  {buttons}
  <p style="margin:22px 0">
    <a href="{AUTHORITIES_URL}" style="background:{_ORANGE};color:#fff;text-decoration:none;padding:11px 20px;border-radius:6px;font-weight:bold">Open it in Omni and sign</a>
  </p>
  <p style="color:#555;font-size:10pt">The package and the justification are on the document in Omni.
  They are deliberately not in this email &mdash; only the signatories may see them.</p>
</div>"""


_CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'


def _drop_cfo_when_consolidated(addrs: list[str]) -> list[str]:
    """CFO 2026-08-12: the CFO no longer wants a per-authority 'needs your
    signature' email — outstanding authorities now show in his morning brief
    (see hris.workforce_brief.outstanding_authorities_brief). Drop only the CFO,
    and only when the consolidation flag is on; the other four signatories still
    get their emails. A decline (notify_declined) is NOT routed through here, so
    the CFO still hears about a terminal decline by email."""
    from django.conf import settings
    if not getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False):
        return addrs
    return [a for a in addrs if (a or '').strip().lower() != _CFO_EMAIL]


def notify_outstanding(authority: AuthorityToRecruit, *, lead: str) -> int:
    """Email every signatory who still owes a decision. Returns messages sent.

    One email PER recipient (CFO 2026-08-18): each carries that person's own
    login-free Approve/Decline buttons, and the signed token in a button is a
    credential — it must never travel to anyone but its owner, so a single
    shared 'to all' message is no longer safe.
    """
    to = outstanding_recipients(authority)
    if not to:
        return 0
    to = _drop_cfo_when_consolidated(to)
    if not to:
        return 0
    kind = ('Authority to Recruit' if authority.kind == AuthorityToRecruit.Kind.RECRUIT
            else 'Authority to Regrade')
    subject = (f'{kind} {authority.reference} — {authority.person_name} '
               f'— needs your signature')
    sent = 0
    for addr in to:
        try:
            sent += send_html_with_cfo_cc(
                subject=subject,
                html=_body(authority, lead=lead, recipient=addr),
                to=[addr],
                # MANDATORY: the CEO and COO are on the never-auto-CC list and
                # are silently stripped without this. See the module docstring.
                allow_named_exec=True,
                # NEVER auto-CC excoboard@ here (CFO 2026-08-18): each email now
                # carries THIS recipient's personal login-free signing token, and
                # a CC would hand a shared mailbox valid tokens for every slot —
                # defeating the per-recipient split. The CFO already gets his own
                # button in the morning brief; a decline still CCs him
                # (notify_declined carries no token).
                cc_cfo=False,
            )
        except Exception as exc:  # noqa: BLE001
            # Never let a mail failure roll back or 500 the signing action.
            log.exception('[atr-notify] %s: could not notify %s: %s',
                          authority.reference, addr, exc)
    log.info('[atr-notify] %s: notified %s (sent=%s)', authority.reference, to, sent)
    return sent


def notify_raised(authority: AuthorityToRecruit) -> int:
    return notify_outstanding(
        authority,
        lead=('A new authority has been raised and is waiting for your signature. '
              'An offer cannot go out until all signatories are in.'))


def notify_after_signature(authority: AuthorityToRecruit, *, by: str) -> int:
    """Someone signed; chase whoever is left."""
    if authority.status != AuthorityToRecruit.Status.PENDING:
        return 0
    return notify_outstanding(
        authority,
        lead=f'{by} has signed. This authority is still waiting for your signature.')


def notify_declined(authority: AuthorityToRecruit, *, by: str, notes: str) -> int:
    """A decline stops the chain — tell the raiser and the other signatories."""
    to = []
    creator_email = (getattr(authority.created_by, 'email', '') or '').strip()
    if creator_email:
        to.append(creator_email)
    for _slug, _label, addr in authority.signatory_chain():
        if addr and addr not in to:
            to.append(addr)
    kind = ('Authority to Recruit' if authority.kind == AuthorityToRecruit.Kind.RECRUIT
            else 'Authority to Regrade')
    html = f"""<div style="font-family:'Book Antiqua',Palatino,Georgia,serif;font-size:11pt;color:#0D1B2A;line-height:1.45;max-width:640px">
  <div style="background:#8B1E1E;color:#fff;padding:12px 18px;font-size:16px;font-weight:bold">
    {kind} {authority.reference} &mdash; DECLINED
  </div>
  <p><b>{by}</b> has declined this authority. It is now closed and no further
  signatures can be recorded. No offer may go out.</p>
  <table cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:10.5pt">
    <tr><td style="color:#555">Person</td><td><b>{authority.person_name}</b></td></tr>
    <tr><td style="color:#555">Position</td><td>{authority.position}</td></tr>
    <tr><td style="color:#555">Reason given</td><td>{notes or '&mdash;'}</td></tr>
  </table>
  <p style="margin:20px 0">
    <a href="{AUTHORITIES_URL}" style="background:{_ORANGE};color:#fff;text-decoration:none;padding:11px 20px;border-radius:6px;font-weight:bold">Open it in Omni</a>
  </p>
</div>"""
    try:
        sent = send_html_with_cfo_cc(
            subject=f'{kind} {authority.reference} — {authority.person_name} — DECLINED',
            html=html, to=to, allow_named_exec=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception('[atr-notify] %s: decline notice failed: %s', authority.reference, exc)
        return 0
    log.info('[atr-notify] %s: decline notice sent to %s (sent=%s)',
             authority.reference, to, sent)
    return sent
