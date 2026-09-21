"""
Apply staff-loan monthly repayment deductions to a payroll period.

This wires the existing (but un-scheduled) payroll amortisation engine
`payroll.loan_service.apply_loan_repayments`, which walks every ACTIVE
EmployeeLoan and materialises that period's LOAN_REPAYMENT deduction line on
the employee's payslip (and decrements the loan's outstanding balance). The GL
credit to the staff-loan receivable then flows through the normal payroll
period posting. Idempotent per (loan, period).

Run monthly, once the period is open, before the payroll run is posted:

    python manage.py run_staff_loan_repayments                # latest open/locked period
    python manage.py run_staff_loan_repayments --period 2026-07
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = 'Apply staff-loan monthly repayment deductions to a payroll period.'

    def add_arguments(self, parser):
        parser.add_argument('--period', help='period_name, e.g. 2026-07 (default: the OPEN period covering today)')
        parser.add_argument('--force', action='store_true',
                            help='allow a period that is not OPEN (use only if you know why).')

    def handle(self, *args, **opts):
        from django.utils import timezone
        from payroll.models import PayrollPeriod
        from payroll.loan_service import apply_loan_repayments

        name = opts.get('period')
        force = opts.get('force')
        if name:
            period = PayrollPeriod.objects.filter(period_name=name).first()
            if not period:
                raise CommandError(f'No payroll period named {name!r}.')
        else:
            # Only ever write into an OPEN period — adding a deduction after a
            # period is calculated/posted corrupts the net pay and the GL balance.
            today = timezone.localdate()
            period = (PayrollPeriod.objects
                      .filter(status=PayrollPeriod.Status.OPEN,
                              start_date__lte=today, end_date__gte=today)
                      .order_by('-start_date').first()
                      or PayrollPeriod.objects.filter(status=PayrollPeriod.Status.OPEN)
                      .order_by('-start_date').first())
            if not period:
                self.stdout.write('No OPEN payroll period — nothing to do.')
                return

        if period.status != PayrollPeriod.Status.OPEN and not force:
            raise CommandError(
                f'{period.period_name} is {period.get_status_display()}, not Open. '
                f'Refusing — a deduction added now would not be collected or would break '
                f'the payroll balance. Re-run with --force only if you understand why.'
            )

        summary = apply_loan_repayments(period)
        self.stdout.write(self.style.SUCCESS(
            f'{period.period_name}: created={summary["created"]} skipped={summary["skipped"]} '
            f'paid_off={summary["paid_off"]} principal={summary["total_principal"]} '
            f'interest={summary["total_interest"]}'
        ))
