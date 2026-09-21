"""send_weekly_hours_review — Friday ~15:00 weekly Time Doctor catch-up email
(CFO-approved spec 2026-08-17). Each staff member gets their OWN Monday-to-Friday
hours table and is asked what their plan is to close any gap, and whether they
will finish it on Saturday. People skip Saturday thinking they've hit target —
this shows them the real weekly picture. REMINDER ONLY — never docks pay.

  python manage.py send_weekly_hours_review                 # DRY RUN (this week)
  python manage.py send_weekly_hours_review --send          # really email
  python manage.py send_weekly_hours_review --date 2026-08-21 --send

Audience: staff, EXCLUDING managers, C-suite and EXCO; positively-matched Time
Doctor accounts only; skips approved-leave and confirmed leavers. Gated by the
same WorkforceBriefSetting switch as the briefs; --force bypasses.
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

BASE = 'https://omni.alphadirect.co.bw'
# C-suite / EXCO / off-Saturday five — excluded by name AND email.
EXEC_NAMES = {'arun iyer', 'arjun iyer', 'paul beka', 'unami butale', 'prathap ganesharajah'}
EXEC_EMAILS = {'aiyer@alphadirect.co.bw', 'arjuniyer@alphadirect.co.bw',
               'pbeka@alphadirect.co.bw', 'ubutale@alphadirect.co.bw',
               'pganesharajah@alphadirect.co.bw', 'excoboard@alphadirect.co.bw', 'cfo@alphadirect.co.bw'}
DEFAULT_LEAVERS = {'letsweletse marumo', 'milidzani muzila'}
NAVY, ORANGE, MUT = '#0D1B2A', '#F4A623', '#6B7280'
WEEKDAY_TARGET = 6.5   # productive-hours target per weekday (CFO 2026-07-15)


def _table_html(name, week_rows, total_so_far, to_date_target, gap, link):
    # week_rows: list of (label, hours, is_today). The shortfall (gap) is measured
    # by the caller against COMPLETED days only, so today's partial hours never
    # read as a shortfall in writing.
    trs = ''
    for label, h, is_today in week_rows:
        colour = '#0A7D33' if h >= WEEKDAY_TARGET else (ORANGE if h > 0 else '#B91C1C')
        shown = f'{label} <span style="color:{MUT};font-weight:400">(so far)</span>' if is_today else label
        trs += (f'<tr><td style="padding:6px 10px;border-bottom:1px solid #EEE">{shown}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #EEE;text-align:right;'
                f'color:{colour};font-weight:700">{h:.1f}h</td></tr>')
    gap_line = (f'<p>By now this week you should have <strong>{to_date_target:.1f} hours</strong> and you are '
                f'<strong>{gap:.1f} hours</strong> short. What is your plan to catch up — and will you close it on '
                f'<strong>Saturday</strong>? Tell us in one click.</p>'
                if gap > 0.05 else
                '<p>You are <strong>on target</strong> for the week so far — thank you. If anything changes, '
                'you can still record a note below.</p>')
    return f"""\
<div style="font-family:Georgia,'Book Antiqua',serif;color:{NAVY};max-width:600px;margin:0 auto">
  <div style="background:{NAVY};padding:16px 24px"><span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>
  <span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span></div>
  <div style="padding:24px;font-size:15px;line-height:1.55">
    <p style="margin-top:0">Hello {name},</p>
    <p>Here are your Time Doctor hours for this week (Monday to Friday):</p>
    <table style="border-collapse:collapse;width:100%;margin:8px 0 4px;font-size:14px">
      {trs}
      <tr><td style="padding:8px 10px;font-weight:700;border-top:2px solid {NAVY}">Total so far this week</td>
      <td style="padding:8px 10px;text-align:right;font-weight:700;border-top:2px solid {NAVY}">{total_so_far:.1f}h</td></tr>
    </table>
    {gap_line}
    <p style="text-align:center;margin:22px 0">
      <a href="{link}" style="display:inline-block;background:{NAVY};color:#fff;font-weight:700;
      font-size:15px;text-decoration:none;padding:13px 26px;border-radius:8px">Record my catch-up plan &rarr;</a></p>
    <p style="color:{MUT};font-size:12px;margin-top:20px">Automated weekly review from Omni · Alpha Direct.
       Nothing is deducted — this is to help you keep your hours on track.</p>
  </div>
</div>"""


class Command(BaseCommand):
    help = "Friday weekly Time Doctor catch-up email (Mon-Fri hours table). Dry-run unless --send."

    def add_arguments(self, parser):
        parser.add_argument('--date', default=None, help='Any day in the target week, YYYY-MM-DD (default: today).')
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--force', action='store_true', help='Bypass the on/off switch.')
        parser.add_argument('--limit', type=int, default=0)

    def handle(self, *args, **opts):
        from hris import eligibility
        from hris.models import LeaveRequest
        from hris.leave_explain import make_plan_token
        from hris.workforce_roles import is_manager_hours_profile, no_tracker_title, NO_TRACKER_TITLES
        from integrations.timedoctor_recon import _norm_name
        from integrations.models import TimeDoctorDailySnapshot
        from core.notifications import send_html_with_cfo_cc

        # DEDICATED off-by-default switch — NOT the morning-brief switch (which is
        # already ON). Only sends once HOURS_REMINDERS_ENABLED is set. --force for a test.
        on = getattr(settings, 'HOURS_REMINDERS_ENABLED', False)
        if not on and not opts['force']:
            self.stdout.write(self.style.WARNING(
                'Hours reminders are OFF (HOURS_REMINDERS_ENABLED not set). Use --force to send anyway.'))
            return

        today = (datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
                 if opts['date'] else timezone.localtime().date())
        monday = today - datetime.timedelta(days=today.weekday())   # Monday of this week
        weekdays = [monday + datetime.timedelta(days=i) for i in range(5)
                    if monday + datetime.timedelta(days=i) <= today]
        # The shortfall is measured against COMPLETED weekdays only (today is a
        # partial day), so a Friday-afternoon review never overstates the gap in
        # writing. Today is shown as "so far" but not counted as a shortfall.
        complete_days = [d for d in weekdays if d < today]
        to_date_target = WEEKDAY_TARGET * len(complete_days)

        # Per-day hours by EMPLOYEE, resolved through the CONFIRMED Time Doctor
        # account link (integrations.td_matching.hours_by_employee) — never by
        # display name, so a name-mismatched person is never falsely short.
        from integrations.td_matching import hours_by_employee
        profiles = list(eligibility.tracking_profiles())
        all_emps = [p.employee for p in profiles]
        # Floored against the permanent record per day: the stored snapshot
        # freezes at the 06:30 pull and Time Doctor back-fills a machine that was
        # offline, so on its own this review understates a late-synced day and
        # tells a manager in writing that someone was short when Omni had already
        # corrected them (checklist L28). One query per day, no API call.
        from hris import hours_for_day
        day_hours = {}
        for d in weekdays:
            snap = TimeDoctorDailySnapshot.objects.filter(as_of=d).order_by('-updated_at').first()
            # FAILS CLOSED on purpose (no on_error): this command puts a shortfall
            # in WRITING to a person, and it already aborts when Time Doctor
            # cannot confirm an identity. An un-floored figure is the same class
            # of unproven accusation.
            day_hours[d] = hours_for_day.floored(
                d, hours_by_employee(snap.payload if snap else [], all_emps),
                profiles=profiles)

        # Data-sanity (live send): the completed days must actually have data. If
        # the most recent completed weekday has no snapshot, abort rather than tell
        # everyone they are short against missing data.
        if opts['send'] and complete_days and not day_hours.get(complete_days[-1]):
            raise CommandError(
                f'No Time Doctor snapshot for {complete_days[-1]} — aborting the weekly '
                f'review so nobody gets a false shortfall.')

        def hours_on(day, emp_id):
            return day_hours.get(day, {}).get(emp_id, 0.0)

        on_leave_ids = set(LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED, start_date__lte=today, end_date__gte=monday
        ).values_list('profile__employee_id', flat=True))
        leavers = set(DEFAULT_LEAVERS)

        def is_exec(p, nn, email):
            # no_tracker_title returns the TITLE STRING (or ''), tested against the
            # ceo/cfo/coo set — never used as a bare truthy.
            if nn in EXEC_NAMES or email.lower() in EXEC_EMAILS or no_tracker_title(p) in NO_TRACKER_TITLES:
                return True
            title = (getattr(p.employee, 'job_title', '') or '').strip().lower()
            return title.startswith('chief ')

        recipients = []
        for p in profiles:
            e = p.employee
            name = (e.full_name or '').strip()
            email = (e.email or '').strip()
            nn = _norm_name(name)
            if not email or nn in leavers or e.id in on_leave_ids:
                continue
            if is_exec(p, nn, email) or is_manager_hours_profile(p):
                continue
            recipients.append((p, name, email))

        # POSITIVELY-MATCHED ONLY — never email the wrong person (fail closed).
        held = []
        if recipients:
            from integrations.td_matching import TDMatcher, active_td_users, collapse_users
            from integrations.timedoctor import TimeDoctorClient
            client = TimeDoctorClient.from_settings()
            try:
                users = collapse_users(active_td_users(client.users()))
                matcher = TDMatcher(users, [r[0].employee for r in recipients])
                matched_emp_ids = set(matcher.employee_for_uid.values())
                matched_emp_ids = {emp.id for emp in matched_emp_ids}
            except Exception as exc:    # noqa: BLE001
                msg = (f'Time Doctor read failed ({exc}) — cannot confirm identities, so NO weekly '
                       f'emails were sent. Re-run when Time Doctor answers.')
                self.stderr.write(self.style.ERROR(msg))
                raise CommandError(msg)
            held = [name for p, name, _e in recipients if p.employee_id not in matched_emp_ids]
            recipients = [r for r in recipients if r[0].employee_id in matched_emp_ids]

        recipients.sort(key=lambda r: r[1].lower())
        if opts['limit']:
            recipients = recipients[:opts['limit']]
        if held:
            self.stdout.write(self.style.WARNING(
                f'{len(held)} unmatched name(s) skipped (confirm in Who-Tracks): ' + ', '.join(sorted(held))))

        # Build each person's week + send.
        def week_for(emp_id):
            rows, total_so_far, complete_total = [], 0.0, 0.0
            for d in weekdays:
                h = hours_on(d, emp_id)
                total_so_far += h
                if d < today:
                    complete_total += h
                rows.append((d.strftime('%a %d %b'), h, d == today))
            gap = max(0.0, to_date_target - complete_total)
            return rows, total_so_far, gap

        if not opts['send']:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN — weekly review for {len(recipients)} staff, week of {monday} '
                f'({len(complete_days)} completed weekday(s), to-date target {to_date_target:.1f}h). '
                f'No email sent.'))
            for p, name, email in recipients:
                _rows, total_so_far, gap = week_for(p.employee_id)
                self.stdout.write(f'  {name} <{email}>  {total_so_far:.1f}h so far, {gap:.1f}h short')
            return

        sent = failed = 0
        digest = []
        for p, name, email in recipients:
            try:
                rows, total_so_far, gap = week_for(p.employee_id)
                link = f"{BASE}/api/leave-explain/{make_plan_token(p.employee_id)}/"
                send_html_with_cfo_cc(
                    subject=f'Your weekly hours & catch-up plan — week of {monday.strftime("%d %b")}',
                    html=_table_html(name.split()[0] if name else 'there', rows, total_so_far,
                                     to_date_target, gap, link),
                    to=[email], cc_cfo=False,
                    text_fallback=(f'Your Time Doctor hours so far this week total {total_so_far:.1f}h; you are '
                                   f'{gap:.1f}h short of the {to_date_target:.1f}h due by now. '
                                   f'Record your catch-up plan (no login): {link}'))
                sent += 1
                digest.append(f'{name} — {total_so_far:.1f}h so far, {gap:.1f}h short')
            except Exception as exc:    # noqa: BLE001
                failed += 1
                self.stderr.write(self.style.WARNING(f'failed {name} <{email}>: {exc}'))

        if digest:
            try:
                body = ('<div style="font-family:Georgia,serif;color:#0D1B2A">'
                        f'<p>Weekly hours review sent to {sent} staff for the week of {monday}:</p><ul>'
                        + ''.join(f'<li>{d_}</li>' for d_ in sorted(digest)) + '</ul></div>')
                send_html_with_cfo_cc(subject=f'Weekly hours review digest — week of {monday}',
                                      html=body, to=['excoboard@alphadirect.co.bw'], cc_cfo=False)
            except Exception as exc:    # noqa: BLE001
                self.stderr.write(self.style.WARNING(f'digest email failed: {exc}'))

        self.stdout.write(self.style.SUCCESS(
            f'Weekly review week of {monday}: emailed={sent}, failed={failed}, total={len(recipients)}.'))
