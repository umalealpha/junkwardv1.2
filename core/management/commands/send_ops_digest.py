"""Consolidated daily "Ops & system health" email for the CFO/EXCO (CFO 2026-08-05).

This is Email 3 of the consolidated 06:30 layout (see the CONSOLIDATED_EMAILS_ENABLED
block in settings). It bundles into ONE email the two things previously sent as
two separate emails:

  1. The outbound-email digest — "what omni emailed" (core email_outbound_digest).
     ALWAYS shown; it is an accounting record.
  2. The Time Doctor pipeline health check (integrations timedoctor_healthcheck).
     ALL-GREEN-SILENT: shown ONLY when the result is WARN or FAIL. When Time
     Doctor is healthy (PASS) the section is omitted entirely — no news is good
     news, so the CFO's eye goes straight to anything that needs attention.

Sent once via core.notifications.send_html_with_cfo_cc to OUTBOUND_DIGEST_TO (the
CFO), which auto-CCs excoboard@ (EXCO) — matching the two audiences the two
separate emails reach today.

Behaviour is gated on CONSOLIDATED_EMAILS_ENABLED: when OFF this command
self-skips its send (the two standalone commands still send as before), so a
deploy changes nothing until the CFO flips the flag on. --dry-run / --preview-file
always compute so the layout can be checked by hand regardless of the flag.

  python manage.py send_ops_digest                       # send (flag ON only)
  python manage.py send_ops_digest --dry-run             # print plan, no send
  python manage.py send_ops_digest --preview-file out.html
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

log = logging.getLogger(__name__)

NAVY = '#0D1B2A'
ORANGE = '#F4A623'


def _manus_section():
    """Compact "what the automated reviewer changed" section (folds the standalone
    07:30 Manus email into this digest). Returns ``(html, text)``; never raises."""
    import datetime

    from django.utils.html import escape

    from core.management.commands.manus_activity_digest import _rows

    day = timezone.localtime().date() - datetime.timedelta(days=1)
    rows = _rows(day)
    if not rows:
        return (f'<p style="margin:0;color:#4B5563;">The automated reviewer (Manus) '
                f'changed nothing on {day:%d %b}.</p>',
                f'Manus: no changes on {day:%d %b}.')
    by_area: dict[str, int] = {}
    for r in rows:
        by_area[r.table_name] = by_area.get(r.table_name, 0) + 1
    trs = ''.join(
        f'<tr><td style="padding:5px 9px;border-bottom:1px solid #eef0f3;">{escape(a)}</td>'
        f'<td style="padding:5px 9px;border-bottom:1px solid #eef0f3;text-align:right;">{n}</td></tr>'
        for a, n in sorted(by_area.items(), key=lambda x: -x[1])[:12])
    html = (f'<p style="margin:0 0 8px;">The automated reviewer (Manus) made '
            f'<b>{len(rows)}</b> change(s) across <b>{len(by_area)}</b> area(s) on '
            f'{day:%d %b}.</p>'
            '<table style="border-collapse:collapse;width:100%;font-size:13px;">'
            + trs + '</table>')
    return html, f'Manus: {len(rows)} change(s) across {len(by_area)} area(s) on {day:%d %b}.'


def _selftest_section():
    """One-line nightly-self-test summary, derived from the bugs it filed overnight
    (folds the standalone 23:00 self-test email). Returns ``(html, text)``."""
    import datetime

    from core.models import BugReport
    from core.management.commands.nightly_self_test import _OPEN

    since = timezone.localtime() - datetime.timedelta(hours=12)
    new_breaks = BugReport.objects.filter(
        reporter_email='omni@alphadirect.co.bw',
        description__startswith='[AUTO - nightly self-test',
        created_at__gte=since).count()
    open_total = BugReport.objects.filter(status__in=_OPEN).count()
    base = getattr(settings, 'PUBLIC_BASE_URL',
                   'https://omni.alphadirect.co.bw').rstrip('/')
    if new_breaks:
        html = (f'<p style="margin:0;color:#B04E00;"><b>{new_breaks}</b> new backend '
                f'break(s) filed by the overnight self-test &mdash; <b>{open_total}</b> '
                f'open on the <a href="{base}/bug-reports">bug board</a>.</p>')
        text = f'Self-test: {new_breaks} new break(s) overnight, {open_total} open.'
    else:
        html = (f'<p style="margin:0;color:#0a7d34;">No new backend breaks overnight. '
                f'<b>{open_total}</b> open on the '
                f'<a href="{base}/bug-reports">bug board</a>.</p>')
        text = f'Self-test: no new breaks overnight, {open_total} open.'
    return html, text


def _dupwatch_section():
    """Duplicate-account watch section (folds the standalone midnight alert).
    Returns ``(html, text)``; silent-clean shows a one-line all-clear."""
    from django.utils.html import escape

    from core.management.commands.duplicate_account_watch import Command as _DupCmd

    findings = _DupCmd()._find_issues()
    if not findings:
        return ('<p style="margin:0;color:#0a7d34;">No duplicate-account issues.</p>',
                'Duplicate accounts: none.')
    items = ''.join(f'<li style="margin:2px 0;">{escape(f)}</li>' for f in findings[:20])
    html = (f'<p style="margin:0 0 6px;color:#B04E00;"><b>{len(findings)}</b> '
            f'duplicate-account issue(s) to action:</p>'
            f'<ul style="margin:0;padding-left:18px;font-size:13px;">{items}</ul>')
    return html, f'Duplicate accounts: {len(findings)} to action.'


def build_ops_email(hours: int = 24):
    """Compose the consolidated ops email.

    Returns ``(subject, html, text, meta)``. ``meta`` carries the outbound-digest
    counts, the Time Doctor status, and whether the Time Doctor section was
    included (it is only included when status != PASS)."""
    from core.management.commands.email_outbound_digest import build_digest
    from integrations.management.commands import timedoctor_healthcheck as tdhc

    digest_subject, digest_html, digest_text, counts = build_digest(hours)

    # The health check never raises by contract; guard anyway so a surprise can
    # never stop the accounting digest going out. A failure is treated as a
    # visible WARN section rather than silence.
    try:
        worst, as_of_txt, checks, sample_rows, td_user_count = tdhc.run_checks()
    except Exception as exc:  # noqa: BLE001
        log.warning('Ops digest: Time Doctor health check raised (treated as WARN): %s', exc)
        worst, as_of_txt, checks, sample_rows, td_user_count = (
            'WARN', timezone.localtime().strftime('%Y-%m-%d %H:%M SAST'),
            [tdhc.Check('Time Doctor health', 'WARN', f'Health check errored: {exc}')],
            [], None)

    include_td = worst != 'PASS'

    date_txt = timezone.localtime().date()
    subject = f'Omni ops & system — {date_txt}'
    if include_td:
        subject += f' — Time Doctor {worst}'

    parts = [
        "<div style=\"font-family:'Segoe UI',Arial,sans-serif;color:#1F2937;"
        'max-width:680px;margin:0 auto;\">',
        f'<div style="background:{NAVY};padding:16px 24px">'
        f'<div style="color:{ORANGE};font-size:18px;font-weight:700">Alpha Direct — omni</div>'
        '<div style="color:#AEB6C2;font-size:13px;margin-top:2px">'
        'Daily ops &amp; system health</div></div>',
        '<div style="padding:8px 24px 24px">',
        f'<h2 style="color:{NAVY};border-bottom:2px solid {ORANGE};'
        'padding-bottom:6px;font-size:16px;">Outbound email</h2>',
        digest_html,
    ]

    if include_td:
        parts.append(
            f'<h2 style="color:{NAVY};border-bottom:2px solid {ORANGE};'
            f'padding-bottom:6px;font-size:16px;">Time Doctor health — {worst}</h2>')
        parts.append(tdhc.render_section(worst, as_of_txt, checks, sample_rows))

    # Folded system-health sections (CFO 2026-08-12): Manus activity, the nightly
    # self-test result and the duplicate-account watch now ride inside this ONE
    # 06:30 email instead of three separate ones. Each is defended so a failure in
    # one section never stops the whole digest going out.
    extra_text: list[str] = []
    for title, fn in (('What the automated reviewer changed', _manus_section),
                      ('Nightly self-test', _selftest_section),
                      ('Duplicate-account watch', _dupwatch_section)):
        try:
            sec_html, sec_text = fn()
        except Exception as exc:  # noqa: BLE001
            log.warning('Ops digest: section %r raised: %s', title, exc)
            sec_html = f'<p style="color:#B04E00;">Could not build this section ({exc}).</p>'
            sec_text = f'{title}: could not build ({exc}).'
        parts.append(
            f'<h2 style="color:{NAVY};border-bottom:2px solid {ORANGE};'
            f'padding-bottom:6px;font-size:16px;">{title}</h2>')
        parts.append(sec_html)
        extra_text += ['', f'{title}:', sec_text]

    parts.append('</div></div>')
    html = '\n'.join(parts)

    text_lines = [digest_text]
    if include_td:
        text_lines += ['', tdhc.render_text(worst, as_of_txt, checks, sample_rows)]
    text_lines += extra_text
    text = '\n'.join(text_lines)

    meta = {'counts': counts, 'td_status': worst, 'td_included': include_td}
    return subject, html, text, meta


class Command(BaseCommand):
    help = ('Consolidated daily "ops & system" email (outbound digest + Time '
            'Doctor health, all-green-silent). CFO 2026-08-05.')

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=24,
                            help='Outbound-digest look-back window in hours (default 24).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Compute and print what would be sent; do not send.')
        parser.add_argument('--preview-file', dest='preview_file', default='',
                            help='Write the composed HTML to this file (no send).')
        parser.add_argument('--to', nargs='*', default=None,
                            help='Override recipients (defaults to OUTBOUND_DIGEST_TO / CFO).')

    def handle(self, *args, **opts):
        flag_on = getattr(settings, 'CONSOLIDATED_EMAILS_ENABLED', False)
        dry_run = opts['dry_run']
        preview_file = opts['preview_file']

        # Flag OFF ⇒ the two standalone commands still send; this consolidated
        # sender stays dormant so a deploy changes nothing until the CFO flips it.
        # Skip BEFORE build_ops_email (which calls the live Time Doctor API) so a
        # dormant run does no needless work. --dry-run / --preview-file still
        # compute so the layout stays testable by hand whatever the flag.
        if not flag_on and not dry_run and not preview_file:
            self.stdout.write('CONSOLIDATED_EMAILS_ENABLED is OFF — the standalone '
                              'digest + heartbeat still send; ops digest skipping.')
            return

        subject, html, text, meta = build_ops_email(opts['hours'])

        recipients = opts['to'] or list(
            getattr(settings, 'OUTBOUND_DIGEST_TO', None)
            or ['pganesharajah@alphadirect.co.bw'])

        td_note = (f'Time Doctor section INCLUDED ({meta["td_status"]})'
                   if meta['td_included']
                   else 'Time Doctor PASS — section omitted (all-green-silent)')

        if preview_file:
            with open(preview_file, 'w', encoding='utf-8') as fh:
                fh.write(html)
            self.stdout.write(f'Wrote preview HTML to {preview_file}. {td_note}.')
            return

        if dry_run:
            self.stdout.write(f'DRY-RUN -> {recipients} (CC excoboard@): {subject}')
            self.stdout.write(td_note + '.')
            self.stdout.write(text)
            return

        from core.notifications import send_html_with_cfo_cc
        send_html_with_cfo_cc(subject, html, recipients, text_fallback=text)
        self.stdout.write(self.style.SUCCESS(
            f'Ops digest emailed to {recipients} (CC excoboard@). {td_note}.'))
