"""Nightly sweep: close the Omni login of anyone whose last working day has passed.

CFO directive 2026-09-18. The sweep exists because the hook in
payroll.archive_service.terminate_employee only covers exits recorded THROUGH
that one path. Three real cases slip past it, and all three were found live:

  * a termination recorded ON the last working day (deliberately left open
    until the end of that day — the CFO's choice, so somebody finishing their
    handover is not locked out at 10am);
  * a status changed straight to 'terminated' on the employee record without
    going through Terminate — how most of the fourteen existing leavers got
    there;
  * anyone who left before this feature existed. Seven of them on 2026-09-18,
    the oldest 84 days past their last day.

Run it nightly. It is idempotent: a closed account is skipped, so a re-run
does nothing and a missed night is caught the next one.

    python manage.py close_leaver_access --dry-run   # report only, changes nothing
    python manage.py close_leaver_access             # close them
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from payroll.models import Employee
from payroll.offboard_access import close_omni_access, should_close


class Command(BaseCommand):
    help = "Close the Omni login of employees whose last working day has passed."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='List who WOULD be closed and change nothing.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        today = timezone.localdate()

        # Widest sensible queryset, then let should_close() be the single place
        # the rule lives — the command must never grow its own copy of it.
        candidates = (Employee.objects
                      .filter(termination_date__isnull=False,
                              termination_date__lt=today,
                              user__isnull=False,
                              user__is_active=True)
                      .select_related('user')
                      .order_by('termination_date'))

        closed = kept = superusers = 0
        for emp in candidates:
            if not should_close(emp):
                if emp.keep_access_after_exit:
                    kept += 1
                    self.stdout.write(
                        f'  KEEP  {emp.full_name} ({emp.email}) — marked "keep access after exit"')
                continue
            res = close_omni_access(emp, actor=None, dry_run=dry)
            if res['skipped_superuser']:
                superusers += 1
                self.stdout.write(self.style.WARNING(
                    f'  SKIP  {emp.full_name} ({emp.email}) — this is a system administrator '
                    f'account and is never closed automatically. A person must review it.'))
                continue
            closed += 1
            verb = 'WOULD CLOSE' if dry else 'CLOSED'
            self.stdout.write(
                f'  {verb}  {emp.full_name} ({emp.email}) — last day {emp.termination_date}, '
                f'{res["device_sessions_revoked"]} phone session(s), '
                f'{res["browser_tokens_cleared"]} browser session(s)')

        # Unami Butale, 2026-09-18: "there seems to be a mismatch with data —
        # the employee data shows the true position, the settings termination
        # shows a different position." Thirteen accounts on prod had the Users
        # screen and the actual sign-in disagreeing. The sync in
        # UserProfile.save() stops NEW divergence; the rows that already
        # diverge are reported here rather than changed, because the safe
        # direction is obvious (close a door) and the other one is not
        # (re-opening a login somebody closed deliberately is not ours to do).
        self._report_mismatches()

        head = 'Would close' if dry else 'Closed'
        self.stdout.write(self.style.SUCCESS(
            f'{head} {closed} leaver account(s). Kept open {kept} on the contractor tick. '
            f'{superusers} administrator account(s) left for a human.'))

    def _report_mismatches(self):
        from core.models import UserProfile
        rows = [(p, p.user) for p in UserProfile.objects.select_related('user')
                if p.user is not None and p.is_active != p.user.is_active]
        if not rows:
            return
        self.stdout.write(self.style.WARNING(
            f'{len(rows)} account(s) where the Users screen and the actual sign-in '
            f'disagree — a person should look at these:'))
        for prof, user in rows:
            if user.is_active:
                self.stdout.write(self.style.ERROR(
                    f'  OPEN DOOR  {user.email or user.username} — switched off on '
                    f'screen but CAN STILL SIGN IN'))
            else:
                self.stdout.write(
                    f'  stale      {user.email or user.username} — shown as active on '
                    f'screen but cannot sign in')
