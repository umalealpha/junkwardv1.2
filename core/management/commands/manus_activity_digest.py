"""Daily summary of everything the automated reviewer changed.

WHY (CFO 2026-08-09): Manus was given manager-level access so it can click
through Omni and find what is broken. The CFO asked to be told what it does —
once a day, not on every click, so an unexpected change is obvious the next
morning without a session of testing filling his inbox.

    python manage.py manus_activity_digest              # yesterday
    python manage.py manus_activity_digest --date 2026-08-09
    python manage.py manus_activity_digest --dry-run    # print, send nothing

Deliberately says SOMETHING EVERY DAY, including "nothing changed". A digest
that only arrives when there is news is indistinguishable from a digest that has
silently stopped running — which is how the 07:00 manager report died six times
before anyone noticed.
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

MANUS_USERNAME = 'manus'


def _rows(day):
    from core.models import AuditLog
    start = timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))
    end = start + datetime.timedelta(days=1)
    return list(AuditLog.objects
                .select_related('user')
                .filter(user__username=MANUS_USERNAME,
                        created_at__gte=start, created_at__lt=end)
                .order_by('created_at'))


def _changed_fields(entry):
    """[(field, before, after)] — the VALUES, not just the field names.

    "rating changed" tells the reader nothing they can act on;
    "rating: Meets -> Exceeds" is the whole point of watching an automated
    account. Values are truncated so one long note cannot swamp the email.
    """
    before = entry.old_values or {}
    after = entry.new_values or {}
    keys = set(before) | set(after)
    out = []
    for k in sorted(keys):
        b, a = before.get(k), after.get(k)
        if b != a:
            out.append((k, _short(b), _short(a)))
    return out


def _short(v, limit=60):
    if v is None:
        return '—'
    s = str(v)
    return s if len(s) <= limit else s[:limit] + '…' 


def _html(day, rows, base_url):
    from django.utils.html import escape

    head = (
        '<div style="margin:0;background:#F3F4F6;font-family:\'Book Antiqua\','
        'Palatino,Georgia,serif;color:#1F2937;padding:20px 12px;">'
        '<div style="max-width:660px;margin:0 auto;background:#fff;border-radius:14px;'
        'overflow:hidden;box-shadow:0 6px 24px rgba(13,27,42,.08);">'
        '<div style="background:#0D1B2A;padding:20px 26px;">'
        '<div style="color:#F4A623;font-size:18px;font-weight:700;">'
        f'Manus activity &mdash; {day.strftime("%A %d %B %Y")}</div></div>'
        '<div style="padding:22px 26px;font-size:15px;line-height:1.6;">'
    )

    if not rows:
        body = (
            '<p style="margin:0 0 14px;">The automated reviewer changed <b>nothing</b> '
            'yesterday.</p>'
            '<p style="margin:0 0 14px;color:#6B7280;font-size:14px;">Looking at screens '
            'is not recorded &mdash; only edits are. So this may simply mean it was '
            'reviewing, or that it did not run.</p>'
        )
    else:
        by_table = {}
        for r in rows:
            by_table.setdefault(r.table_name, []).append(r)
        body = (
            f'<p style="margin:0 0 16px;">The automated reviewer made '
            f'<b>{len(rows)}</b> change{"" if len(rows) == 1 else "s"} across '
            f'<b>{len(by_table)}</b> area{"" if len(by_table) == 1 else "s"}.</p>'
        )
        body += '<table style="width:100%;border-collapse:collapse;font-size:14px;margin:0 0 18px;">'
        body += ('<tr style="background:#F9FAFB;">'
                 '<td style="padding:7px 10px;border:1px solid #EEF0F3;font-weight:600;">Time</td>'
                 '<td style="padding:7px 10px;border:1px solid #EEF0F3;font-weight:600;">What</td>'
                 '<td style="padding:7px 10px;border:1px solid #EEF0F3;font-weight:600;">Changed</td></tr>')
        for r in rows[:60]:
            fields = _changed_fields(r)
            shown = '<br>'.join(
                f'<b>{escape(f)}</b>: {escape(b)} &rarr; '
                f'<span style="color:#059669;">{escape(a)}</span>'
                for f, b, a in fields[:5])
            if len(fields) > 5:
                shown += f'<br><span style="color:#6B7280;">+{len(fields) - 5} more</span>' 
            body += (
                '<tr>'
                f'<td style="padding:7px 10px;border:1px solid #EEF0F3;white-space:nowrap;">'
                f'{timezone.localtime(r.created_at).strftime("%H:%M")}</td>'
                f'<td style="padding:7px 10px;border:1px solid #EEF0F3;">'
                f'<b>{escape(r.action)}</b> {escape(r.table_name)}'
                f'<br><span style="color:#6B7280;font-size:12px;">'
                f'{escape((r.description or r.record_id or "")[:90])}</span></td>'
                f'<td style="padding:7px 10px;border:1px solid #EEF0F3;color:#6B7280;">'
                f'{shown or "&mdash;"}</td></tr>')
        body += '</table>'
        if len(rows) > 60:
            body += (f'<p style="margin:0 0 14px;color:#6B7280;font-size:13px;">'
                     f'Showing the first 60 of {len(rows)}. The rest are on the screen below.</p>')

    body += (
        f'<p style="margin:0 0 18px;text-align:center;">'
        f'<a href="{base_url}/manus-activity" style="display:inline-block;background:#0D1B2A;'
        f'color:#F4A623;text-decoration:none;font-weight:700;font-size:14px;padding:11px 22px;'
        f'border-radius:9px;">Open the full activity screen &rarr;</a></p>'
        '<p style="margin:0 0 4px;color:#6B7280;font-size:12px;">Manus is an automated '
        'reviewer with manager-level access. It cannot open the HR file or leave '
        'administration.</p>'
    )
    tail = ('<p style="margin:18px 0 0;">Regards,<br><b>Omni</b><br>'
            '<span style="color:#6B7280;">Alpha Direct</span></p></div></div></div>')
    return head + body + tail


class Command(BaseCommand):
    help = 'Email a daily summary of everything the Manus reviewer account changed.'

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date', help='YYYY-MM-DD (default: yesterday)')
        parser.add_argument('--to', dest='to', help='Override the recipient')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from django.conf import settings

        if opts.get('date'):
            day = datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
        else:
            day = timezone.localtime().date() - datetime.timedelta(days=1)

        rows = _rows(day)
        base = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
        html = _html(day, rows, base)
        subject = (f'Manus activity — {len(rows)} change{"" if len(rows) == 1 else "s"} '
                   f'on {day.strftime("%d %b")}')
        to = opts.get('to') or getattr(settings, 'MANUS_DIGEST_TO', '') or 'cfo@alphadirect.co.bw'

        if opts.get('dry_run'):
            self.stdout.write(f'DRY RUN — would send "{subject}" to {to} ({len(rows)} rows)')
            return

        # CFO 2026-08-12: when the consolidated-emails flag is on, this Manus
        # summary rides inside the 06:30 ops digest instead of a standalone email.
        if getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False):
            self.stdout.write('CONSOLIDATED_EMAILS_ENABLED on — folded into the ops '
                              'digest; standalone Manus email skipped.')
            return

        # Sent direct, like the other named-recipient reports: core.notifications
        # strips explicitly-named C-suite addresses via its _NEVER_CC blocklist.
        from django.core.mail import EmailMultiAlternatives
        msg = EmailMultiAlternatives(
            subject=subject,
            body=f'Manus made {len(rows)} change(s) on {day}. Open {base}/manus-activity',
            to=[a.strip() for a in to.split(',') if a.strip()],
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        self.stdout.write(self.style.SUCCESS(f'sent to {to}: {len(rows)} change(s) on {day}'))
