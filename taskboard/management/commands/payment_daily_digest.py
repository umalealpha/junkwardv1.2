"""One email a day: every payment request, summarised.

CFO 2026-08-09: *"every day I need a cron somewhere around nine thirty AM,
summarising all the payment requests. everything... and email to me one email...
I have more than seven accountants requesting for payments, and it is too much
overwhelming for me to go and check everything."*

    python manage.py payment_daily_digest
    python manage.py payment_daily_digest --dry-run     # print, send nothing
    python manage.py payment_daily_digest --to me@x.com

Sends EVERY day, including "nothing is waiting on you". A digest that only
arrives when there is news cannot be told apart from one that has silently
stopped — which is how the 07:00 manager report died six times unnoticed.

ONE-TAP DECISIONS (CFO 2026-09-13)
The CFO's one-tap Approve / Decline / Need-detail links used to ride on the
morning brief (infra/ceo-monitor/cfo_driver.py). He then asked for that brief to
be about PEOPLE — leave, staff loans, incentives, commissions — so it now filters
every payment out, and the tested one-tap gate was left with nothing to send it.
The links live here instead: this is the email that is already about payments.

The gate, the tokens and the click handler are unchanged and still live in
core.ceo_monitor_views. Nothing here goes round complete_task, so PAY-DUP-01,
the held-line block, the CFO check, confirm_total and approver-is-not-creator all
still apply at the moment of the tap. Approving records an authorisation in Omni;
Omni never moves money — that happens at FNB, loaded and released by two people.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils.html import escape


def _money(cur, v):
    return f'{escape(cur)} {v:,.2f}'


_BTN = {
    'Approve': ('#0D1B2A', '#F4A623'),
    'Decline': ('#B91C1C', '#FFFFFF'),
    'Need detail': ('#475467', '#FFFFFF'),
}


def _decision_cell(links):
    """The one-tap buttons for a single row, or a plain dash."""
    if not links:
        return ('<td style="padding:6px 9px;border:1px solid #EEF0F3;color:#9CA3AF;">'
                'In Omni</td>')
    out = '<td style="padding:6px 9px;border:1px solid #EEF0F3;white-space:nowrap;">'
    for label, url in links:
        bg, fg = _BTN.get(label, ('#475467', '#FFFFFF'))
        out += (f'<a href="{escape(url)}" style="display:inline-block;background:{bg};'
                f'color:{fg};text-decoration:none;font-family:Arial,Helvetica,sans-serif;'
                f'font-size:12px;font-weight:700;padding:6px 11px;border-radius:5px;'
                f'margin:2px 4px 2px 0;">{escape(label)}</a>')
    return out + '</td>'


def _rows_table(rows, *, show_age=True, limit=40, decisions=None):
    """`decisions` maps a row reference to its one-tap links. Absent (the normal
    case, and every table other than "waiting on you") means no buttons at all."""
    if not rows:
        return '<p style="margin:0 0 14px;color:#6B7280;font-size:14px;">None.</p>'
    head = ('<tr style="background:#F9FAFB;">'
            '<td style="padding:6px 9px;border:1px solid #EEF0F3;font-weight:600;">Reference</td>'
            '<td style="padding:6px 9px;border:1px solid #EEF0F3;font-weight:600;">Payee</td>'
            '<td style="padding:6px 9px;border:1px solid #EEF0F3;font-weight:600;">Amount</td>'
            '<td style="padding:6px 9px;border:1px solid #EEF0F3;font-weight:600;">Loaded by</td>'
            + ('<td style="padding:6px 9px;border:1px solid #EEF0F3;font-weight:600;">Days</td>'
               if show_age else '')
            + ('<td style="padding:6px 9px;border:1px solid #EEF0F3;font-weight:600;">Decision</td>'
               if decisions is not None else '') + '</tr>')
    body = ''
    for r in rows[:limit]:
        age = (f'<td style="padding:6px 9px;border:1px solid #EEF0F3;'
               f'{"color:#B91C1C;font-weight:700;" if r["age_days"] >= 3 else ""}">'
               f'{r["age_days"]}</td>') if show_age else ''
        dec = (_decision_cell((decisions or {}).get(r['ref']))
               if decisions is not None else '')
        body += (f'<tr><td style="padding:6px 9px;border:1px solid #EEF0F3;">{escape(r["ref"])}'
                 f'<br><span style="color:#6B7280;font-size:12px;">{escape(r["entity"])}</span></td>'
                 f'<td style="padding:6px 9px;border:1px solid #EEF0F3;">{escape(str(r["payee"])[:44])}</td>'
                 f'<td style="padding:6px 9px;border:1px solid #EEF0F3;white-space:nowrap;">'
                 f'{_money(r["currency"], r["total"])}</td>'
                 f'<td style="padding:6px 9px;border:1px solid #EEF0F3;">{escape(str(r["loader"])[:26])}</td>'
                 f'{age}{dec}</tr>')
    more = ''
    if len(rows) > limit:
        more = (f'<p style="margin:6px 0 14px;color:#6B7280;font-size:13px;">'
                f'Showing {limit} of {len(rows)} — the rest are on the screen.</p>')
    return ('<table style="width:100%;border-collapse:collapse;font-size:13.5px;'
            f'margin:0 0 10px;">{head}{body}</table>{more}')


#: The only domain a personal one-tap link may be delivered to - the same one
#: the click resolves the actor against in core.ceo_monitor_views._resolve.
ACTOR_DOMAIN = 'alphadirect.co.bw'


def _bare(addr: str) -> str:
    """'Prathap <p@x.co.bw>' -> 'p@x.co.bw'."""
    a = (addr or '').strip()
    if '<' in a and '>' in a:
        a = a[a.rfind('<') + 1:a.rfind('>')]
    return a.strip().lower()


def approve_actor_for(recipients):
    """The one person this email's one-tap links may be signed for, or None.

    A one-tap link is signed FOR ONE PERSON, and anyone holding it can decide as
    that person. So the links go out ONLY when this email has exactly one
    recipient and that recipient is somebody Omni already allows to decide a
    payment by email (core.ceo_monitor_views.MAY_DECIDE_MONEY_BY_EMAIL).

    Two addresses in --to, or PAYMENT_DIGEST_TO pointing at a shared mailbox,
    and the digest still goes out - with no personal buttons in it. The digest
    also never cc's anybody (see Command.handle): a personal link is only as
    personal as its delivery.

    The address must be the CORPORATE one, in full. Matching only the part
    before the @ would have posted a link that acts as the CFO to
    pganesharajah@ anywhere - a personal address, a forward, someone else's
    domain. The click resolves the actor to <handle>@alphadirect.co.bw
    (core.ceo_monitor_views._resolve), so that is the only mailbox the link may
    be delivered to.
    """
    if len(recipients) != 1:
        return None
    try:
        from core.ceo_monitor_views import MAY_DECIDE_MONEY_BY_EMAIL
    except ImportError:                                  # older backend image
        return None
    addr = _bare(recipients[0])
    handle, _, domain = addr.rpartition('@')
    if domain != ACTOR_DOMAIN:
        return None
    return handle if handle in MAY_DECIDE_MONEY_BY_EMAIL else None


def decision_links(rows, actor, base_url):
    """{reference: [(label, url), ...]} for the rows `actor` may decide by email.

    Renders with is_decidable_task(task, actor=actor) and signs the token with
    the SAME actor - which is the whole point. On 2026-09-13 the morning brief
    rendered with the actor and the click re-checked without one, fell back to
    the CEO handle, and refused the CFO on his own payment. It failed closed, so
    nothing unsafe happened; the feature was simply dead. Render and re-check
    with one actor, and prove it through the HTTP view, not the predicate.
    """
    if not actor:
        return {}
    try:
        from core.ceo_monitor_views import (DECISION_LABELS, is_decidable_task,
                                            make_decision_token)
        from core.models import OmniTask
    except ImportError:            # older image: lose the buttons, keep the digest
        return {}
    ids = [r['task_id'] for r in rows if r.get('task_id')]
    if not ids:
        return {}
    tasks = {t.pk: t for t in OmniTask.objects.filter(pk__in=ids)}
    out = {}
    for r in rows:
        task = tasks.get(r.get('task_id'))
        if task is None or not is_decidable_task(task, actor=actor):
            continue
        out[r['ref']] = [
            (label, f'{base_url}/api/ceo-monitor/decide/?t='
                    + make_decision_token(task_id=task.pk, action=act, actor=actor))
            for act, label in DECISION_LABELS]
    return out


def build_html(d, narrative_text, source, base_url, decisions=None):
    cur_lines = ' · '.join(f'{k} {v:,.2f}' for k, v in sorted(d['by_currency'].items())) or '—'
    uncounter = [o for o in d['overrides'] if not o['countersigned']]

    # Internal email, so it carries the red do-not-reply banner (CFO
    # 2026-08-07): a reply here is not tracked and nobody is assigned to it.
    try:
        from core.notifications import no_reply_banner
        banner = no_reply_banner()
    except Exception:                                    # noqa: BLE001
        banner = ''

    h = (
        banner +
        '<div style="margin:0;background:#F3F4F6;font-family:\'Book Antiqua\','
        'Palatino,Georgia,serif;color:#1F2937;padding:20px 12px;">'
        '<div style="max-width:720px;margin:0 auto;background:#fff;border-radius:14px;'
        'overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">'
        '<div style="background:#0D1B2A;padding:20px 26px;">'
        '<div style="font-size:12px;color:#FFD98A;letter-spacing:.06em;text-transform:uppercase">'
        'Alpha Direct &middot; Payments</div>'
        '<div style="color:#F4A623;font-size:19px;font-weight:700;margin-top:3px;">'
        f'{d["open_count"]} payment request'
        f'{"" if d["open_count"] == 1 else "s"} open</div>'
        f'<div style="font-size:13px;color:#C7D2E0;margin-top:3px;">'
        f'{d["generated_at"]:%A %d %B %Y, %H:%M}</div></div>'
        '<div style="padding:22px 26px;font-size:15px;line-height:1.6;">'
    )

    # The AI's read of the day, first — this is what he asked for.
    h += ('<div style="background:#F8FAFC;border-left:4px solid #0D1B2A;border-radius:6px;'
          'padding:14px 16px;margin:0 0 18px;">'
          f'{escape(narrative_text)}'
          f'<div style="margin-top:8px;font-size:11px;color:#9CA3AF;">'
          f'Written by {escape(source)} from the figures below. '
          f'Every number here is calculated by Omni, never by the AI.</div></div>')

    # 🔴 Uncountersigned duplicate overrides — the control that failed on 7 Aug.
    if uncounter:
        h += ('<div style="background:#FEF2F2;border-left:4px solid #B91C1C;border-radius:6px;'
              'padding:14px 16px;margin:0 0 18px;">'
              f'<b style="color:#B91C1C;">{len(uncounter)} duplicate override(s) with no '
              f'second signature</b><br><span style="color:#7F1D1D;">')
        for o in uncounter[:6]:
            h += (f'{escape(o["ref"])} — {_money(o["currency"], o["total"])} to '
                  f'{escape(str(o["payee"])[:40])}, loaded by {escape(str(o["loader"])[:26])}<br>')
        h += '</span></div>'

    h += (f'<p style="margin:0 0 6px;"><b>Waiting on you — {len(d["waiting_cfo"])}, '
          f'{d["total_waiting_cfo"]:,.2f}</b></p>')
    if decisions is not None:
        h += ('<p style="margin:0 0 8px;font-size:13px;color:#475467;">'
              'You can decide these from here. Tapping a button opens a confirm '
              'page, and your decision is then recorded in Omni against your name '
              'with every check Omni already applies &mdash; the duplicate '
              'control, any held line, and the rule that the approver cannot be '
              'the person who raised it. Omni records the authorisation only. '
              'Nothing is paid from this email: the payment is still loaded and '
              'released at the bank by two people. A row marked &ldquo;In Omni&rdquo; '
              'needs the screen. These buttons are yours alone, so please do not '
              'forward this email.</p>')
    h += _rows_table(d['waiting_cfo'], decisions=decisions)

    h += (f'<p style="margin:14px 0 6px;"><b>Still with finance — {len(d["waiting_finance"])}, '
          f'{d["total_waiting_finance"]:,.2f}</b></p>')
    h += _rows_table(d['waiting_finance'])

    # With the exception committee (a changed bank account). Not blocked —
    # waiting on three sign-offs. Was invisible here (Fable 5.1 audit, M2).
    if d.get('waiting_committee'):
        h += (f'<p style="margin:14px 0 6px;"><b>With the exception committee — '
              f'{len(d["waiting_committee"])}, {d["total_waiting_committee"]:,.2f}</b></p>')
        h += _rows_table(d['waiting_committee'])

    # Who is asking, so seven accountants become one line each.
    if d['by_loader']:
        h += '<p style="margin:14px 0 6px;"><b>Who has requests open</b></p>'
        h += '<table style="width:100%;border-collapse:collapse;font-size:13.5px;margin:0 0 14px;">'
        for k, v in sorted(d['by_loader'].items(), key=lambda kv: -kv[1]['total']):
            h += (f'<tr><td style="padding:5px 9px;border:1px solid #EEF0F3;">{escape(k)}</td>'
                  f'<td style="padding:5px 9px;border:1px solid #EEF0F3;">{v["count"]}</td>'
                  f'<td style="padding:5px 9px;border:1px solid #EEF0F3;white-space:nowrap;">'
                  f'{v["total"]:,.2f}</td></tr>')
        h += '</table>'

    if d['by_entity']:
        h += '<p style="margin:14px 0 6px;"><b>By company</b></p>'
        h += '<table style="width:100%;border-collapse:collapse;font-size:13.5px;margin:0 0 14px;">'
        for k, v in sorted(d['by_entity'].items(), key=lambda kv: -kv[1]['total']):
            h += (f'<tr><td style="padding:5px 9px;border:1px solid #EEF0F3;">{escape(k)}</td>'
                  f'<td style="padding:5px 9px;border:1px solid #EEF0F3;">{v["count"]}</td>'
                  f'<td style="padding:5px 9px;border:1px solid #EEF0F3;white-space:nowrap;">'
                  f'{v["total"]:,.2f}</td></tr>')
        h += '</table>'

    h += (f'<p style="margin:14px 0 4px;font-size:13.5px;color:#6B7280;">'
          f'Open by currency: {escape(cur_lines)} · '
          f'settled in the last 24 hours: {len(d["settled_24h"])} · '
          f'open with no due date: {len(d["no_due_date"])}</p>')

    h += (f'<p style="margin:18px 0 6px;text-align:center;">'
          f'<a href="{base_url}/payment-requests" style="display:inline-block;background:#0D1B2A;'
          f'color:#F4A623;text-decoration:none;font-weight:700;font-size:14px;padding:12px 24px;'
          f'border-radius:9px;">Open payment requests &rarr;</a></p>')
    h += ('<p style="margin:14px 0 0;">Regards,<br><b>Omni</b><br>'
          '<span style="color:#6B7280;">Alpha Direct</span></p></div></div></div>')
    return h


class Command(BaseCommand):
    help = "Email the CFO one daily summary of every payment request."

    def add_arguments(self, parser):
        parser.add_argument('--to', dest='to')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from django.conf import settings
        from taskboard import payment_digest

        d = payment_digest.collect()
        text, source = payment_digest.narrative(d)
        base = getattr(settings, 'PUBLIC_BASE_URL',
                       'https://omni.alphadirect.co.bw').rstrip('/')

        # Work out the recipients BEFORE the HTML, because who this is going to
        # decides whether it may carry personal one-tap links at all.
        to = opts.get('to') or getattr(settings, 'PAYMENT_DIGEST_TO', '') \
            or 'pganesharajah@alphadirect.co.bw'
        recipients = [a.strip() for a in to.split(',') if a.strip()]
        actor = approve_actor_for(recipients)
        decisions = decision_links(d['waiting_cfo'], actor, base) if actor else None

        html = build_html(d, text, source, base, decisions=decisions)

        waiting = len(d['waiting_cfo'])
        uncounter = len([o for o in d['overrides'] if not o['countersigned']])
        flag = f'🔴 {uncounter} unsigned override · ' if uncounter else ''
        subject = (f'{flag}Payments — {waiting} waiting on you '
                   f'({d["total_waiting_cfo"]:,.0f}), {d["open_count"]} open')

        if opts.get('dry_run'):
            self.stdout.write(f'DRY RUN — "{subject}" to {to}')
            self.stdout.write(f'narrative ({source}): {text}')
            self.stdout.write(f'one-tap decisions for: {actor or "nobody"} '
                              f'({len(decisions or {})} row(s))')
            return

        from django.core.mail import EmailMultiAlternatives
        # NO cc AND NO bcc, deliberately. This email can carry Approve links
        # signed for one person, and the house helper send_html_with_cfo_cc
        # would copy the SHARED excoboard@ mailbox by default - several people
        # read it, and any of them could then decide as him. Sent plainly, to
        # the recipients above and nobody else.
        msg = EmailMultiAlternatives(
            subject=subject,
            body=f'{text}\n\nOpen {base}/payment-requests',
            to=recipients,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        self.stdout.write(self.style.SUCCESS(
            f'sent to {to}: {waiting} waiting, {d["open_count"]} open, narrative by {source}'))
