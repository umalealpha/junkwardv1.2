"""devlog_digest — email the CFO what is claimed done but cannot be proven live.

WHY THIS EXISTS (CFO 2026-09-11)
--------------------------------
"claude is saying it deployed and made it live but it misses so many things
eg this telegram and CFO morning breif, adding kevins manager so many examples"

The Build Log already holds the answer and has since 9-Sep. Measured on prod the
morning he asked: 56 requests recorded, exactly ONE ever confirmed live. Not
because nothing shipped — because the board only fills itself and never empties
itself, and nothing ever put it in front of him. A register nobody opens is a
register nobody trusts.

So this sends it. Same figures as /cfo/build-log, no second source of truth:
day_report.day() computes, this only writes the email.

WHAT IT LEADS WITH
  waiting to go live   built against, still not live, oldest first. This is the
                       "a chat told me it was done" band — the Telegram fix and
                       his own morning brief were both sitting in it.
  shipped unrecorded   code that reached prod with no request behind it: work
                       nobody can trace back to an ask.
  never started        asked for, nothing built yet.

IT SENDS EVERY DAY, including a clean one. A digest that only arrives when there
is news cannot be told apart from a digest that has quietly died — which is how
the 07:00 manager report died six times unnoticed, and how the check that should
have caught the Telegram fix ran for a whole day reporting nothing but an error.

  python manage.py devlog_digest --dry-run     # print it, send nothing
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils.html import escape

NAVY = '#1D3270'
ORANGE = '#F47C20'
GREY = '#6b7280'

# Anything open longer than this has stopped being "in progress".
STALE_HOURS = 24
# Enough to act on, not so many he stops reading. The count is always exact.
MAX_ROWS = 12


def _rows(items, base):
    out = []
    for r in items[:MAX_ROWS]:
        age = r.get('age_hours') or 0
        days = age / 24
        when = f'{days:.0f} days' if days >= 1 else f'{age:.0f} hours'
        colour = ORANGE if age >= STALE_HOURS else GREY
        who = escape(r['who_asked'])
        area = f' &middot; {escape(r["area"])}' if r.get('area') else ''
        out.append(
            '<tr>'
            '<td style="padding:8px 12px;border-bottom:1px solid #eee">'
            f'<a href="{base}/cfo/build-log" '
            f'style="color:{NAVY};text-decoration:none">{escape(r["title"])}</a><br>'
            f'<span style="color:{GREY};font-size:12px">{who}{area}</span></td>'
            '<td style="padding:8px 12px;border-bottom:1px solid #eee;'
            f'white-space:nowrap;color:{colour};font-size:13px">{when} old</td>'
            '</tr>')
    return ''.join(out)


def _band(title, note, items, base):
    if not items:
        return (f'<p style="margin:18px 0 6px;color:{GREY};font-size:14px">'
                f'<b style="color:{NAVY}">{escape(title)}</b> — none.</p>')
    more = ''
    if len(items) > MAX_ROWS:
        more = (f'<p style="margin:6px 0 0;color:{GREY};font-size:12px">'
                f'and {len(items) - MAX_ROWS} more on the board.</p>')
    return (f'<h3 style="margin:22px 0 2px;color:{NAVY};font-size:16px">'
            f'{escape(title)} <span style="color:{ORANGE}">({len(items)})</span></h3>'
            f'<p style="margin:0 0 8px;color:{GREY};font-size:13px">{escape(note)}</p>'
            '<table style="width:100%;border-collapse:collapse;font-size:14px">'
            f'{_rows(items, base)}</table>{more}')


def build_html(d, base):
    counts = d['counts']
    waiting = d['in_flight']
    unlinked = d['unlinked_commits']

    unlinked_html = ''
    if unlinked:
        lines = ''.join(
            f'<li style="margin:3px 0">{escape(u["subject"])} '
            f'<span style="color:{GREY};font-size:12px">{escape(u["author"])}</span></li>'
            for u in unlinked[:MAX_ROWS])
        unlinked_html = (
            f'<h3 style="margin:22px 0 2px;color:{NAVY};font-size:16px">'
            'Shipped with no request behind it '
            f'<span style="color:{ORANGE}">({len(unlinked)})</span></h3>'
            f'<p style="margin:0 0 8px;color:{GREY};font-size:13px">'
            'Live on production today, traceable to nothing you asked for.</p>'
            f'<ul style="margin:0;padding-left:18px;font-size:14px">{lines}</ul>')

    return (
        '<div style="font-family:Segoe UI,Helvetica,Arial,sans-serif;'
        'max-width:680px;color:#111">'
        f'<h2 style="margin:0 0 2px;color:{NAVY};font-size:20px">'
        f'Build Log &mdash; {d["day"]}</h2>'
        f'<p style="margin:0 0 4px;color:{GREY};font-size:14px">'
        f'{counts["went_live_today"]} went live today &middot; '
        f'{counts.get("open_all_time", counts["still_open"])} open in total &middot; '
        f'{counts["deploys_today"]} deploy(s)</p>'
        f'<p style="margin:0 0 16px;color:{GREY};font-size:13px">'
        'Nothing here is called live unless a deploy recorded it. If something '
        'below was reported finished to you, it was not.</p>'
        + _band('Looks done — please confirm',
                'Evidence says these are finished. Answer yes or no on the Build Log.',
                d.get('looks_done', []), base)
        + _band('Waiting to go live',
                'Work exists against these. Production has not confirmed any of them.',
                waiting, base)
        + unlinked_html
        + _band('Never started', 'Asked for. Nothing built against it yet.',
                d['still_to_do'], base)
        + f'<p style="margin:26px 0 0;font-size:14px">'
          f'<a href="{base}/cfo/build-log" style="background:{NAVY};color:#fff;'
          'padding:9px 16px;border-radius:6px;text-decoration:none">'
          'Open the Build Log</a></p></div>')


class Command(BaseCommand):
    help = 'Email the CFO what is claimed done but not proven live.'

    def add_arguments(self, parser):
        parser.add_argument('--to', default='')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from django.conf import settings

        from devlog import day_report

        d = day_report.day()
        base = getattr(settings, 'PUBLIC_BASE_URL',
                       'https://omni.alphadirect.co.bw').rstrip('/')
        html = build_html(d, base)

        counts = d['counts']
        waiting = len(d['in_flight'])
        unrecorded = counts['shipped_without_a_request']
        flag = f'{unrecorded} shipped unrecorded - ' if unrecorded else ''
        subject = (f'{flag}Build Log: {waiting} waiting to go live, '
                   f'{counts["still_open"]} open, '
                   f'{counts["went_live_today"]} live today')

        text = (f'{waiting} item(s) built and not confirmed live. '
                f'{counts["still_open"]} open in total. '
                f'{counts["went_live_today"]} confirmed live today.\n\n'
                f'{base}/cfo/build-log')

        # His own address, not excoboard@ — this is his personal working board,
        # and /cfo/build-log opens for pganesharajah only.
        to = (opts.get('to') or getattr(settings, 'DEVLOG_DIGEST_TO', '')
              or 'pganesharajah@alphadirect.co.bw')

        if opts.get('dry_run'):
            self.stdout.write(f'DRY RUN — "{subject}" to {to}')
            self.stdout.write(text)
            return

        from django.core.mail import EmailMultiAlternatives
        msg = EmailMultiAlternatives(
            subject=subject, body=text,
            to=[a.strip() for a in to.split(',') if a.strip()])
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        self.stdout.write(self.style.SUCCESS(
            f'sent to {to}: {waiting} waiting, {counts["still_open"]} open, '
            f'{unrecorded} unrecorded'))
