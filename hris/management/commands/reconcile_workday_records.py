"""reconcile_workday_records — correct the permanent day-record against the FINAL
Time Doctor figure (TD-RECON-01; Time Doctor control audit, 3-Sep-2026, finding 1).

Why this exists. `send_daily_brief` writes each person's WorkdayJustification ONCE
at 09:05 and nothing ever revisits it. When the true figure later changes — a
second machine folded in, a late upload, a repaired identity link — the person's
own screen corrects itself but their permanent record does not. That record feeds
the monthly automatic feedback (7th), the performance panel managers rate against,
and leave docking. On 2-Sep Natasha Nthite's record read 0.0 h "explained" while
her real figure was 5.73 h; on 1-Sep three more people were recorded at 0 and asked
to explain hours Omni had lost. Four people in two days confessed to a shortfall
that was ours.

What it does, per day in the window:
  * re-reads the day's FINAL snapshot through the confirmed identity layer
    (integrations.td_matching.hours_by_employee — folds second machines, never a
    name guess);
  * where the stored tracked_hours differ by more than --tolerance, rewrites the
    hours and re-derives the day with workforce.classify_day;
  * a shortfall that has VANISHED (now met / not required) clears the day —
    'explained', 'pending' and 'unjustified' all become 'met'; the person's own
    explanation stays on the row as history;
  * it NEVER moves a day towards a worse status. Convicting is the daily brief's
    job, behind its settle + facts gates. This command can only correct in the
    person's favour or update hours on a day that is still short;
  * approved-leave days ('justified') keep their status — leave is a fact, not a
    figure;
  * every change is one AuditLog row (old/new hours + status) via AuditableMixin;
  * a false shortfall that clears sends the person ONE plain line: "Your hours for
    <day> were corrected to X h — no action needed." (skipped with --no-email);
  * if an unpaid Time Doctor deduction (td_deduct) already covers a day that now
    shows real hours, it is NOT reversed here — money is a human call — but the
    conflict is emailed to the CFO by name so it cannot sit silently.

Dry-run by default. --apply writes.

  python manage.py reconcile_workday_records                 # last 3 completed days, dry
  python manage.py reconcile_workday_records --days 30 --apply
  python manage.py reconcile_workday_records --date 2026-09-02 --apply --no-email
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

log = logging.getLogger(__name__)

TOLERANCE_HOURS = Decimal('0.10')
# Statuses this command may clear when the shortfall has vanished. Anything else
# (met / not_required / justified) is either already fine or a leave fact.
CLEARABLE = ('explained', 'pending', 'unjustified')


def _flag_docked_conflict(out, w, emp, day, hours: Decimal, td_type) -> None:
    """Flag an unpaid Time Doctor deduction sitting on a day that shows work.

    Never reverses it — money is a human call — but it must never sit silently
    either, so the caller emails the CFO. Called for a CORRECTED row and for a
    REFUSED one: a row we refuse to lower still has real hours on the record,
    and if a deduction covers that day it is the same conflict.
    """
    from hris.models import LeaveRequest
    if td_type is None or hours <= 0:
        return
    docked = (LeaveRequest.objects
              .filter(profile=w.profile, leave_type=td_type,
                      start_date__lte=day, end_date__gte=day)
              .exclude(status__in=[LeaveRequest.Status.CANCELLED,
                                   LeaveRequest.Status.REFUSED]))
    for lr in docked:
        out['docked_conflicts'].append((emp, day, hours, lr))


def reconcile_day(day: datetime.date, *, apply: bool, tolerance: Decimal = TOLERANCE_HOURS) -> dict:
    """Pure-ish worker: returns {'corrected': [...], 'cleared': [...],
    'docked_conflicts': [...], 'skipped_no_snapshot': bool}. Each corrected item is
    (profile, employee, old_hours, new_hours, old_status, new_status)."""
    from hris import eligibility, workforce
    from hris.models import LeaveType, WorkdayJustification as WJ
    from integrations.models import TimeDoctorDailySnapshot
    from integrations.td_matching import hours_by_employee

    out = {'corrected': [], 'cleared': [], 'docked_conflicts': [],
           'skipped_no_snapshot': False, 'refused_downward': []}
    snap = TimeDoctorDailySnapshot.objects.filter(as_of=day).order_by('-updated_at').first()
    if snap is None or not snap.payload:
        out['skipped_no_snapshot'] = True
        return out

    profiles = list(eligibility.tracking_profiles())
    # Read Time Doctor LIVE, not the stored snapshot. The snapshot freezes at the
    # 06:30 pull and Time Doctor back-fills offline time afterwards, so re-reading
    # it here re-imposed a figure that was already known to be too low — the exact
    # harm this command exists to undo (bug c82def7f: the corrected 3.24 h was
    # pulled back to 1.74 h 30 minutes after the daily brief wrote it, every day).
    from integrations.td_live import rows_for_day
    td_rows, td_source = rows_for_day(day, snap.payload)
    out['td_source'] = td_source
    emp_hours = hours_by_employee(td_rows, [p.employee for p in profiles])
    td_type = LeaveType.objects.filter(code='td_deduct').first()

    rows = (WJ.objects.filter(work_date=day, profile__in=profiles)
            .select_related('profile__employee'))
    for w in rows:
        emp = w.profile.employee
        live = emp_hours.get(emp.id)
        if live is None:                       # no matched account — nothing to compare
            continue
        live_h = Decimal(str(live)).quantize(Decimal('0.01'))
        old_h = Decimal(w.tracked_hours or 0)
        if abs(live_h - old_h) <= tolerance:
            continue

        # THE FLOOR RULE (CFO 2026-09-09) — hours NEVER go down from a Time
        # Doctor read. Time Doctor only ever ADDS time to a past day (an offline
        # machine uploading its buffer); it does not take work away. So a figure
        # BELOW what is already recorded means this read is incomplete, not that
        # the person worked less. The old code rewrote in either direction — its
        # guard protected the STATUS ("never convicts") but not the HOURS — and
        # that is what made the same employee report the same lost hours twice.
        # A genuine reduction is a human decision, never a cron's.
        if live_h < old_h:
            out['refused_downward'].append((w.profile, emp, old_h, live_h))
            # Employee NUMBER, never a name — an app log is a wider audience
            # than the screen, and the number is the stable identifier.
            log.warning('reconcile %s: REFUSED to lower employee %s from %sh to '
                        '%sh (hours are a floor; source=%s)',
                        day, emp.employee_number or emp.pk, old_h, live_h, td_source)
            # Still check for an unpaid deduction on this day BEFORE skipping.
            # A refused row whose record shows real hours and which already
            # carries a td_deduct is precisely the conflict the CFO email exists
            # for — returning early here would hide the one case that costs
            # someone leave (Fable review 2026-09-09, finding C).
            _flag_docked_conflict(out, w, emp, day, old_h, td_type)
            continue

        old_status = w.status
        new_status = old_status
        if old_status in CLEARABLE:
            cls = workforce.classify_day(Decimal(w.required_hours or 0), live_h,
                                         Decimal(w.justified_hours or 0))
            if cls == 'met':
                new_status = WJ.Status.MET
            elif cls == 'not_required':
                new_status = WJ.Status.NOT_REQUIRED
            # 'justified' / 'unjustified' from the classifier: leave the status as
            # the daily brief set it — never convict from here.

        item = (w.profile, emp, old_h, live_h, old_status, new_status)
        out['corrected'].append(item)
        if new_status != old_status:
            out['cleared'].append(item)

        # A deduction already sitting on a day that now shows work: flag, never
        # reverse. Money is a human call.
        _flag_docked_conflict(out, w, emp, day, live_h, td_type)

        if apply:
            w.tracked_hours = live_h
            w.status = new_status
            w.save(update_fields=['tracked_hours', 'status', 'updated_at'],
                   audit_description=(f'Reconciled from final Time Doctor snapshot for {day}: '
                                      f'{old_h}h -> {live_h}h; status {old_status} -> {new_status}'))
    return out


def _correction_html(name: str, day: datetime.date, hours: Decimal) -> str:
    from core.notifications import no_reply_banner
    first = (name or 'there').split()[0]
    return (
        '<div style="font-family:\'Book Antiqua\',Palatino,Georgia,serif;max-width:640px;margin:16px auto;'
        'background:#fff;border-radius:14px;overflow:hidden;border:1px solid #E5E7EB">'
        f'{no_reply_banner()}'
        '<div style="background:#0D1B2A;padding:18px 22px"><div style="color:#F4A623;font-size:20px;font-weight:700">'
        'Your hours were corrected</div></div>'
        f'<div style="padding:18px 22px;color:#1F2937;font-size:15px;line-height:1.5">'
        f'<p style="margin:0 0 10px">Hi {first},</p>'
        f'<p style="margin:0 0 10px">Your Time Doctor hours for <b>{day:%A %d %B}</b> have been corrected to '
        f'<b>{hours} h</b>. The earlier figure was wrong on our side, not yours. No action is needed from you, '
        'and nothing about this stays on your record.</p>'
        '<p style="margin:0;color:#6B7280;font-size:13px">If a day still looks wrong to you, please report it on '
        'Omni — that is exactly how this one was found. Thank you.</p></div></div>'
    )


class Command(BaseCommand):
    help = 'Correct WorkdayJustification hours/status against the final Time Doctor snapshot (dry-run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=3, help='Completed days to reconcile (default 3).')
        parser.add_argument('--date', help='One specific day YYYY-MM-DD (overrides --days).')
        parser.add_argument('--apply', action='store_true', help='Write corrections. Default is dry-run.')
        parser.add_argument('--no-email', action='store_true', help='Do not email people whose shortfall cleared.')
        parser.add_argument('--tolerance', type=float, default=float(TOLERANCE_HOURS))

    def handle(self, *args, **opts):
        today = timezone.localtime().date()
        if opts.get('date'):
            days = [datetime.date.fromisoformat(opts['date'])]
        else:
            days = [today - datetime.timedelta(days=i) for i in range(1, int(opts['days']) + 1)]
        apply = bool(opts['apply'])
        tol = Decimal(str(opts['tolerance']))
        mode = 'APPLY' if apply else 'DRY-RUN'

        total_corr = total_clear = emailed = total_refused = 0
        conflicts = []
        for day in sorted(days):
            res = reconcile_day(day, apply=apply, tolerance=tol)
            if res['skipped_no_snapshot']:
                self.stdout.write(f'{day}: no snapshot — skipped')
                continue
            for prof, emp, oh, nh, os_, ns in res['corrected']:
                flag = ' CLEARED' if ns != os_ else ''
                self.stdout.write(f'{day}: {emp.full_name}: {oh}h -> {nh}h  {os_} -> {ns}{flag}')
            # A refusal is not a no-op — it means Time Doctor is under-reporting
            # a day we already have better figures for. Say so where the corrected
            # rows are said, so it is visible in the cron log instead of only in
            # the app log (Fable review 2026-09-09, finding D). No email: on a
            # stale-snapshot day this fires per person and would be daily noise —
            # a refusal that also carries a deduction is escalated as a conflict
            # below, which is the case that actually costs someone leave.
            for prof, emp, oh, nh in res['refused_downward']:
                self.stdout.write(self.style.WARNING(
                    f'{day}: {emp.full_name}: REFUSED {oh}h -> {nh}h '
                    f'(hours are a floor — kept {oh}h)'))
            total_refused += len(res['refused_downward'])
            total_corr += len(res['corrected'])
            total_clear += len(res['cleared'])
            conflicts.extend(res['docked_conflicts'])

            if apply and not opts.get('no_email'):
                for prof, emp, oh, nh, os_, ns in res['cleared']:
                    email = (getattr(emp, 'email', '') or '').strip()
                    if not email:
                        continue
                    try:
                        from core.notifications import send_html_with_cfo_cc
                        send_html_with_cfo_cc(
                            f'Your Time Doctor hours for {day:%d %b} were corrected — no action needed',
                            _correction_html(emp.full_name, day, nh), [email],
                            cc_cfo=False, no_reply=True)
                        emailed += 1
                    except Exception:      # noqa: BLE001 — a mail failure must not stop the correction
                        log.exception('reconcile: correction email failed for %s', emp.id)

        if conflicts:
            lines = ''.join(
                f'<li><b>{emp.full_name}</b> — {day}: record now shows {h} h, but an unpaid Time Doctor '
                f'deduction ({lr.start_date}→{lr.end_date}, {lr.days}d, {lr.status}) covers it.</li>'
                for emp, day, h, lr in conflicts)
            self.stdout.write(self.style.ERROR(f'{len(conflicts)} DOCKED-DAY CONFLICT(S) — not reversed, CFO notified'))
            if apply:
                try:
                    from django.conf import settings
                    from core.notifications import send_html_with_cfo_cc
                    send_html_with_cfo_cc(
                        f'Time Doctor: {len(conflicts)} deduction(s) sit on days that now show hours',
                        ('<div style="font-family:\'Book Antiqua\',Georgia,serif;font-size:15px;color:#1F2937">'
                         '<p>The nightly record correction found real hours on days that already carry an unpaid '
                         'Time Doctor deduction. Nothing has been reversed — that is your call.</p>'
                         f'<ul>{lines}</ul></div>'),
                        [getattr(settings, 'MANDATORY_CFO_CC', 'excoboard@alphadirect.co.bw')],
                        cc_cfo=False, no_reply=True)
                except Exception:      # noqa: BLE001
                    log.exception('reconcile: CFO conflict email failed')

        self.stdout.write(self.style.SUCCESS(
            f'[{mode}] {len(days)} day(s): {total_corr} record(s) corrected, {total_clear} false '
            f'shortfall(s) cleared, {emailed} person(s) told, {len(conflicts)} docked-day conflict(s), '
            f'{total_refused} downward read(s) refused.'))
