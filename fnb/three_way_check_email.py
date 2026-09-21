"""The daily three-way check, as an email a person can act on.

Kept apart from `three_way_check` so the check itself stays pure and easy to
test: that module answers "what disagrees?", this one answers "how do we say so".
"""
from __future__ import annotations

from html import escape

from django.conf import settings

from .three_way_check import FINDINGS

_DEFAULT_RECIPIENTS = [
    'pganesharajah@alphadirect.co.bw',   # CFO
    'ktshutlhedi@alphadirect.co.bw',     # Kago Tshutlhedi, Finance Manager
    'pkago@alphadirect.co.bw',           # Pako Kago, Financial Controller
]

_NAVY, _ORANGE, _RED, _GREY = '#0D1B2A', '#F4A623', '#C53030', '#64748B'


def _money(v):
    return f'BWP {float(v or 0):,.2f}'


def _table(rows):
    head = ('<tr>' + ''.join(
        f'<th style="text-align:left;padding:4px 10px 4px 0;color:{_GREY};'
        f'font-weight:600;font-size:12px;">{h}</th>'
        for h in ('Reference', 'Payee', 'Amount', 'Omni says', 'Bank says', 'Age')) +
        '</tr>')
    body = ''
    for r in rows:
        body += (
            '<tr>'
            f'<td style="padding:3px 10px 3px 0;">{escape(r["ref"] or "")}</td>'
            f'<td style="padding:3px 10px 3px 0;">{escape((r["payee"] or "")[:40])}</td>'
            f'<td style="padding:3px 10px 3px 0;white-space:nowrap;">{_money(r["total"])}</td>'
            f'<td style="padding:3px 10px 3px 0;">{escape(r["omni_status"] or "")}</td>'
            f'<td style="padding:3px 10px 3px 0;">{escape(r["batch_status"] or "")}</td>'
            f'<td style="padding:3px 10px 3px 0;">{r["age_days"]}d</td>'
            '</tr>')
    return (f'<table style="border-collapse:collapse;font-size:13px;margin:6px 0 14px;">'
            f'{head}{body}</table>')


def _rej_table(rows: list) -> str:
    """One row per payment request, not per bank instruction — a supplier
    request split line by line is ONE business problem (CFO's brief, control 4)."""
    if not rows:
        return ''
    head = ('<table cellpadding="0" cellspacing="0" style="font-size:12px;'
            'border-collapse:collapse;margin:4px 0 0;"><tr>'
            + ''.join(f'<th style="text-align:left;padding:3px 14px 3px 0;'
                      f'color:{_GREY};font-weight:normal;">{h}</th>'
                      for h in ('Reference', 'Payee', 'Amount', 'Omni says',
                                'Instructions', 'Bank said', 'Evidence'))
            + '</tr>')
    body = ''
    for r in rows:
        body += (
            f'<tr><td style="padding:3px 14px 3px 0;">{escape(str(r.get("ref") or ""))}</td>'
            f'<td style="padding:3px 14px 3px 0;">{escape(str(r.get("payee") or ""))}</td>'
            f'<td style="padding:3px 14px 3px 0;">{_money(r.get("total") or 0)}</td>'
            f'<td style="padding:3px 14px 3px 0;">{escape(str(r.get("omni_status") or ""))}</td>'
            f'<td style="padding:3px 14px 3px 0;">{len(r.get("instructions") or [])}</td>'
            f'<td style="padding:3px 14px 3px 0;">'
            f'{escape(", ".join(r.get("bank_reasons") or []) or "-")}</td>'
            f'<td style="padding:3px 14px 3px 0;">'
            f'{"yes" if r.get("evidence_recorded") else "none"}</td></tr>')
    return f'{head}{body}</table>'


def build_html(res: dict) -> str:
    base = (getattr(settings, 'OMNI_PUBLIC_BASE_URL', '')
            or 'https://omni.alphadirect.co.bw').rstrip('/')

    if res['clean']:
        head = (f'<p style="color:#166534;font-weight:bold;">'
                f'Omni and the bank agree on every payment that has a batch.</p>')
    else:
        head = (f'<p style="color:{_RED};font-weight:bold;font-size:15px;">'
                f'{res["contradiction_count"]} payment(s) where Omni and the bank '
                f'do not agree.</p>')

    parts = []
    for key, rows in res['contradictions'].items():
        if not rows:
            continue
        headline, meaning = FINDINGS[key]
        parts.append(
            f'<h3 style="color:{_NAVY};font-size:14px;margin:16px 0 2px;">'
            f'{escape(headline)} — {len(rows)}</h3>'
            f'<p style="color:{_GREY};font-size:12px;margin:0 0 4px;">{escape(meaning)}</p>'
            + _table(rows))

    # The window the whole email covers. CFO, 21-Sep-2026: *"this email should
    # not talk about things 7 days old, going forward"* — strictly, so nothing
    # older is counted, listed or mentioned here. The exception cockpit screen
    # still carries everything; it is the email that is quiet, not Omni.
    cap = res.get('max_age_days')
    window = (f' · last {cap} days only' if cap else '')

    # With a window on, "over N days" can only ever describe the sliver between
    # the stale threshold and the window edge, under a heading that reads as if
    # it meant the whole backlog. A column that invites the wrong reading is
    # worse than no column.
    bat = ''
    for status, g in res['batches']['groups'].items():
        bat += (f'<tr><td style="padding:3px 14px 3px 0;">{escape(status)}</td>'
                f'<td style="padding:3px 14px 3px 0;">{g["count"]}</td>'
                f'<td style="padding:3px 14px 3px 0;">{_money(g["total"])}</td>'
                + ('' if cap else
                   f'<td style="padding:3px 14px 3px 0;">{g["over_threshold"]}</td>')
                + '</tr>')
    over_head = ('' if cap else
                 f'<th style="text-align:left;padding:4px 14px 4px 0;color:{_GREY};'
                 f'font-size:12px;">Over {res["batches"]["stale_days"]} days</th>')

    # The CFO's read-only reconciliation (his FNB brief, control 8). Its own
    # section, and its own send trigger below: without both, a day with zero
    # contradictions and sixteen rejected-but-closed payments sends NO email at
    # all and the report only exists for somebody who happens to open the
    # screen. A report nobody is sent is a report nobody reads.
    rej = res.get('rejected_not_open') or {}
    rej_html = ''
    if rej.get('count'):
        rej_html = (
            f'<h3 style="color:{_RED};font-size:14px;margin:18px 0 2px;">'
            f'Rejected or unconfirmed at the bank, but closed in Omni — '
            f'{rej["count"]} payment(s), {_money(rej.get("total") or 0)}</h3>'
            f'<p style="color:{_GREY};font-size:12px;margin:0 0 4px;">'
            f'{escape(rej.get("note") or "")}</p>'
            + _rej_table(rej.get('rows') or []))

    notif = res.get('notifications') or {}
    notif_html = ''
    if notif.get('stuck') or notif.get('failed'):
        notif_html = (
            f'<h3 style="color:{_RED};font-size:14px;margin:18px 0 2px;">'
            f'Bank notifications needing a look — '
            f'{notif.get("stuck", 0)} stuck, {notif.get("failed", 0)} could not be read</h3>'
            f'<p style="color:{_GREY};font-size:12px;margin:0 0 4px;">'
            f'These are FNB account postings that did not move on their own. '
            f'Open the FNB Webhook Events screen in Omni admin and check them.</p>')

    return f"""
<div style="font-family:Arial,Helvetica,sans-serif;color:{_NAVY};">
  <h2 style="font-size:16px;margin:0 0 4px;">Payments — daily three-way check</h2>
  <p style="color:{_GREY};font-size:12px;margin:0 0 12px;">{escape(res['checked_at'])} ·
     Omni record vs FNB batch{window}. Nothing here has been changed.</p>
  {head}
  {''.join(parts)}
  <h3 style="color:{_NAVY};font-size:14px;margin:18px 0 2px;">
    Batches the bank has not settled{window}</h3>
  <table style="border-collapse:collapse;font-size:13px;margin:6px 0 14px;">
    <tr>
      <th style="text-align:left;padding:4px 14px 4px 0;color:{_GREY};font-size:12px;">Bank status</th>
      <th style="text-align:left;padding:4px 14px 4px 0;color:{_GREY};font-size:12px;">Batches</th>
      <th style="text-align:left;padding:4px 14px 4px 0;color:{_GREY};font-size:12px;">Amount</th>
      {over_head}
    </tr>
    {bat}
  </table>
  {rej_html}
  {notif_html}
  <p style="font-size:13px;">
    Open the payments queue: <a href="{base}/payment-requests">{base}/payment-requests</a><br>
    The full exception screen:
    <a href="{base}/payments/exceptions">{base}/payments/exceptions</a>
  </p>
  <p style="color:{_GREY};font-size:11px;margin-top:16px;">
    This check only reads. It never changes a payment, and Omni never moves money —
    money leaves at FNB, authorised by a person with two-factor.
  </p>
</div>"""


def recipients() -> list:
    """Who this report goes to. ONE definition, so the screen that shows the
    same findings can gate on exactly this list and the two can never drift
    apart about who the report is for."""
    return list(getattr(settings, 'FNB_THREE_WAY_RECIPIENTS', _DEFAULT_RECIPIENTS))


def send_report(res: dict, *, only_when_dirty: bool = True) -> dict:
    """Send it. By default a clean day sends nothing.

    A daily all-clear email trains people to ignore the one that matters — but
    silence also looks identical to a job that died, which is exactly how the
    failed-debit report sent nothing for a month and nobody noticed. The
    compromise: silent when clean, and the command still prints its verdict every
    run so the log proves it ran.

    'clean' and 'notifications_clean' are checked SEPARATELY (not folded into
    one flag) — see three_way_check.run()'s docstring on why: the headline
    text below only ever describes payment/bank contradictions, so a day
    that is payment-clean but has a flagged bank notification must still not
    stay silent.
    """
    # Control 8 is its OWN send trigger, deliberately not folded into `clean`.
    # `clean` means "no contradictions" and three renderers already print it
    # that way; widening it would make them lie. But a day with 16 closed
    # payments the bank rejected is not a quiet day, so it must break the
    # silence on its own.
    _rej_count = (res.get('rejected_not_open') or {}).get('count', 0)
    if (only_when_dirty and res['clean']
            and res.get('notifications_clean', True) and not _rej_count):
        return {'sent': 0, 'reason': 'nothing disagrees today'}

    to = recipients()
    if not to:
        return {'sent': 0, 'reason': 'no recipients configured'}

    from core.notifications import send_html_with_cfo_cc
    n = send_html_with_cfo_cc(
        subject=(f'Payments: {res["contradiction_count"]} disagreement(s) between '
                 f'Omni and the bank'),
        html=build_html(res),
        to=to,
        text_fallback=(f'{res["contradiction_count"]} payments where Omni and the FNB '
                       f'batch disagree. Open {getattr(settings, "OMNI_PUBLIC_BASE_URL", "https://omni.alphadirect.co.bw")}/payment-requests'),
    )
    return {'sent': n, 'to': to}
