"""send_late_reminder — 10am nudge to anyone who started after 08:15.

CFO directive 2026-09-10: "these late starters who are clocking in after
8.15am, by 10am send them just a reminder you started late today."

  python manage.py send_late_reminder             # DRY RUN
  python manage.py send_late_reminder --send      # really email

Reads today's Time Doctor worklog, finds first-activity after 08:15, sends
a short friendly reminder. Excludes C-suite, people on leave, and anyone
with a TrackingDirective(expected_to_track=False).
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

NAVY, ORANGE, MUT = '#0D1B2A', '#F4A623', '#6B7280'

EXEC_EMAILS = {
    'aiyer@alphadirect.co.bw', 'arjuniyer@alphadirect.co.bw',
    'pbeka@alphadirect.co.bw', 'ubutale@alphadirect.co.bw',
    'pganesharajah@alphadirect.co.bw', 'excoboard@alphadirect.co.bw',
    'cfo@alphadirect.co.bw',
}


def _email_html(first_name, arrival_time, date_str):
    return f"""\
<div style="font-family:Georgia,'Book Antiqua',serif;color:{NAVY};max-width:600px;margin:0 auto">
  <div style="background:{NAVY};padding:16px 24px">
    <span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>
    <span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span>
  </div>
  <div style="padding:24px;font-size:15px;line-height:1.55">
    <p style="margin-top:0">Hello {first_name},</p>
    <p>This is a quick reminder that your first activity on Time Doctor today
       was at <strong>{arrival_time}</strong>.</p>
    <p style="background:#FFF6E5;border-left:3px solid {ORANGE};padding:10px 12px;
       border-radius:4px">Your employment agreement asks you to start at
       <strong>8:00 am</strong>. Please make sure you are on time tomorrow.</p>
    <p style="color:{MUT};font-size:12px;margin-top:20px">
      Automated reminder from Omni &middot; Alpha Direct &middot; {date_str}.</p>
  </div>
</div>"""


class Command(BaseCommand):
    help = "10am late-start reminder for anyone who clocked in after 08:15."

    def add_arguments(self, parser):
        parser.add_argument('--date', default=None, help='YYYY-MM-DD (default: today)')
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--force', action='store_true', help='Bypass the on/off switch.')

    def handle(self, *args, **opts):
        from hris import eligibility
        from hris import exceptions_report as xr
        from hris.models import LeaveRequest
        from hris.workforce_roles import no_tracker_title, NO_TRACKER_TITLES
        from integrations.td_matching import TDMatcher, active_td_users, collapse_users
        from integrations.timedoctor import TimeDoctorClient
        from integrations.timedoctor_recon import _norm_name
        from core.notifications import send_html_with_cfo_cc

        on = getattr(settings, 'HOURS_REMINDERS_ENABLED', False)
        if not on and not opts['force']:
            self.stdout.write(self.style.WARNING(
                'Hours reminders OFF (HOURS_REMINDERS_ENABLED). Use --force to override.'))
            return

        d = (datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
             if opts['date'] else timezone.localtime().date())

        profiles = list(eligibility.tracking_profiles())
        on_leave_ids = set(LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED, start_date__lte=d, end_date__gte=d
        ).values_list('profile__employee_id', flat=True))

        client = TimeDoctorClient.from_settings()
        try:
            raw_users = client.users()
            users = collapse_users(active_td_users(raw_users))
        except Exception as exc:
            raise CommandError(f'Time Doctor read failed: {exc}')

        matcher = TDMatcher(users, [p.employee for p in profiles])
        uid_by_emp = {emp.id: uid for uid, emp in matcher.employee_for_uid.items()}

        now = timezone.now()
        worklog = client.worklog(d, now, user_ids=list(uid_by_emp.values()))
        per = xr.per_user_day(raw_users, worklog)

        late = []
        for p in profiles:
            e = p.employee
            email = (e.email or '').strip().lower()
            name = (e.full_name or '').strip()
            if not email or email in EXEC_EMAILS:
                continue
            if e.id in on_leave_ids:
                continue
            title = (getattr(e, 'job_title', '') or '').strip().lower()
            if title.startswith('chief ') or no_tracker_title(p) in NO_TRACKER_TITLES:
                continue
            uid = uid_by_emp.get(e.id)
            if not uid:
                continue
            rec = per.get(uid)
            if not rec or not rec.get('start'):
                continue
            start_local = (rec['start'] + xr.LOCAL_OFFSET).time()
            if start_local > xr.LATE_START_AFTER:
                arrival_str = xr._hm(rec['start'])
                late.append((name, email, arrival_str))

        late.sort(key=lambda x: x[2])

        dtxt = d.strftime('%A, %d %B %Y')
        if not opts['send']:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN — {len(late)} late starters on {dtxt}. Re-run with --send.'))
            for name, email, arr in late:
                self.stdout.write(f'  {name} <{email}> — started {arr}')
            return

        sent = failed = 0
        digest = []
        for name, email, arr in late:
            try:
                first = name.split()[0] if name else 'there'
                send_html_with_cfo_cc(
                    subject=f'Late start reminder — {dtxt}',
                    html=_email_html(first, arr, dtxt),
                    to=[email], cc_cfo=False,
                    text_fallback=(f'Your first Time Doctor activity today was at {arr}. '
                                   f'Your employment agreement asks you to start at 8:00 am.'))
                sent += 1
                digest.append(f'{name} — {arr}')
            except Exception:
                failed += 1
                self.stderr.write(self.style.WARNING(f'failed {name} <{email}>'))

        if digest:
            try:
                body = ('<div style="font-family:Georgia,serif;color:#0D1B2A">'
                        f'<p>Late-start reminder sent to {sent} staff on {dtxt}:</p><ul>'
                        + ''.join(f'<li>{d_}</li>' for d_ in sorted(digest)) + '</ul></div>')
                send_html_with_cfo_cc(
                    subject=f'Late-start reminder digest — {dtxt}',
                    html=body, to=['excoboard@alphadirect.co.bw'], cc_cfo=False)
            except Exception:
                self.stderr.write(self.style.WARNING('digest email failed'))

        self.stdout.write(self.style.SUCCESS(
            f'Late reminder {dtxt}: sent={sent}, failed={failed}, total={len(late)}.'))
