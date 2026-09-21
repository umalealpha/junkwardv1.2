"""send_hours_reminder — intraday Time Doctor hours nudges (CFO-approved spec
2026-08-17). REMINDERS ONLY — this command NEVER applies leave or docks pay; it
just emails a person who is behind on today's hours a one-click "explain your
day" button. Docking stays the separate /alphaleave engine with CFO sign-off.

  python manage.py send_hours_reminder --slot morning     # 11:45, under 2h  (DRY RUN)
  python manage.py send_hours_reminder --slot afternoon    # 16:00, under 4h  (DRY RUN)
  python manage.py send_hours_reminder --slot morning --send   # really email

Audience (positively-MATCHED Time Doctor accounts only — never fuzzy-name email):
  * morning   — staff, EXCLUDING managers, C-suite and EXCO; under 2h so far;
                if they first tracked after 08:15 it adds the 8am-start line.
  * afternoon — staff AND managers (EXCLUDING only C-suite / EXCO); under 4h;
                CCs the person's manager (escalation).
Skips anyone on approved leave, confirmed leavers, and anyone Time Doctor simply
hasn't finished syncing (the people-data guardrail — critical intraday).

Gated by the same WorkforceBriefSetting switch as the briefs; --force bypasses.
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

BASE = 'https://omni.alphadirect.co.bw'
# The C-suite / EXCO / off-Saturday five — excluded from EVERY slot, by name AND
# email (omni has duplicate/mislabelled records that slip a name-only check).
EXEC_NAMES = {'arun iyer', 'arjun iyer', 'paul beka', 'unami butale', 'prathap ganesharajah'}
EXEC_EMAILS = {'aiyer@alphadirect.co.bw', 'arjuniyer@alphadirect.co.bw',
               'pbeka@alphadirect.co.bw', 'ubutale@alphadirect.co.bw',
               'pganesharajah@alphadirect.co.bw', 'excoboard@alphadirect.co.bw', 'cfo@alphadirect.co.bw'}
DEFAULT_LEAVERS = {'letsweletse marumo', 'milidzani muzila'}
NAVY, ORANGE, MUT = '#0D1B2A', '#F4A623', '#6B7280'

SLOTS = {
    'morning':   {'threshold': 2.0, 'when': '11:45',  'exclude_managers': True,
                  'arrival_line': True,  'escalate': False,
                  'subject': 'Your Time Doctor hours so far today'},
    'afternoon': {'threshold': 4.0, 'when': '4:00 pm', 'exclude_managers': False,
                  'arrival_line': False, 'escalate': True,
                  'subject': 'Time Doctor — finishing your day'},
}


def _email_html(name, dtxt, link, threshold, hours, when, late_start=None):
    hrs = f'{hours:.1f}'.rstrip('0').rstrip('.')
    late = ''
    if late_start:
        late = (f'<p style="background:#FFF6E5;border-left:3px solid {ORANGE};padding:10px 12px;'
                f'border-radius:4px">Your first activity today was at <strong>{late_start}</strong>. '
                f'Your employment agreement asks you to start at <strong>8:00 am</strong>.</p>')
    return f"""\
<div style="font-family:Georgia,'Book Antiqua',serif;color:{NAVY};max-width:600px;margin:0 auto">
  <div style="background:{NAVY};padding:16px 24px"><span style="color:#fff;font-size:20px;letter-spacing:1px">OMNI</span>
  <span style="color:{ORANGE};font-size:13px;margin-left:8px">Alpha Direct</span></div>
  <div style="padding:24px;font-size:15px;line-height:1.55">
    <p style="margin-top:0">Hello {name},</p>
    <p>As of <strong>{when}</strong> today, Time Doctor shows about <strong>{hrs} hours</strong> for you —
       under the <strong>{threshold:.0f} hours</strong> we would expect by now.</p>
    {late}
    <p>If Time Doctor isn't running, please <strong>restart it</strong> (or restart your computer). If you are
       on leave or away with notice, tell us in one click — no login needed.</p>
    <p style="text-align:center;margin:22px 0">
      <a href="{link}" style="display:inline-block;background:{NAVY};color:#fff;font-weight:700;
      font-size:15px;text-decoration:none;padding:13px 26px;border-radius:8px">Explain my day &rarr;</a></p>
    <p style="color:{MUT};font-size:12px;margin-top:20px">Automated reminder from Omni · Alpha Direct.
       Nothing is deducted — this is a nudge to keep your hours on track. If you are working and this looks
       wrong, still click and say so.</p>
  </div>
</div>"""


class Command(BaseCommand):
    help = "Intraday Time Doctor hours reminder (morning <2h / afternoon <4h). Dry-run unless --send."

    def add_arguments(self, parser):
        parser.add_argument('--slot', choices=list(SLOTS), required=True)
        parser.add_argument('--date', default=None, help='Day to check, YYYY-MM-DD (default: today).')
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--force', action='store_true', help='Bypass the on/off switch.')
        parser.add_argument('--limit', type=int, default=0)

    def handle(self, *args, **opts):
        from hris import eligibility
        from hris.models import LeaveRequest
        from hris.leave_explain import make_token
        from hris.workforce_roles import is_manager_hours_profile, no_tracker_title, NO_TRACKER_TITLES
        from integrations.timedoctor_recon import _norm_name
        from integrations.models import TimeDoctorDailySnapshot
        from core.notifications import send_html_with_cfo_cc

        slot = SLOTS[opts['slot']]

        # DEDICATED on/off switch, default OFF — deliberately NOT the morning-brief
        # switch (that one is already ON in prod, which would have made these send
        # before the CFO's dry-run + sample). These reminders only go out once
        # HOURS_REMINDERS_ENABLED is set true. --force bypasses for a dry test.
        on = getattr(settings, 'HOURS_REMINDERS_ENABLED', False)
        if not on and not opts['force']:
            self.stdout.write(self.style.WARNING(
                'Hours reminders are OFF (HOURS_REMINDERS_ENABLED not set). Use --force to send anyway.'))
            return

        d = (datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
             if opts['date'] else timezone.localtime().date())

        # Today's hours so far — from the latest (intraday) snapshot for the day.
        snap = TimeDoctorDailySnapshot.objects.filter(as_of=d).order_by('-updated_at').first()

        # FRESH-DATA GATE (live send only). The intraday pull at :40 must have
        # written today's snapshot minutes ago. If it is missing or stale, ABORT:
        # otherwise the resolver reads 0.0 for EVERYONE and the whole company
        # reads as "behind" — a mass false email. (--date backfills/tests skip it.)
        if opts['send'] and not opts['date']:
            fresh = bool(snap and snap.updated_at
                         and (timezone.now() - snap.updated_at) <= datetime.timedelta(minutes=40))
            if not fresh:
                raise CommandError(
                    'No fresh Time Doctor snapshot for today (the intraday pull is missing or '
                    'stale) — aborting so we never email the whole company on empty data.')
        # Today's hours resolved through the CONFIRMED Time Doctor account link
        # (integrations.td_matching.hours_by_employee) — NEVER by display name, so a
        # person whose Time Doctor name differs from their HR name is read correctly
        # and can never be falsely nudged as "0h". Employees with no matched account
        # are absent (default 0.0) and are dropped by the positively-matched filter.
        from integrations.td_matching import hours_by_employee
        profiles = list(eligibility.tracking_profiles())
        emp_hours = hours_by_employee(snap.payload if snap else [],
                                      [p.employee for p in profiles])

        on_leave_ids = set(LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED, start_date__lte=d, end_date__gte=d
        ).values_list('profile__employee_id', flat=True))
        leavers = set(DEFAULT_LEAVERS)

        def is_exec(p, nn, email):
            # no_tracker_title returns the person's TITLE STRING (or ''), not a bool —
            # it must be tested against the ceo/cfo/coo set, exactly as every other
            # caller does. Using it truthily excluded anyone with any title at all.
            if nn in EXEC_NAMES or email.lower() in EXEC_EMAILS or no_tracker_title(p) in NO_TRACKER_TITLES:
                return True
            title = (getattr(p.employee, 'job_title', '') or '').strip().lower()
            return title.startswith('chief ')

        threshold = slot['threshold']
        recipients = []
        for p in profiles:
            e = p.employee
            name = (e.full_name or '').strip()
            email = (e.email or '').strip()
            nn = _norm_name(name)
            if not email or nn in leavers or e.id in on_leave_ids:
                continue
            if is_exec(p, nn, email):
                continue
            if slot['exclude_managers'] and is_manager_hours_profile(p):
                continue
            h = emp_hours.get(p.employee_id, 0.0)
            if h >= threshold:
                continue
            recipients.append((p, name, email, h))

        # POSITIVELY-MATCHED ONLY (CFO: never email the wrong person). We do NOT
        # reuse the morning-after people_data_guard hold here: it assumes "zero
        # today" and holds anyone who tracked recently but is behind now — i.e.
        # exactly the person we want to nudge. The freshness gate above already
        # blocks the mass-email-on-empty-data case. Fail CLOSED on a dead TD read.
        held_names, arrival = [], {}
        if recipients:
            from integrations.td_matching import TDMatcher, active_td_users, collapse_users
            from integrations.timedoctor import TimeDoctorClient
            client = TimeDoctorClient.from_settings()
            try:
                raw_users = client.users()
                users = collapse_users(active_td_users(raw_users))
                matcher = TDMatcher(users, [r[0].employee for r in recipients])
                uid_by_emp = {emp.id: uid for uid, emp in matcher.employee_for_uid.items()}
            except Exception as exc:    # noqa: BLE001
                msg = (f'Time Doctor read failed ({exc}) — cannot confirm identities, so NO reminder '
                       f'emails were sent. Re-run when Time Doctor answers.')
                self.stderr.write(self.style.ERROR(msg))
                raise CommandError(msg)
            # Drop anyone we cannot match to a Time Doctor account (positively-matched only).
            unmatched = [(p, name) for p, name, _e, _h in recipients if p.employee_id not in uid_by_emp]
            if unmatched:
                held_names.extend(n for _p, n in unmatched)
                recipients = [r for r in recipients if r[0].employee_id in uid_by_emp]

            # Arrival time for the "started after 08:15" line (morning only).
            if slot['arrival_line'] and recipients:
                try:
                    from hris import exceptions_report as xr
                    now = timezone.now()
                    worklog = client.worklog(d, now, user_ids=list(uid_by_emp.values()))
                    per = xr.per_user_day(raw_users, worklog)
                    for p, name, _e, _h in recipients:
                        rec = per.get(uid_by_emp.get(p.employee_id))
                        start = rec.get('start') if rec else None
                        if start and (start + xr.LOCAL_OFFSET).time() > xr.LATE_START_AFTER:
                            arrival[p.employee_id] = xr._hm(start)
                except Exception as exc:    # noqa: BLE001 — arrival line is a bonus, never block the send
                    self.stderr.write(self.style.WARNING(f'arrival-time lookup skipped: {exc}'))

        # ── AI SEND-GUARD (CFO 2026-08-27) ─────────────────────────────────
        # Before we tell anyone they are behind, two independent engines
        # (DeepSeek + Gemini, off the Claude subscription) sanity-check each low
        # reading. Anyone who looks like a data error — or whose Time Doctor
        # account is not confirmed-linked — is HELD, never emailed. Fail-closed:
        # if the engines cannot run, everyone is held. This is the backstop behind
        # the map-based hours fix, so a wrong "behind on hours" email can never go.
        guard_held = []
        if recipients:
            from hris.hours_reminder_guard import hold_suspect_reminders
            from integrations.models import TimeDoctorUserMap
            confirmed_ids = set(TimeDoctorUserMap.objects.filter(
                confirmed=True, employee__isnull=False).values_list('employee_id', flat=True))
            emps = [r[0].employee for r in recipients]
            recent = list(TimeDoctorDailySnapshot.objects.filter(as_of__lt=d).order_by('-as_of')[:10])
            # Each past day floored against the permanent record, so a person's
            # "typical" hours are not dragged down by days whose figures arrived
            # late and were corrected afterwards (checklist L28). This median is
            # what decides whether a reminder is held as suspect.
            from hris import hours_for_day
            hist = {}
            for s in recent:
                # on_error='degrade': this median only decides whether a reminder
                # is HELD as suspect, so a stale figure is better than killing the
                # whole reminder run. It is logged loudly either way. The two
                # commands that accuse someone fail closed instead.
                for eid, v in hours_for_day.floored(
                        s.as_of, hours_by_employee(s.payload, emps),
                        on_error='degrade').items():
                    if v > 0:
                        hist.setdefault(eid, []).append(v)

            def _typical(eid):
                xs = sorted(hist.get(eid, []))
                return xs[len(xs) // 2] if xs else None

            items = [{'ref': i, 'hours': h, 'threshold': threshold,
                      'typical': _typical(p.employee_id),
                      'mapped': p.employee_id in confirmed_ids,
                      'arrival': arrival.get(p.employee_id)}
                     for i, (p, _n, _e, h) in enumerate(recipients)]
            held_refs, hold_reason, _ai_ran = hold_suspect_reminders(items)
            if held_refs:
                guard_held = [(recipients[i][1], hold_reason[i]) for i in sorted(held_refs)]
                recipients = [r for i, r in enumerate(recipients) if i not in held_refs]
                self.stdout.write(self.style.WARNING(
                    f'AI send-guard HELD {len(guard_held)} (not emailed): '
                    + '; '.join(f'{n} — {r}' for n, r in guard_held)))

        recipients.sort(key=lambda r: r[1].lower())
        if opts['limit']:
            recipients = recipients[:opts['limit']]
        if held_names:
            self.stdout.write(self.style.WARNING(
                f'guardrail HELD {len(held_names)} name(s) (unmatched or still syncing): '
                + ', '.join(sorted(held_names))))

        when = slot['when']
        if not opts['send']:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN [{opts['slot']}] — {len(recipients)} under {threshold:.0f}h at {when} on {d} "
                f"(re-run with --send). No email sent."))
            for _, name, email, h in recipients:
                extra = f" · arrived {arrival.get(_.employee_id)}" if _.employee_id in arrival else ''
                self.stdout.write(f'  {name} <{email}> ({h:.2f}h){extra}')
            return

        dtxt = d.strftime('%A, %d %B %Y')
        sent = failed = 0
        digest = []
        for p, name, email, h in recipients:
            try:
                link = f"{BASE}/api/leave-explain/{make_token(p.employee_id, d.isoformat())}/"
                cc = None
                if slot['escalate']:
                    mgr = getattr(getattr(p, 'manager', None), 'email', None)
                    if mgr and mgr.lower() not in EXEC_EMAILS:
                        cc = [mgr]
                send_html_with_cfo_cc(
                    subject=f"{slot['subject']} — {dtxt}",
                    html=_email_html(name.split()[0] if name else 'there', dtxt, link,
                                     threshold, h, when, late_start=arrival.get(p.employee_id)),
                    to=[email], cc=cc, cc_cfo=False,
                    text_fallback=(f'As of {when} today Time Doctor shows ~{h:.1f}h for you (under {threshold:.0f}h). '
                                   f'Restart Time Doctor, or explain your day (no login): {link}'))
                sent += 1
                digest.append(f'{name} — {h:.1f}h')
            except Exception as exc:    # noqa: BLE001
                failed += 1
                self.stderr.write(self.style.WARNING(f'failed {name} <{email}>: {exc}'))

        # ONE HR/CFO digest — who was reminded, together (not per-person).
        if digest:
            try:
                body = ('<div style="font-family:Georgia,serif;color:#0D1B2A">'
                        f'<p>{opts["slot"].title()} hours reminder ({when}, under {threshold:.0f}h) sent to '
                        f'{sent} staff on {dtxt}:</p><ul>'
                        + ''.join(f'<li>{d_}</li>' for d_ in sorted(digest)) + '</ul></div>')
                send_html_with_cfo_cc(subject=f'Hours reminder digest — {opts["slot"]} {dtxt}',
                                      html=body, to=['excoboard@alphadirect.co.bw'], cc_cfo=False)
            except Exception as exc:    # noqa: BLE001
                self.stderr.write(self.style.WARNING(f'digest email failed: {exc}'))

        self.stdout.write(self.style.SUCCESS(
            f"[{opts['slot']}] {dtxt}: emailed={sent}, failed={failed}, total={len(recipients)}."))
