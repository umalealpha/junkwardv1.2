"""
hris/management/commands/send_daily_brief.py

Emails each employee their Daily Brief for a day (default: yesterday), and
stores a WorkdayJustification row per employee so month-end can report
justified vs unjustified hours.

  python manage.py send_daily_brief                 # yesterday, email on
  python manage.py send_daily_brief --date 2026-07-13
  python manage.py send_daily_brief --dry-run       # build + store nothing, print
  python manage.py send_daily_brief --no-email      # store rows, no email
  python manage.py send_daily_brief --limit 5       # first 5 profiles (pilot)

Guarded by settings.WORKFORCE_BRIEF_ENABLED (default False) so it does nothing
until the CFO switches it on — pass --force to run regardless (testing).

Tracked hours are read LIVE from Time Doctor for the Botswana day, falling back
to the day's stored snapshot when the API is unreachable, and resolved through
the employee's CONFIRMED Time Doctor account link. No Time Doctor data for the
day → the brief shows 'awaiting data' and no shortfall is flagged.

Why live and not the stored snapshot (bug c82def7f, 9-Sep-2026): Time Doctor
BACK-FILLS time a machine buffered while it was offline, so the stored figure
for a day is a FLOOR, not the truth. The snapshot pull runs 06:30 UTC; this
command ran 07:05 UTC and wrote that floor into WorkdayJustification.
tracked_hours — the number the employee sees on the portal and the number that
docks leave from 1-Sep — while send_morning_brief (07:00 UTC) read Time Doctor
live and emailed the higher, correct figure. A power cut took one employee's
machine offline mid-morning on 8-Sep; the snapshot held 1.74 h, live held
3.24 h, and she was shown both and reported it. Reading live here makes the
permanent record agree with the brief and with Time Doctor itself.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from hris.models import LeaveRequest, WorkdayJustification
from hris import workforce, workforce_brief
from hris.workforce_roles import is_manager_hours_profile


def _settle_slot_count(as_of: datetime.date) -> int:
    """How many settle pulls the day's snapshot holds (slots 0300/0400/0830).

    This command writes the PERMANENT attendance record that month-end hours, the
    Monthly Manager Return, the performance panel and (from 1-Sep-2026) leave
    docking all read. It must therefore not stamp a verdict off half-arrived Time
    Doctor data — the exact failure hris.people_data_guard exists to stop, which
    every OTHER people-facing job already passes through and this one did not.
    Returns 0 on any problem, which is treated as "not settled" (fail-safe).
    """
    try:
        from integrations.models import TimeDoctorDailySnapshot
        snap = (TimeDoctorDailySnapshot.objects
                .filter(as_of=as_of).order_by('-updated_at').first())
        return len(snap.settle_samples or {}) if snap else 0
    except Exception:    # noqa: BLE001
        return 0


def _approved_leave_for(profile, as_of: datetime.date):
    """The approved LeaveRequest covering `as_of`, if any."""
    try:
        return (LeaveRequest.objects
                .filter(profile=profile, status=LeaveRequest.Status.APPROVED,
                        start_date__lte=as_of, end_date__gte=as_of)
                .first())
    except Exception:    # noqa: BLE001
        return None


class Command(BaseCommand):
    help = "Email each employee their Daily Brief and record the day's hours status."

    def add_arguments(self, parser):
        parser.add_argument('--date', dest='date', help='Day to brief, YYYY-MM-DD (default: yesterday).')
        parser.add_argument('--dry-run', action='store_true', help='Build + print, store/send nothing.')
        parser.add_argument('--no-email', action='store_true', help='Store rows but do not email.')
        parser.add_argument('--limit', type=int, default=0, help='Only the first N profiles (pilot).')
        parser.add_argument('--force', action='store_true', help='Run even if WORKFORCE_BRIEF_ENABLED is off.')

    def handle(self, *args, **opts):
        from hris.models import WorkforceBriefSetting
        on = WorkforceBriefSetting.is_enabled() or getattr(settings, 'WORKFORCE_BRIEF_ENABLED', False)
        if not on and not opts.get('force'):
            self.stdout.write(self.style.WARNING(
                'SKIPPED: the Workforce Brief switch is OFF. Turn it on from the omni '
                'Time Doctor page (or pass --force) to send.'))
            return

        if opts.get('date'):
            as_of = datetime.datetime.strptime(opts['date'], '%Y-%m-%d').date()
        else:
            as_of = timezone.localtime().date() - datetime.timedelta(days=1)

        off_dates = workforce_brief.holiday_off_dates()
        from integrations.models import TimeDoctorDailySnapshot
        day_snap = TimeDoctorDailySnapshot.objects.filter(as_of=as_of).order_by('-updated_at').first()
        from_email = (getattr(settings, 'OMNI_FROM_EMAIL', '')
                      or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>'))

        # Tracking-eligible payroll staff only (agents / excluded roles removed
        # via hris.eligibility) — iterating every non-terminated profile nagged
        # agents who never track (Fable review 2026-07-14).
        from hris import eligibility
        profiles = eligibility.tracking_profiles()
        if opts.get('limit'):
            profiles = profiles[:int(opts['limit'])]

        # Hours resolved through the CONFIRMED Time Doctor account link
        # (integrations.td_matching.hours_by_employee) — NEVER by display name, so
        # a person whose Time Doctor name differs from their HR name is recorded
        # with their real hours instead of a false 0/absent. This record feeds
        # month-end and the 1-Sept leave docking, so the identity must be exact.
        # LIVE first: a stored snapshot is a FLOOR because Time Doctor back-fills
        # offline time after the 06:30 pull (bug c82def7f). integrations.td_live
        # owns the choice and refuses a live read that looks incomplete, so this
        # command and reconcile_workday_records can never disagree again.
        from integrations.td_live import rows_for_day
        from integrations.td_matching import hours_by_employee
        td_rows, td_source = rows_for_day(as_of, day_snap.payload if day_snap else [])
        if td_source != 'live':
            self.stderr.write(self.style.WARNING(
                f'Time Doctor hours for {as_of} came from the {td_source} — they may '
                f'under-report time that synced late.'))
        emp_hours = hours_by_employee(td_rows, [p.employee for p in profiles])

        # Is the day's Time Doctor data final? Until all three pulls are in, a
        # zero can still be a late desktop upload rather than an absence, so we
        # must not brand the day UNJUSTIFIED in the permanent record.
        from hris import people_data_guard as pdg
        slots = _settle_slot_count(as_of)
        settled = slots >= pdg.SETTLED_MIN_SLOTS
        if not settled:
            self.stderr.write(self.style.WARNING(
                f'Time Doctor data for {as_of} is not settled yet '
                f'({slots}/{pdg.SETTLED_MIN_SLOTS} pulls in) — shortfalls will be '
                f'recorded as "awaiting response", not "unjustified".'))

        # What Omni already holds about each person for this day — leave, a client
        # visit, a public holiday, an existing verdict. Read ONCE for the whole
        # batch (five queries, not five per head).
        facts = pdg.facts_for_profiles([p.id for p in profiles], as_of)
        held_by_facts: list = []

        sent = stored = skipped = 0
        for p in profiles:
            emp = getattr(p, 'employee', None)
            email = (getattr(emp, 'email', '') or '').strip()
            name  = (getattr(emp, 'full_name', '') or '').strip()
            _th = emp_hours.get(p.employee_id)
            tracked = Decimal(str(_th)) if _th is not None else None
            # THE FLOOR RULE, applied BEFORE the brief is built (Fable review
            # 2026-09-09, finding 2c). A re-run must never write a figure below
            # one already on the record — and the floor has to be taken here,
            # not after, or `status`, `shortfall` and the emailed badge would all
            # still be derived from the lower figure while the hours written are
            # the higher one. That produced a visible contradiction: 7.00 h on
            # the record, stamped UNJUSTIFIED, with a red "below target" badge.
            existing = WorkdayJustification.objects.filter(profile=p, work_date=as_of).first()
            if (tracked is not None and existing is not None
                    and Decimal(existing.tracked_hours or 0) > tracked):
                tracked = Decimal(existing.tracked_hours)
            # Approved leave covering the day counts as justified hours up front
            # — never email someone on approved leave to "explain yourself".
            leave = _approved_leave_for(p, as_of)
            justified = None
            if leave is not None and tracked is not None:
                mgr = is_manager_hours_profile(p)
                justified = workforce.shortfall_hours(
                    workforce.required_hours_for_date(as_of, off_dates, is_manager=mgr),
                    Decimal(tracked or 0))
            brief = workforce_brief.build_employee_brief(p, as_of, tracked, off_dates,
                                                         justified_hours=justified)

            if opts.get('dry_run'):
                self.stdout.write(f'[dry] {name or p.id}: req={brief["required"]} '
                                  f'tracked={brief["tracked"]} status={brief["status"]}')
                continue

            # Record the day's status for month-end reporting (idempotent per day).
            if tracked is not None:
                st_map = {'met': WorkdayJustification.Status.MET,
                          'not_required': WorkdayJustification.Status.NOT_REQUIRED,
                          'justified': WorkdayJustification.Status.JUSTIFIED,
                          'unjustified': WorkdayJustification.Status.UNJUSTIFIED}
                if existing and existing.responded_at:
                    # Employee already justified this day — refresh the hours but
                    # NEVER overwrite their response/status on a re-run (Fable review).
                    existing.required_hours = brief['required']
                    existing.tracked_hours = brief['tracked']
                    existing.save(update_fields=['required_hours', 'tracked_hours', 'updated_at'])
                else:
                    st = st_map.get(brief['status'], WorkdayJustification.Status.PENDING)
                    # Unsettled data cannot convict anyone. PENDING keeps the
                    # hours on record and keeps the day off every "unexplained
                    # absence" count until the figure is final.
                    if st == WorkdayJustification.Status.UNJUSTIFIED and not settled:
                        st = WorkdayJustification.Status.PENDING
                    # FACTS gate (CFO 2026-08-03) — never write "unexplained
                    # absence" for a day Omni already holds an explanation for.
                    # This is the record every downstream count reads, so a wrong
                    # verdict here becomes a wrong performance panel, a wrong
                    # Monthly Manager Return and (from 1-Sep) wrongly docked leave.
                    if st == WorkdayJustification.Status.UNJUSTIFIED:
                        ok, why = pdg.facts_gate(facts.get(p.id), as_of)
                        if not ok:
                            st = WorkdayJustification.Status.PENDING
                            held_by_facts.append(f'{name or p.id}: {why}')
                    defaults = {'required_hours': brief['required'],
                                'tracked_hours': brief['tracked'],
                                'status': st}
                    if brief['status'] == 'justified' and leave is not None:
                        defaults.update({'reason': WorkdayJustification.Reason.ON_LEAVE,
                                         'linked_leave': leave,
                                         'justified_hours': justified or Decimal('0')})
                    WorkdayJustification.objects.update_or_create(
                        profile=p, work_date=as_of, defaults=defaults)
                stored += 1

            if not email or opts.get('no_email'):
                skipped += 1
                continue
            try:
                msg = EmailMultiAlternatives(
                    subject=f'Your Daily Brief — {as_of}',
                    body='(See the HTML version of this email.)',
                    from_email=from_email, to=[email])
                msg.attach_alternative(workforce_brief.build_brief_html(brief), 'text/html')
                msg.send()
                sent += 1
            except Exception as exc:    # noqa: BLE001
                skipped += 1
                self.stderr.write(self.style.WARNING(f'email failed for {name}: {exc}'))

        # Say what was withheld and why. A guard that silently holds names looks
        # identical to a quiet day (eligibility.on_leave_names lesson).
        for line in held_by_facts:
            self.stdout.write(f'  not marked unexplained — {line}')

        self.stdout.write(self.style.SUCCESS(
            f'Daily brief {as_of}: emailed={sent}, rows stored={stored}, skipped={skipped}, '
            f'explained-by-record={len(held_by_facts)}.'))
