"""
integrations/management/commands/detect_fake_activity.py

Catch the "weight on the spacebar" trick for a given day and report who a human
should look at. Reuses the exact Time Doctor pull the daily report already runs;
the detection logic lives in integrations.td_integrity (unit-tested offline).

  python manage.py detect_fake_activity                 # yesterday (SAST), print only
  python manage.py detect_fake_activity --date 2026-08-28
  python manage.py detect_fake_activity --date 2026-08-28 --email   # + CFO email

Privacy (AD-POL-AI-GOV-001): reports per-person counts + minutes + a plain-English
reason only. No window/app titles are printed, emailed or stored.

The output is a FLAG for the existing "explain your day" step, never an auto-dock.
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.timedoctor import TimeDoctorClient, TimeDoctorError, aggregate
from integrations.td_integrity import analyze_day, flagged


class Command(BaseCommand):
    help = 'Detect faked Time Doctor activity (held-key / weight-on-spacebar) for a day.'

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date', help='Day to check, YYYY-MM-DD (default: yesterday SAST).')
        parser.add_argument('--email', action='store_true', help='Also email the CFO the flagged list.')

    def handle(self, *args, **opts):
        client = TimeDoctorClient.from_settings()
        if not client.configured:
            self.stdout.write(self.style.WARNING(
                'SKIPPED: TIMEDOCTOR_TOKEN / TIMEDOCTOR_COMPANY_ID not set.'))
            return

        if opts.get('date'):
            day = datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
        else:
            day = timezone.localtime().date() - datetime.timedelta(days=1)
        day_from = day
        day_to = day + datetime.timedelta(days=1)   # API `to` is exclusive

        try:
            users = client.users()
            ids = [u.get('id') for u in users if u.get('id')]
            worklog = client.worklog(day_from, day_to, user_ids=ids)
            timeuse = client.timeuse(day_from, day_to, user_ids=ids)
        except TimeDoctorError as exc:
            self.stderr.write(self.style.ERROR(f'Time Doctor pull failed: {exc}'))
            raise SystemExit(1)

        # Productive hours per canonical person, from the same aggregate the
        # daily report uses (this supplies the hours gate).
        agg = aggregate(users, worklog, timeuse, [], [], as_of=day, td_user_ids=ids)
        phours = {m.get('user_id'): m.get('productive_hours')
                  for m in agg['members'] if m.get('user_id')}

        signals = analyze_day(users, worklog, timeuse,
                              ordered_ids=ids, productive_hours_by_uid=phours)
        flags = flagged(signals)

        self.stdout.write(f'{day}: {len(signals)} people analysed, {len(flags)} to review.')
        for s in flags:
            ph = '—' if s.productive_hours is None else f'{s.productive_hours}h'
            win = '—' if s.distinct_windows is None else s.distinct_windows
            self.stdout.write(
                f"  [{s.suspicion.upper():10}] {(s.name or s.user_id or '?')[:32]:32} "
                f"prod={ph:>6}  longest_block={s.longest_block_min/60:.1f}h  "
                f"breaks={s.idle_breaks}  windows={win}")
            for r in s.reasons:
                self.stdout.write(f"               - {r}")

        if opts.get('email') and flags:
            try:
                from core.notifications import send_html_with_cfo_cc
                n = send_html_with_cfo_cc(
                    subject=f'Time Doctor — possible faked activity, {day}',
                    html=_build_html(day, flags),
                    to=['pganesharajah@alphadirect.co.bw'],
                )
                self.stdout.write(self.style.SUCCESS(f'Emailed flagged list ({n} message(s)).'))
            except Exception as exc:    # noqa: BLE001
                self.stderr.write(self.style.WARNING(f'Email failed: {exc}'))
        elif opts.get('email'):
            self.stdout.write('Nothing flagged — no email sent.')


def _build_html(day, flags) -> str:
    from django.utils.html import escape
    navy, orange, ink, mut = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280'
    rows = ''
    for s in flags:
        ph = '—' if s.productive_hours is None else f'{s.productive_hours}h'
        win = '—' if s.distinct_windows is None else s.distinct_windows
        # Escape staff-editable text (TD display names + reason lines) so a
        # renamed account can't inject markup into the CFO's email (Fable 5).
        who = escape((s.name or str(s.user_id or '?'))[:40])
        why = escape('; '.join(s.reasons) or '—')
        badge = '#B91C1C' if s.suspicion == 'suspicious' else '#B45309'
        rows += (
            f'<tr>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;color:{ink}">{who}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:center">'
            f'<span style="color:#fff;background:{badge};padding:2px 8px;border-radius:10px;font-size:11px">{s.suspicion}</span></td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;color:{navy};font-weight:700">{ph}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;color:{ink}">{s.longest_block_min/60:.1f}h</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;color:{mut}">{s.idle_breaks}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;text-align:right;color:{mut}">{win}</td>'
            f'<td style="padding:6px 10px;border-bottom:1px solid #EEF0F3;color:{mut};font-size:12px">{why}</td>'
            f'</tr>')
    return f"""<!doctype html><html><body style="margin:0;background:#EEF0F3;font-family:'Book Antiqua',Georgia,serif">
<div style="max-width:820px;margin:0 auto;background:#fff">
  <div style="background:{navy};padding:18px 24px">
    <div style="color:{orange};font-size:20px;font-weight:700">Alpha Direct — Possible Faked Activity</div>
    <div style="color:#AEB6C2;font-size:13px;margin-top:2px">Time Doctor · {day}</div>
  </div>
  <div style="padding:22px 24px">
    <p style="color:{ink};font-size:13px;margin:0 0 12px">The people below tracked productive hours in one long unbroken stretch with almost no idle breaks — the pattern a weight held on a key produces. This is a prompt to ask them to explain their day, not a conclusion.</p>
    <table style="border-collapse:collapse;width:100%;font-size:13px">
      <thead><tr style="background:{navy}">
        <th style="padding:7px 10px;text-align:left;color:#fff">Employee</th>
        <th style="padding:7px 10px;text-align:center;color:#fff">Flag</th>
        <th style="padding:7px 10px;text-align:right;color:#fff">Productive</th>
        <th style="padding:7px 10px;text-align:right;color:#fff">Longest block</th>
        <th style="padding:7px 10px;text-align:right;color:#fff">Breaks</th>
        <th style="padding:7px 10px;text-align:right;color:#fff">Windows</th>
        <th style="padding:7px 10px;text-align:left;color:#fff">Why</th>
      </tr></thead><tbody>{rows}</tbody>
    </table>
    <p style="color:{mut};font-size:11px;margin-top:16px">Per-person counts only; no window titles are collected or stored (AD-POL-AI-GOV-001).</p>
  </div>
</div></body></html>"""
