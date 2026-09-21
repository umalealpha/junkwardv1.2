"""
Remove Unicoin (UNI) employees from Omni who are NOT on payroll.

Per CFO 10-Sep-2026: "ignore unicoin people most of them are agents, only
consider unicoin people who are in payroll, if there are unicoin people in
omni and not in payroll remove them from Omni"

Usage:
    python manage.py unicoin_cleanup
    python manage.py unicoin_cleanup --commit
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Deactivate Unicoin employees who have no payslip (agents, not staff).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **options):
        from payroll.models import Employee, Payslip

        uni_employees = (
            Employee.objects
            .filter(company__code='UNI', status='active', is_test_record=False)
            .select_related('company')
        )

        on_payroll = set(
            Payslip.objects
            .filter(employee__company__code='UNI')
            .values_list('employee_id', flat=True).distinct()
        )

        to_deactivate = [e for e in uni_employees if e.pk not in on_payroll]

        self.stdout.write(
            f'UNI employees: {uni_employees.count()} total, '
            f'{len(on_payroll)} on payroll, {len(to_deactivate)} agents to remove\n')

        for emp in to_deactivate:
            if options['commit']:
                emp.status = 'terminated'
                emp.save(update_fields=['status', 'updated_at'])
                if emp.user:
                    emp.user.is_active = False
                    emp.user.save(update_fields=['is_active'])
            action = 'DEACTIVATED' if options['commit'] else 'WOULD DEACTIVATE'
            self.stdout.write(f'  {action} {emp.full_name} ({emp.email})')

        if on_payroll:
            self.stdout.write('\nKEPT (on payroll):')
            for emp in uni_employees:
                if emp.pk in on_payroll:
                    self.stdout.write(f'  {emp.full_name} ({emp.email})')

        mode = 'COMMITTED' if options['commit'] else 'DRY RUN'
        self.stdout.write(f'\n{mode}: {len(to_deactivate)} Unicoin agents deactivated.')
