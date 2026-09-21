"""send_saturday_explain — email each salaried person who had NO Time Doctor
hours on a given day a one-click "explain your day" button (CFO + CEO approved
2026-07-22). They click, say worked/on-leave + office/off-site, write >=50 words;
it stores on the backend and shows in the CFO Excuses feed. Leave for
non-responders is applied SEPARATELY with CFO sign-off — never by this command.

  python manage.py send_saturday_explain                 # DRY RUN (default) — lists recipients
  python manage.py send_saturday_explain --send          # really email them
  python manage.py send_saturday_explain --date 2026-07-18 --deadline "5pm today"
  python manage.py send_saturday_explain --leavers "letsweletse marumo,milidzani muzila"

Audience = tracking-eligible staff (has a payslip) who tracked 0h that day,
MINUS the five off on Saturdays, MINUS confirmed leavers, MINUS anyone on
approved leave that day.
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

BASE = 'https://omni.alphadirect.co.bw'
SAT_OFF = {'arun iyer', 'arjun iyer', 'paul beka', 'unami butale', 'prathap ganesharajah'}
# Also exclude the off-Saturday five by EMAIL — omni has duplicate/mislabelled
# records (e.g. "Arjun Parameswaran" carrying arjuniyer@) that slip a name check.
SAT_OFF_EMAILS = {'aiyer@alphadirect.co.bw', 'arjuniyer@alphadirect.co.bw',
                  'pbeka@alphadirect.co.bw', 'ubutale@alphadirect.co.bw',
                  'pganesharajah@alphadirect.co.bw'}
DEFAULT_LEAVERS = {'letsweletse marumo', 'milidzani muzila'}
NAVY, ORANGE, MUT = '#0D1B2A', '#F4A623', '#6B7280'


def _email_html(name: str, dtxt: str, link: str, deadline: str, hours_phrase: str = 'no hours') -> str:
    return f"""\
<div style="font-family:Georgia,'Book Antiqua',serif;color:{NAVY};max-width:600px;margin:0 auto">
  <div style="background:{NAVY};padding:16px 24px"><span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>
  <span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span></div>
  <div style="padding:24px;font-size:15px;line-height:1.55">
    <p style="margin-top:0">Hello {name},</p>
    <p>Time Doctor recorded <strong>{hours_phrase}</strong> for you on <strong>{dtxt}</strong>.
       Please tell us what happened — it takes a minute.</p>
    <p>Click the button, say whether you were <strong>working</strong> (office or off-site) or
       <strong>on leave</strong>, and write <strong>at least 50 words</strong> explaining the day.
       No login needed.</p>
    <p style="text-align:center;margin:22px 0">
      <a href="{link}" style="display:inline-block;background:{NAVY};color:#fff;font-weight:700;
      font-size:15px;text-decoration:none;padding:13px 26px;border-radius:8px">Explain my day →</a></p>
    <p><strong>Please respond by {deadline}.</strong> If we don't hear from you, leave may be applied
       for that day (you can appeal it).</p>
    <p style="color:{MUT};font-size:12px;margin-top:20px">Automated message from Omni · Alpha Direct.
       If you actually worked and this looks wrong, still click and say so — it will be reviewed.</p>
  </div>
</div>"""


class Command(BaseCommand):
    help = "Email the day's no-hours salaried staff a one-click explain button. Dry-run unless --send."

    def add_arguments(self, parser):
        parser.add_argument('--date', default='2026-07-18')
        parser.add_argument('--deadline', default='5pm today')
        parser.add_argument('--leavers', default='')
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--band', choices=['zero', 'under1'], default='zero',
                            help="'zero' = tracked ~0h (default); 'under1' = tracked >0 but <1h.")

    def handle(self, *args, **opts):
        from hris import eligibility
        from hris.models import LeaveRequest
        from hris.leave_explain import make_token
        from integrations.timedoctor_recon import _norm_name
        from integrations.models import TimeDoctorDailySnapshot
        from core.notifications import send_html_with_cfo_cc

        d = datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
        leavers = set(DEFAULT_LEAVERS)
        leavers |= {x.strip().lower() for x in (opts['leavers'] or '').split(',') if x.strip()}

        snap = TimeDoctorDailySnapshot.objects.filter(as_of=d).order_by('-updated_at').first()
        # Hours by EMPLOYEE via the CONFIRMED Time Doctor account link, never by
        # display name — a name-mismatched person is never falsely asked to explain.
        from integrations.td_matching import hours_by_employee
        profiles = list(eligibility.tracking_profiles())
        # Floored against the permanent record: never ask someone to explain a
        # shortfall against a figure Omni has already corrected upward
        # (checklist L28).
        from hris import hours_for_day
        # FAILS CLOSED on purpose (no on_error) — see the guard below: this
        # command already refuses to send when Time Doctor cannot prove a zero,
        # and an un-floored figure is the same unproven accusation.
        emp_hours = hours_for_day.floored(
            d, hours_by_employee(snap.payload if snap else [],
                                 [p.employee for p in profiles]),
            profiles=profiles)

        on_leave_ids = set(LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED, start_date__lte=d, end_date__gte=d
        ).values_list('profile__employee_id', flat=True))

        band = opts['band']
        recipients = []
        for p in profiles:
            e = p.employee
            name = (e.full_name or '').strip()
            email = (e.email or '').strip()
            nn = _norm_name(name)
            if nn in SAT_OFF or email.lower() in SAT_OFF_EMAILS or nn in leavers or e.id in on_leave_ids:
                continue
            if not email:
                continue
            h = emp_hours.get(p.employee_id, 0.0)
            if band == 'zero' and h >= 0.01:
                continue
            if band == 'under1' and not (0.01 <= h < 1.0):
                continue
            recipients.append((p, name, email, h))

        # PEOPLE-DATA GUARDRAIL (CFO 2026-08-01): the same settle + AI gate the
        # 09:00 report and the morning brief use. Nobody is asked to explain a
        # zero that Time Doctor simply hasn't finished uploading — a held name is
        # dropped from this send entirely. Fail-safe: unproven = held.
        held_names: list = []
        if recipients:
            from hris import people_data_guard as pdg
            from integrations.td_matching import TDMatcher, active_td_users, collapse_users
            from integrations.timedoctor import TimeDoctorClient
            client = TimeDoctorClient.from_settings()
            try:
                users = collapse_users(active_td_users(client.users()))
                matcher = TDMatcher(users, [r[0].employee for r in recipients])
                uid_by_emp = {emp.id: uid for uid, emp in matcher.employee_for_uid.items()}
            except Exception as exc:    # noqa: BLE001
                # FAIL CLOSED. No Time Doctor read = nothing is proven, and an
                # unproven zero must never become an accusation. Abort the whole
                # send rather than let an empty match set read as "nobody held".
                msg = (f'Time Doctor read failed ({exc}) — cannot prove anyone was genuinely '
                       f'absent, so NO explain emails were sent. Re-run when Time Doctor answers.')
                self.stderr.write(self.style.ERROR(msg))
                # Non-zero exit so a cron run cannot look successful while
                # silently sending nothing.
                raise CommandError(msg)
            pairs = [(uid_by_emp[p.employee_id], name)
                     for p, name, _e, _h in recipients if p.employee_id in uid_by_emp]
            # Anyone we could not match to a Time Doctor account has no data to
            # settle either — same rule, hold them.
            unmatched = [(p, name) for p, name, _e, _h in recipients
                         if p.employee_id not in uid_by_emp]
            if unmatched:
                held_names.extend(n for _p, n in unmatched)
                recipients = [r for r in recipients if r[0].employee_id in uid_by_emp]
            held = pdg.held_uids(pairs, d, client=client)
            if held:
                # EXTEND, never assign — the unmatched names collected above are
                # held too and must stay in the count.
                held_names.extend(n for u, n in pairs if u in held)
                recipients = [r for r in recipients
                              if uid_by_emp.get(r[0].employee_id) not in held]
        held_names.sort()

        recipients.sort(key=lambda r: r[1].lower())
        if opts['limit']:
            recipients = recipients[:opts['limit']]
        if held_names:
            self.stdout.write(self.style.WARNING(
                f'guardrail HELD {len(held_names)} name(s) — hours may still be uploading: '
                + ', '.join(held_names)))

        dtxt = d.strftime('%A, %d %B %Y')
        if not opts['send']:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN — {len(recipients)} recipients for {dtxt} (re-run with --send). No email sent.'))
            for _, name, email, h in recipients:
                self.stdout.write(f'  {name} <{email}> ({h:.2f}h)')
            return

        sent = failed = 0
        for p, name, email, h in recipients:
            try:
                link = f"{BASE}/api/leave-explain/{make_token(p.employee_id, opts['date'])}/"
                phrase = 'no hours' if h < 0.01 else f'only about {round(h * 60)} minutes'
                send_html_with_cfo_cc(
                    subject=f'Please explain your hours — {dtxt}',
                    html=_email_html(name.split()[0] if name else 'there', dtxt, link, opts['deadline'], phrase),
                    to=[email], cc_cfo=False,
                    text_fallback=(f'Time Doctor shows {phrase} for you on {dtxt}. Please open Omni and explain '
                                   f'(worked/on leave, 50+ words) by {opts["deadline"]}: {link}'))
                sent += 1
            except Exception as exc:    # noqa: BLE001
                failed += 1
                self.stderr.write(self.style.WARNING(f'failed {name} <{email}>: {exc}'))
        self.stdout.write(self.style.SUCCESS(f'{dtxt}: emailed={sent}, failed={failed}, total={len(recipients)}.'))
