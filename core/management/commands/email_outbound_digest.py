"""Daily "what omni emailed" digest for the CFO.

CFO instruction 2026-07-25, option A. He asked to be sent whatever the ~18
guard-bypassing senders send. Copying him on all of it would be ~3,500 emails a
month (72 payslips + ~120 daily briefs x 30) and he would stop reading it inside
a week — so instead every send is recorded by
core.email_backends.GuardedEmailBackend and summarised here once a day.

Two things this must always surface:
  * anything BLOCKED — a barred shared mailbox was on a message. That is the
    signal that some record still points at admin@ and needs fixing.
  * anything FAILED — mail that did not go out at all.

Grouped by subject so 120 identical daily briefs are one line, not 120.

Run:  python manage.py email_outbound_digest [--hours 24] [--dry-run]
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from html import escape

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import OutboundEmailLog

NAVY = '#0D1B2A'
ORANGE = '#F4A623'
RED = '#C0392B'


def _recipient_count(row) -> int:
    n = 0
    for field in (row.to_addrs, row.cc_addrs, row.bcc_addrs):
        if field:
            n += len([p for p in field.split(',') if p.strip()])
    return n


def build_digest(hours: int = 24):
    """Build the outbound-email digest without sending it.

    Reads OutboundEmailLog for the look-back window and returns
    ``(subject, html, text, counts)`` where ``counts`` is a dict of
    ``sent / blocked / stripped / failed`` totals. Pure (no email side-effect)
    so both this command's handle() and the consolidated send_ops_digest
    command can reuse the exact same HTML.
    """
    since = timezone.now() - timedelta(hours=hours)
    rows = list(OutboundEmailLog.objects.filter(created_at__gte=since))

    sent = [r for r in rows if r.status == OutboundEmailLog.Status.SENT]
    blocked = [r for r in rows if r.status == OutboundEmailLog.Status.BLOCKED]
    failed = [r for r in rows if r.status == OutboundEmailLog.Status.FAILED]
    # A message can be delivered AND have had a barred address stripped off
    # it — that still needs the CFO's attention.
    stripped = [r for r in sent if r.blocked_addrs]

    groups = defaultdict(lambda: {'n': 0, 'recips': 0, 'attach': 0})
    for r in sent:
        g = groups[r.subject or '(no subject)']
        g['n'] += 1
        g['recips'] += _recipient_count(r)
        g['attach'] += r.attachment_count
    ordered = sorted(groups.items(), key=lambda kv: -kv[1]['n'])

    window = f'last {hours} hour(s)'
    subject = (f'Omni outbound email digest — {len(sent)} sent, '
               f'{len(blocked) + len(stripped)} blocked, {len(failed)} failed')

    parts = [
        f"<div style=\"font-family:'Book Antiqua',Georgia,serif;color:{NAVY};"
        f'font-size:14px;line-height:1.5;">',
        '<p>Prathap</p>',
        f'<p>Everything Omni emailed in the {window}.</p>',
        f'<p><b>{len(sent)}</b> sent &middot; '
        f'<b style="color:{RED}">{len(blocked)}</b> suppressed entirely &middot; '
        f'<b style="color:{RED}">{len(stripped)}</b> had a barred address removed '
        f'&middot; <b>{len(failed)}</b> failed</p>',
    ]

    if blocked or stripped or failed:
        parts.append(f'<h3 style="color:{RED};border-bottom:2px solid {RED};'
                     'padding-bottom:3px;">Needs your attention</h3>')
        parts.append('<table style="width:100%;border-collapse:collapse;'
                     'font-size:13px;"><tr style="background:#FDECEA;">'
                     '<th align="left" style="padding:5px;">When</th>'
                     '<th align="left" style="padding:5px;">What</th>'
                     '<th align="left" style="padding:5px;">Outcome</th></tr>')
        for r in (blocked + stripped + failed)[:60]:
            if r.status == OutboundEmailLog.Status.BLOCKED:
                note = (f'NOT SENT — every recipient barred '
                        f'({escape(r.blocked_addrs)})')
            elif r.status == OutboundEmailLog.Status.FAILED:
                note = 'send failed'
            else:
                note = f'sent, but stripped {escape(r.blocked_addrs)}'
            parts.append(
                f'<tr><td style="padding:5px;border-bottom:1px solid #eee;">'
                f'{r.created_at:%d %b %H:%M}</td>'
                f'<td style="padding:5px;border-bottom:1px solid #eee;">'
                f'{escape(r.subject[:70])}</td>'
                f'<td style="padding:5px;border-bottom:1px solid #eee;">'
                f'{note}</td></tr>')
        parts.append('</table>')
        parts.append('<p style="background:#FFF8E7;padding:8px;border-left:'
                     f'3px solid {ORANGE};">A barred address appearing here '
                     'means some record still points at a shared mailbox. '
                     'Worth fixing at the source.</p>')
    else:
        parts.append('<p>Nothing blocked and nothing failed.</p>')

    parts.append(f'<h3 style="color:{NAVY};border-bottom:2px solid {ORANGE};'
                 'padding-bottom:3px;">What went out</h3>')
    if ordered:
        parts.append('<table style="width:100%;border-collapse:collapse;'
                     f'font-size:13px;"><tr style="background:{NAVY};color:#fff;">'
                     '<th align="left" style="padding:5px;">Email</th>'
                     '<th align="right" style="padding:5px;">Count</th>'
                     '<th align="right" style="padding:5px;">Recipients</th>'
                     '<th align="right" style="padding:5px;">Attachments</th></tr>')
        for subj, g in ordered[:40]:
            parts.append(
                f'<tr><td style="padding:5px;border-bottom:1px solid #eee;">'
                f'{escape(subj[:80])}</td>'
                f'<td align="right" style="padding:5px;border-bottom:1px solid #eee;">'
                f'{g["n"]}</td>'
                f'<td align="right" style="padding:5px;border-bottom:1px solid #eee;">'
                f'{g["recips"]}</td>'
                f'<td align="right" style="padding:5px;border-bottom:1px solid #eee;">'
                f'{g["attach"]}</td></tr>')
        parts.append('</table>')
        if len(ordered) > 40:
            parts.append(f'<p>&hellip; and {len(ordered) - 40} more kinds of '
                         'email not listed.</p>')
    else:
        parts.append('<p>No email was sent in this window.</p>')

    parts.append('<p style="color:#6B7280;font-size:11px;border-top:1px solid '
                 '#eee;padding-top:6px;">Message contents are deliberately not '
                 'recorded — only subject, recipients and counts. Storing bodies '
                 'would put payslip figures and live sign-in codes in the '
                 'database.</p>')
    parts.append('</div>')
    html = '\n'.join(parts)

    text = (f'Omni outbound email, {window}: {len(sent)} sent, '
            f'{len(blocked)} suppressed, {len(stripped)} stripped, '
            f'{len(failed)} failed. See the HTML version.')

    counts = {'sent': len(sent), 'blocked': len(blocked),
              'stripped': len(stripped), 'failed': len(failed)}
    return subject, html, text, counts


class Command(BaseCommand):
    help = 'Email the CFO a one-page digest of every email omni sent.'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=24,
                            help='Look-back window in hours (default 24).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the digest instead of sending it.')
        parser.add_argument('--to', nargs='*', default=None,
                            help='Override recipients.')

    def handle(self, *args, **opts):
        # Consolidated ops email (CFO 2026-08-05): when ON, send_ops_digest
        # bundles this digest into the single 06:30 "ops & system" email, so
        # this standalone sender self-skips. Flag OFF ⇒ unchanged. A --dry-run
        # still computes/prints so the digest stays testable by hand.
        if (getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False)
                and not opts['dry_run']):
            self.stdout.write('CONSOLIDATED_EMAILS_ENABLED on — send_ops_digest '
                              'covers the outbound digest; skipping own send.')
            return

        hours = opts['hours']
        subject, html, text, counts = build_digest(hours)

        recipients = opts['to'] or list(
            getattr(settings, 'OUTBOUND_DIGEST_TO', None)
            or ['pganesharajah@alphadirect.co.bw'])

        if opts['dry_run']:
            self.stdout.write(f'DRY-RUN -> {recipients}: {subject}')
            self.stdout.write(text)
            return

        # Sent through the guarded backend like everything else, so the digest
        # is itself subject to the same rules.
        from django.core.mail import EmailMultiAlternatives
        frm = (getattr(settings, 'OMNI_FROM_EMAIL', '')
               or getattr(settings, 'DEFAULT_FROM_EMAIL', None))
        msg = EmailMultiAlternatives(subject=subject, body=text,
                                     from_email=frm, to=recipients)
        msg.attach_alternative(html, 'text/html')
        msg.send()
        self.stdout.write(self.style.SUCCESS(
            f'Digest emailed to {recipients}: {counts["sent"]} sent, '
            f'{counts["blocked"]} suppressed, {counts["stripped"]} stripped, '
            f'{counts["failed"]} failed.'))
