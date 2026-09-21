"""Back-fill attendance days for leave that was approved before hris.leave_backfill existed.

One-off (safe to re-run — hris.leave_backfill skips days that are already MET /
NOT_REQUIRED / JUSTIFIED, so a second pass changes nothing).

    python manage.py backfill_leave_workdays --dry-run
    python manage.py backfill_leave_workdays
    python manage.py backfill_leave_workdays --since 2026-07-01
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand

from hris.leave_backfill import backfill_workdays_for_leave
from hris.models import LeaveRequest


class Command(BaseCommand):
    help = 'Mark the attendance days covered by already-APPROVED leave as justified-on-leave.'

    def add_arguments(self, parser):
        parser.add_argument('--since', help='Only leave starting on/after this date (YYYY-MM-DD).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        qs = (LeaveRequest.objects
              .filter(status=LeaveRequest.Status.APPROVED)
              .select_related('profile__employee')
              .order_by('start_date'))
        if opts.get('since'):
            qs = qs.filter(start_date__gte=datetime.date.fromisoformat(opts['since']))

        from hris.models import WorkdayJustification
        keep = {WorkdayJustification.Status.MET,
                WorkdayJustification.Status.NOT_REQUIRED,
                WorkdayJustification.Status.JUSTIFIED}

        total = touched = 0
        for lr in qs:
            name = getattr(getattr(lr.profile, 'employee', None), 'full_name', '') or str(lr.pk)
            if opts.get('dry_run'):
                open_rows = (WorkdayJustification.objects
                             .filter(profile_id=lr.profile_id,
                                     work_date__gte=lr.start_date,
                                     work_date__lte=lr.end_date)
                             .exclude(status__in=keep)
                             .exclude(required_hours__lte=0)
                             .count())
                if open_rows:
                    self.stdout.write(f'[dry] {name}: {lr.start_date}→{lr.end_date} '
                                      f'— {open_rows} day(s) would be cleared')
                    total += open_rows
                    touched += 1
                continue

            n = backfill_workdays_for_leave(lr)
            if n:
                self.stdout.write(f'{name}: {lr.start_date}→{lr.end_date} — {n} day(s) cleared')
                total += n
                touched += 1

        verb = 'would clear' if opts.get('dry_run') else 'cleared'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {total} day(s) across {touched} leave request(s) '
            f'(scanned {qs.count()} approved request(s)).'))
