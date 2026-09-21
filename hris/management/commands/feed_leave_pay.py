"""hris/management/commands/feed_leave_pay.py - Leave Pay Feed (Build Spec B10, marker AUTO-LEAVEPAY).

Usage:  python manage.py feed_leave_pay --period 2026-10 [--company ADIC] [--dry-run]

Creates a PENDING payroll amendment batch. It never applies it, never posts a
journal and never moves money — the monthly close reviews and applies, and
dual sign-off still pays.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = 'Push approved leave pay into a pending payroll amendment batch.'

    def add_arguments(self, parser):
        parser.add_argument('--period', required=True,
                            help='Payroll period name, e.g. 2026-10')
        parser.add_argument('--company', default=None,
                            help='Limit to one entity by company code.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Roll everything back at the end; print what would happen.')

    def handle(self, *args, **opts):
        from core.models import Company
        from hris.leave_pay_feed import feed_period

        company = None
        if opts['company']:
            company = Company.objects.filter(code__iexact=opts['company']).first()
            if company is None:
                self.stderr.write(f"No company with code {opts['company']}.")
                return

        def _run():
            res = feed_period(period_label=opts['period'], company=company)
            self._say(res)
            return res

        if opts['dry_run']:
            try:
                with transaction.atomic():
                    res = _run()
                    self.stdout.write(f'DRY RUN — rolling back: {res}')
                    raise _Rollback()
            except _Rollback:
                return
        else:
            res = _run()
            self.stdout.write(str(res))


    def _say(self, res):
        """Say out loud what the go-live cut-off left out.

        A cut-off nobody can see working looks exactly like a broken feed: the
        queue is empty either way. So the count and the date are printed on
        every run, including the runs where nothing was pushed.
        """
        cutoff = res.get('golive_cutoff')
        held = res.get('skipped_pre_golive') or 0
        if cutoff:
            self.stdout.write(
                f'Go-live cut-off {cutoff}: {held} approved encashment(s) left '
                f'out — approved on or before it, so Finance settled those '
                f'directly and they are not payable again through payroll.')
            for row in (res.get('pre_golive_detail') or [])[:25]:
                self.stdout.write(f"    held back: {row['name']} — {row['reason']}")
        else:
            self.stdout.write(
                'No leave-pay go-live cut-off is set '
                '(payroll setting leave_pay.golive_date is blank) — every '
                'approved encashment in the month is eligible.')


class _Rollback(Exception):
    pass
