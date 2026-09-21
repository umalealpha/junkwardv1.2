"""enforce_td_deductions — the 4 pm Time Doctor deduction, inside the code and
behind the guard (TD-ENFORCE-01; Time Doctor control audit 3-Sep-2026, finding 2).

Ported from /etc/alpha-finance/td_enforce_20260903.py — the hand-run server
script that created the first two unpaid deductions (CFO directive 2026-09-03:
"no Time Doctor tracking and no explanation by the 4:00 pm cut-off"). That
script matched people by fuzzy name tokens, consulted none of the guards, ran
outside the repository and recorded no user. The business rule is kept exactly;
the process is what changes.

Who is considered. ONLY the people the morning exceptions report PUBLISHED as
"did not track" for the day (snapshot.reported_no_track) — the chased set, which
by construction already survived the settle gate, the facts gate and the
DeepSeek/Gemini check. Identity is the confirmed Time Doctor ↔ payroll link,
never a name.

Every one of these must hold, or the person is SKIPPED with the reason printed:
  1. enforcement is active for the day (leave_accountability, 1-Sep-2026 on);
  2. the day's snapshot is fully settled (all settle pulls in);
  3. the FINAL folded figure for the day is still dark (< 0.10 h) — a second
     machine or a late upload that has since arrived ends it here;
  4. the permanent record (WorkdayJustification) exists and says UNJUSTIFIED —
     no explanation, no leave, not pending, not already met;
  5. the people-data guard does not HOLD the person for the day;
  6. no other leave (any type, pending or approved) covers the day;
  7. no non-cancelled td_deduct already covers the day (idempotent);
  8. the person was actually EMPLOYED on the day (Kakale f8ded9e1, 17-Sep-2026);
  9. the account existed in Time Doctor's user list for the day — an account
     that does not exist cannot track, so its zero is IT's problem, not the
     person's.

Fail direction: QUIET. This is an accusation that docks pay — when anything is
uncertain the person is skipped, never docked. (The opposite of the orphan-hours
gate, on purpose; see hris.people_data_guard.)

What it creates: a PENDING unpaid td_deduct LeaveRequest for each contiguous run
of eligible days, addressed to the person's leave approver (default_leave_approver,
else the line manager), who gets the existing one-click Approve/Decline email.
The manager's decision is the human stamp on the money; --actor stamps the human
who ran the enforcement on the audit trail.

Dry-run by default. --apply writes.

  python manage.py enforce_td_deductions                       # yesterday, dry
  python manage.py enforce_td_deductions --apply --actor pganesharajah
  python manage.py enforce_td_deductions --date 2026-09-02 --days 2 --apply
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

log = logging.getLogger(__name__)

DARK_HOURS = Decimal('0.10')
REASON = ('Auto-applied: no Time Doctor tracking and no explanation by the 4:00 pm '
          'cut-off, {day} (CFO directive 2026-09-03). Guards passed: snapshot settled '
          '{slots} pull(s), final figure {hours} h, record unjustified, not held by the '
          'people-data guard, no other leave on the day.')


def settle_slot_count(snap) -> int:
    return len((getattr(snap, 'settle_samples', None) or {}))


def approver_for(profile):
    """The human who will approve the deduction: the chosen leave approver, else
    the line manager's login. None → nobody to approve → do not create."""
    u = getattr(profile, 'default_leave_approver', None)
    if u is not None and getattr(u, 'is_active', False):
        return u
    mgr = getattr(profile, 'manager', None)
    mu = getattr(mgr, 'user', None) if mgr is not None else None
    return mu if (mu is not None and getattr(mu, 'is_active', False)) else None


def employed_on(emp, day) -> tuple[bool, str]:
    """Was `emp` on the payroll on `day`? (ok, reason_when_not).

    Guard 8 — Kakale Botana, Omni bug f8ded9e1 (17-Sep-2026): Oratile
    Tlhomelang resigned on 14-Sep and Omni went on raising unpaid Time Doctor
    deductions against her afterwards. One reached the approver's inbox reading
    like any other and was approved in oversight, so the first seven guards all
    passed on a day she was not employed at all. Somebody who has left cannot
    track hours, so EVERY day after their exit is dark for ever: without this
    guard the chase never stops.

    Fail direction is the module's: QUIET. When the record is ambiguous —
    terminated with no date on file — nothing is docked.
    """
    term = getattr(emp, 'termination_date', None)
    if term and day > term:
        return False, f'left the company on {term} — not employed on this day'
    status = (getattr(emp, 'status', '') or '').lower()
    if status == 'terminated' and not term:
        return False, ('payroll status is "terminated" with no termination date '
                       'on file — cannot tell which days were worked')
    if status == 'suspended':
        return False, 'payroll status is "suspended" — not expected to track'
    hired = getattr(emp, 'hire_date', None)
    if hired and day < hired:
        return False, f'started on {hired} — not employed on this day'
    return True, ''


def eligible_days(profile, emp, uid, days, *, client=None) -> tuple[list, list]:
    """(eligible_dates, skip_reasons). Applies guards 1-9 per day."""
    from hris import eligibility, people_data_guard as pdg
    from hris.leave_accountability import enforcement_active
    from hris.models import LeaveRequest, LeaveType, WorkdayJustification as WJ
    from integrations.models import TimeDoctorDailySnapshot
    from integrations.td_matching import fold_uid, hours_by_employee

    td_type = LeaveType.objects.filter(code='td_deduct').first()
    ok, why = [], []
    for day in sorted(days):
        if not enforcement_active(day):
            why.append((day, 'enforcement not active for this date')); continue
        # Guard 8 — first, because a day outside someone's employment can never
        # become eligible no matter what the snapshot says (Kakale f8ded9e1).
        employed, not_employed_why = employed_on(emp, day)
        if not employed:
            why.append((day, not_employed_why)); continue
        snap = TimeDoctorDailySnapshot.objects.filter(as_of=day).order_by('-updated_at').first()
        if snap is None:
            why.append((day, 'no snapshot')); continue
        if str(uid) not in {str(u) for u in (snap.reported_no_track or [])}:
            why.append((day, 'not in the published did-not-track list for this day')); continue
        # Guard 9 — an account that is not on Time Doctor's own user list for the
        # day cannot have tracked; the zero belongs to IT, never the person.
        roster = {str(fold_uid(r.get('user_id'))) for r in (snap.payload or [])}
        if str(uid) not in roster:
            why.append((day, 'account not in Time Doctor\'s user list that day — an IT matter, not the person')); continue
        slots = settle_slot_count(snap)
        if slots < pdg.SETTLED_MIN_SLOTS:
            why.append((day, f'snapshot not settled ({slots}/{pdg.SETTLED_MIN_SLOTS} pulls)')); continue
        # Read Time Doctor LIVE, not the frozen snapshot. The snapshot is taken
        # at 06:30 and NEVER receives a late upload, so this guard's promise
        # that "a late upload ends it here" could not be kept: an offline
        # machine that uploaded its buffer at 10:00 still looked dark at 14:25
        # and a FULL DAY of unpaid leave was docked off a figure of 0.00 h
        # (bug c82def7f class; Fable review 2026-09-09, finding B).
        from integrations.td_live import rows_for_day
        td_rows, _td_source = rows_for_day(day, snap.payload or [])
        live = hours_by_employee(td_rows, [emp]).get(emp.id)
        if live is not None and Decimal(str(live)) >= DARK_HOURS:
            why.append((day, f'final figure shows {Decimal(str(live)):.2f} h — not dark')); continue
        wj = WJ.objects.filter(profile=profile, work_date=day).first()
        if wj is None:
            why.append((day, 'no permanent record written for the day')); continue
        # The permanent record is a FLOOR too. reconcile_workday_records raises
        # it when hours arrive late but deliberately does NOT clear a real
        # shortfall's status, so a record can legitimately read 3.20 h and still
        # say UNJUSTIFIED. Docking a full unpaid day against that is indefensible
        # — the hours are right there on the record.
        if Decimal(wj.tracked_hours or 0) >= DARK_HOURS:
            why.append((day, f'the permanent record already shows '
                             f'{Decimal(wj.tracked_hours or 0):.2f} h — not dark')); continue
        if wj.status != WJ.Status.UNJUSTIFIED:
            why.append((day, f'record is "{wj.status}", not unjustified')); continue
        try:
            held = pdg.held_uids([(uid, emp.full_name)], day, client=client)
        except Exception:      # noqa: BLE001 — guard broke → treat as held
            held = {uid}
        if str(uid) in {str(h) for h in held}:
            why.append((day, 'HELD by the people-data guard')); continue
        other_leave = (LeaveRequest.objects
                       .filter(profile=profile, start_date__lte=day, end_date__gte=day)
                       .exclude(status__in=[LeaveRequest.Status.CANCELLED, LeaveRequest.Status.REFUSED]))
        if td_type is not None and other_leave.filter(leave_type=td_type).exists():
            why.append((day, 'td_deduct already covers this day')); continue
        if other_leave.exists():
            why.append((day, 'another leave request covers this day')); continue
        ok.append(day)
    return ok, why


def runs(days):
    groups, cur = [], []
    for d in sorted(days):
        if cur and (d - cur[-1]).days != 1:
            groups.append(cur); cur = []
        cur.append(d)
    if cur:
        groups.append(cur)
    return groups


class Command(BaseCommand):
    help = 'Create PENDING unpaid Time Doctor deductions for published did-not-track days that passed every guard (dry-run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--date', help='Last day to consider, YYYY-MM-DD (default: yesterday).')
        parser.add_argument('--days', type=int, default=1, help='How many completed days back from --date (default 1).')
        parser.add_argument('--apply', action='store_true', help='Create the requests. Default is dry-run.')
        parser.add_argument('--actor', help='Username of the human running the enforcement (stamped on the audit trail).')

    def handle(self, *args, **opts):
        from django.contrib.auth import get_user_model
        from hris.models import HRISProfile, LeaveRequest, LeaveType
        from integrations.models import TimeDoctorDailySnapshot, TimeDoctorUserMap

        today = timezone.localtime().date()
        last = datetime.date.fromisoformat(opts['date']) if opts.get('date') else today - datetime.timedelta(days=1)
        if last >= today:
            raise CommandError('Refusing to enforce on today or the future — the day is not complete.')
        days = [last - datetime.timedelta(days=i) for i in range(int(opts['days']))]
        apply = bool(opts['apply'])
        actor = None
        if opts.get('actor'):
            actor = get_user_model().objects.filter(username=opts['actor']).first()
            if actor is None:
                raise CommandError(f'--actor {opts["actor"]!r}: no such user.')
        td_type = LeaveType.objects.filter(code='td_deduct').first()
        if td_type is None:
            raise CommandError('LeaveType td_deduct is not seeded — nothing to enforce with.')

        # The chased set: everyone published as did-not-track on any day in range.
        uids = set()
        for d in days:
            s = TimeDoctorDailySnapshot.objects.filter(as_of=d).first()
            uids |= {str(u) for u in ((s.reported_no_track if s else None) or [])}
        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(f'[{mode}] days {min(days)}..{max(days)} — {len(uids)} published did-not-track account(s)')

        created = skipped_people = 0
        for uid in sorted(uids):
            m = (TimeDoctorUserMap.objects.filter(td_user_id=uid, confirmed=True, employee__isnull=False)
                 .select_related('employee').first())
            if m is None:
                self.stdout.write(f'  {uid}: SKIP — no confirmed payroll link'); skipped_people += 1; continue
            emp = m.employee
            prof = HRISProfile.objects.filter(employee=emp).first()
            if prof is None:
                self.stdout.write(f'  {emp.full_name}: SKIP — no HRIS profile'); skipped_people += 1; continue
            ok, why = eligible_days(prof, emp, uid, days)
            for d, r in why:
                self.stdout.write(f'  {emp.full_name} {d}: skip — {r}')
            if not ok:
                skipped_people += 1; continue
            approver = approver_for(prof)
            if approver is None:
                self.stdout.write(f'  {emp.full_name}: SKIP — no active leave approver to send it to'); skipped_people += 1; continue
            for run in runs(ok):
                s, e = run[0], run[-1]
                snap = TimeDoctorDailySnapshot.objects.filter(as_of=e).first()
                label = f'{s}..{e} ({len(run)}d) -> {approver.username}'
                if not apply:
                    self.stdout.write(f'  {emp.full_name}: WOULD create td_deduct {label}'); continue
                lr = LeaveRequest(
                    profile=prof, leave_type=td_type, start_date=s, end_date=e,
                    days=Decimal(len(run)), reason_category='other',
                    reason=REASON.format(day=e.strftime('%a %d %b %Y'),
                                         slots=settle_slot_count(snap), hours='0.00'),
                    status=LeaveRequest.Status.PENDING, requested_approver=approver)
                lr.save(audit_user=actor,
                        audit_description=(f'enforce_td_deductions: PENDING unpaid Time Doctor deduction '
                                           f'{s}..{e} for {emp.full_name}; all guards passed; '
                                           f'approver {approver.username}; run by '
                                           f'{actor.username if actor else "system (cron)"}'))
                created += 1
                self.stdout.write(self.style.WARNING(f'  {emp.full_name}: CREATED td_deduct {label} id={lr.pk}'))
                try:
                    from core.notifications import notify_leave_pending_approval
                    notify_leave_pending_approval(lr)
                except Exception:      # noqa: BLE001 — the request exists; a mail failure must not roll it back
                    log.exception('enforce: approver notification failed for %s', lr.pk)

        self.stdout.write(self.style.SUCCESS(
            f'[{mode}] created={created} skipped_people={skipped_people}'))
