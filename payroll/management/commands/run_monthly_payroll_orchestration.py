"""
Conduct one month's payroll feeds, in order, and verify the commission
population (Build Spec B13 — Payroll 08).

    python manage.py run_monthly_payroll_orchestration
    python manage.py run_monthly_payroll_orchestration --period 2026-10
    python manage.py run_monthly_payroll_orchestration --company ADIC
    python manage.py run_monthly_payroll_orchestration --trigger cron

It REFUSES a period that is already signed off, and it exits non-zero when it
refuses, when a step fails, or when the run did nothing at all — a cron that
silently succeeds having done nothing is the failure this build exists to stop.

It never locks, never posts, and never moves money.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ('Run the month\'s payroll feeds in order and verify commissions. '
            'Refuses a signed-off period.')

    def add_arguments(self, parser):
        parser.add_argument('--period', help='period_name, e.g. 2026-10 '
                                             '(default: the OPEN period covering today)')
        parser.add_argument('--company', help='Company code or name. Default: every entity.')
        parser.add_argument('--user-email', help='Actor the run is attributed to.')
        parser.add_argument('--trigger', choices=['cron', 'manual'], default='manual')

    def handle(self, *args, **opts):
        from django.contrib.auth.models import User
        from django.utils import timezone

        from core.models import Company
        from payroll.models import PayrollPeriod
        from payroll.monthly_orchestration import PeriodRefused, run_month

        name = opts.get('period')
        if name:
            period = PayrollPeriod.objects.filter(period_name=name).first()
            if period is None:
                raise CommandError(f'No payroll period named {name!r}.')
        else:
            today = timezone.localdate()          # Botswana time, never date.today()
            period = (PayrollPeriod.objects
                      .filter(start_date__lte=today, end_date__gte=today,
                              status=PayrollPeriod.Status.OPEN)
                      .order_by('-start_date').first())
            if period is None:
                raise CommandError('No OPEN payroll period covers today. Name one '
                                   'with --period, or open the month first.')

        company = None
        if opts.get('company'):
            token = opts['company'].strip()
            company = (Company.objects.filter(code__iexact=token).first()
                       or Company.objects.filter(name__iexact=token).first())
            if company is None:
                raise CommandError(f'No company matching {token!r}.')

        user = None
        if opts.get('user_email'):
            user = User.objects.filter(email__iexact=opts['user_email']).first()
            if user is None:
                raise CommandError(f'No user with e-mail {opts["user_email"]!r}.')

        try:
            run = run_month(period_label=period.period_name, company=company,
                            user=user, trigger=opts['trigger'])
        except PeriodRefused as exc:
            # Refused is a correct outcome, not a crash — but it must be loud
            # and it must be non-zero so a cron mails somebody.
            raise CommandError(f'REFUSED: {exc}') from exc

        from payroll.models import PayrollOrchestrationRun as Run

        self.stdout.write(f'{run}  ({run.rows_touched} row(s) touched)')
        for st in run.steps.all():
            self.stdout.write(f'  {st.sequence}. {st.name}: {st.get_status_display()} '
                              f'— {st.rows} row(s), {st.skipped} skipped')
            for line in (st.detail or st.error or '').splitlines():
                self.stdout.write(f'       {line}')
        if run.gaps:
            self.stdout.write(self.style.WARNING('  GAPS (reported, not widened):'))
            for line in run.gaps.splitlines():
                self.stdout.write(self.style.WARNING(f'       {line}'))
        self.stdout.write(f'  Reported to: {run.notified_to or "NOBODY"}'
                          + (f' ({run.notify_error})' if run.notify_error else ''))

        if run.status == Run.Status.FAILED:
            raise CommandError('A step FAILED — see the run log and the e-mail.')
        if run.status == Run.Status.EMPTY:
            raise CommandError(
                'The run did NOTHING — no step raised a single row. Silence is '
                'not success: check that approvals are reaching payroll before '
                'this month is closed.')
        if run.status == Run.Status.ATTENTION:
            self.stdout.write(self.style.WARNING(
                'Completed with gaps to look at (see above).'))
        else:
            self.stdout.write(self.style.SUCCESS('Completed.'))
