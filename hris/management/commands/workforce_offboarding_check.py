"""
hris/management/commands/workforce_offboarding_check.py

Investigation-ticket engine (CFO 2026-07-14, "do as per your recommendation").

When an employee who is EXPECTED to track has NOT tracked for N consecutive
WORKING days (default 5) and has NO leave covering them, raise ONE HR
investigation ticket to Unami (CFO informed). It NEVER disables an account,
never touches payroll, never terminates anyone — Botswana labour law +
CFO-authorises-terminations. It only recommends a human review.

Hard safeguards (Fable review):
  - Counts WORKING days only (skips Sundays + non-working public holidays).
  - Excludes anyone with APPROVED or PENDING leave (any type, incl. sick)
    covering a day in the window, and payroll status on_leave/suspended.
  - Excludes new joiners (hire_date within NEW_JOINER_DAYS, default 14).
  - Only employees matched to a real Time Doctor account (never the ghost list).
  - Outage guard: if the company-wide tracked count looks like a Time Doctor
    outage / expired token (circuit breaker), it does NOTHING and alerts the CFO.
  - One OPEN investigation ticket per person (never duplicates).
  - Gated by BOTH the Workforce switch AND its own WORKFORCE_OFFBOARDING_ENABLED
    flag (default OFF) so it stays dark until the CFO enables it after a clean run.

  python manage.py workforce_offboarding_check --dry-run
  python manage.py workforce_offboarding_check --force        # ignore both flags
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

NO_TRACK_WORKING_DAYS = 5
NEW_JOINER_DAYS = 14
UNAMI_EMAIL = 'ubutale@alphadirect.co.bw'
CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'


class Command(BaseCommand):
    help = 'Raise HR investigation tickets for staff with 5 working days of no Time Doctor tracking (no auto-action).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true', help='Ignore both enable flags.')
        parser.add_argument('--days', type=int, default=NO_TRACK_WORKING_DAYS)

    def handle(self, *args, **opts):
        from hris.models import WorkforceBriefSetting
        force = opts.get('force')
        on = WorkforceBriefSetting.is_enabled() or getattr(settings, 'WORKFORCE_BRIEF_ENABLED', False)
        offboarding_on = getattr(settings, 'WORKFORCE_OFFBOARDING_ENABLED', False)
        if not force and (not on or not offboarding_on):
            self.stdout.write(self.style.WARNING(
                f'SKIPPED: brief_switch={on}, offboarding_enabled={offboarding_on} '
                '(both must be on; use --force to test).'))
            return

        from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
        from integrations.td_matching import TDMatcher, active_td_users
        from hris import eligibility, exceptions_report, workforce
        from hris.workforce_brief import holiday_off_dates
        from hris.models import LeaveRequest

        client = TimeDoctorClient.from_settings()
        if not client.configured:
            self.stdout.write(self.style.WARNING('SKIPPED: TIMEDOCTOR_TOKEN not set.'))
            return

        today = timezone.localtime().date()
        off_dates = holiday_off_dates()
        n = int(opts.get('days') or NO_TRACK_WORKING_DAYS)

        # The N most recent WORKING days up to yesterday.
        working_days, d = [], today - datetime.timedelta(days=1)
        while len(working_days) < n and d > today - datetime.timedelta(days=n * 4):
            if workforce.required_hours_for_date(d, off_dates) > 0:
                working_days.append(d)
            d -= datetime.timedelta(days=1)
        if len(working_days) < n:
            self.stdout.write(self.style.SUCCESS('Not enough working days in range — nothing to do.'))
            return
        window_start, window_end = min(working_days), max(working_days)

        try:
            users = active_td_users(client.users())
            ids = [u.get('id') for u in users if u.get('id')]
            profiles = eligibility.tracking_profiles()
            matcher = TDMatcher(users, [p.employee for p in profiles])
            wl = client.worklog(exceptions_report.day_window_utc(window_start)[0],
                                exceptions_report.day_window_utc(window_end)[1], user_ids=ids)
        except TimeDoctorError as exc:
            self.stderr.write(self.style.ERROR(f'Time Doctor pull failed: {exc}'))
            raise SystemExit(1)

        sec_by = exceptions_report.tracked_seconds_by_user_day(wl)

        # Outage guard — if almost nobody tracked across the window, this is a
        # data problem, not a workforce one. Do NOT raise tickets.
        matched = matcher.employee_for_uid
        active_uids = {uid for uid in matched
                       for wd in working_days if sec_by.get((uid, wd), 0) > 0}
        if matched and len(active_uids) < 0.5 * len(matched):
            why = (f'only {len(active_uids)}/{len(matched)} matched staff tracked at all across '
                   f'{window_start}..{window_end} — looks like a Time Doctor outage, not absence')
            self._alert_cfo_outage(why, opts.get('dry_run'))
            self.stderr.write(self.style.ERROR(f'OUTAGE GUARD — no tickets raised: {why}'))
            return

        # profile lookup by employee id
        prof_by_emp = {p.employee_id: p for p in profiles}
        candidates = []
        for uid, emp in matched.items():
            prof = prof_by_emp.get(emp.id)
            if prof is None:
                continue
            if getattr(emp, 'status', 'active') != 'active':
                continue
            if emp.hire_date and (today - emp.hire_date).days < NEW_JOINER_DAYS:
                continue
            # zero tracked on EVERY working day in the window?
            if any(sec_by.get((uid, wd), 0) > 0 for wd in working_days):
                continue
            # any leave (approved OR pending, any type) covering any window day?
            has_leave = LeaveRequest.objects.filter(
                profile=prof, start_date__lte=window_end, end_date__gte=window_start,
                status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.PENDING]).exists()
            if has_leave:
                continue
            candidates.append(emp)

        # PEOPLE-DATA GUARDRAIL (CFO 2026-08-01): an HR investigation ticket is
        # the sharpest accusation of the lot, so it gets the same settle + AI
        # gate as the 09:00 report and the morning brief. Held on the most recent
        # window day = data still arriving; no ticket.
        if candidates:
            from hris import people_data_guard as pdg
            uid_by_emp = {emp.id: uid for uid, emp in matched.items()}
            pairs = [(uid_by_emp[e.id], (e.full_name or '').strip())
                     for e in candidates if e.id in uid_by_emp]
            held = pdg.held_uids(pairs, window_end, client=client)
            if held:
                held_names = sorted(n for u, n in pairs if u in held)
                candidates = [e for e in candidates if uid_by_emp.get(e.id) not in held]
                self.stdout.write(self.style.WARNING(
                    f'guardrail HELD {len(held_names)} candidate(s) — hours may still be '
                    f'uploading, no ticket raised: ' + ', '.join(held_names)))

        if opts.get('dry_run'):
            self.stdout.write(f'[dry] window {window_start}..{window_end} ({n} working days); '
                              f'matched={len(matched)}; investigation candidates={len(candidates)}: '
                              + ', '.join((e.full_name or '') for e in candidates[:30]))
            return

        created = self._raise_tickets(candidates, working_days)
        self.stdout.write(self.style.SUCCESS(
            f'Offboarding check {window_start}..{window_end}: candidates={len(candidates)}, '
            f'new investigation tickets={created}.'))

    def _raise_tickets(self, candidates, working_days) -> int:
        from django.contrib.auth.models import User
        from core.models import OmniTask
        unami = User.objects.filter(email__iexact=UNAMI_EMAIL).first()
        cfo = User.objects.filter(email__iexact=CFO_EMAIL).first()
        assigner = cfo or User.objects.filter(is_superuser=True).order_by('id').first()
        assignee = unami or cfo or assigner
        if assigner is None or assignee is None:
            self.stderr.write(self.style.ERROR('No assigner/assignee user found — cannot raise tickets.'))
            return 0
        span = f"{min(working_days).strftime('%d %b')}–{max(working_days).strftime('%d %b %Y')}"
        created = 0
        made = []
        for emp in candidates:
            title = f'Investigate: no Time Doctor tracking — {emp.full_name} ({len(working_days)} working days)'
            # one OPEN investigation ticket per person
            if OmniTask.objects.filter(
                    assignee=assignee, source='workforce_offboarding', title=title,
                    status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]).exists():
                continue
            OmniTask.objects.create(
                assigner=assigner, assignee=assignee, title=title,
                body=(f'{emp.full_name} ({emp.job_title or "—"}, {emp.department or "—"}) has no Time '
                      f'Doctor tracking on any working day {span}, and no approved or pending leave '
                      f'covering those days.\n\n'
                      f'ACTION: investigate the reason with the employee and their manager. This is a '
                      f'review only — do NOT disable any account, touch payroll, or take disciplinary '
                      f'action from this ticket. Terminations require CFO authorisation. If the tracker '
                      f'was simply broken, help them raise an IT ticket and close this off.'),
                priority=OmniTask.Priority.HIGH, source='workforce_offboarding',
                week_of=min(working_days) - datetime.timedelta(days=min(working_days).weekday()))
            created += 1
            made.append(emp.full_name)
        if made and cfo:
            self._inform_cfo(made, span)
        return created

    def _inform_cfo(self, names, span):
        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        body = (f'HR investigation tickets were raised (assigned to Unami) for staff with no Time '
                f'Doctor tracking across the working days {span}, and no leave on record:\n\n• '
                + '\n• '.join(names)
                + '\n\nThese are review-only tickets — no account, payroll or disciplinary action is '
                  'taken automatically. You are informed as CFO.')
        try:
            EmailMultiAlternatives(subject=f'Workforce: {len(names)} no-tracking investigation ticket(s) raised',
                                   body=body, from_email=from_email, to=[CFO_EMAIL]).send()
        except Exception as exc:    # noqa: BLE001
            self.stderr.write(self.style.WARNING(f'CFO inform email failed: {exc}'))

    def _alert_cfo_outage(self, why, dry):
        if dry:
            return
        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))
        try:
            EmailMultiAlternatives(
                subject='Workforce offboarding check HELD — Time Doctor data looks wrong',
                body=f'No investigation tickets were raised.\n\nReason: {why}\n\n'
                     'Check the Time Doctor token / service, then re-run.',
                from_email=from_email, to=[CFO_EMAIL]).send()
        except Exception:    # noqa: BLE001
            pass
