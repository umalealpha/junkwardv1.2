"""
Deactivate a specific employee by email.

Usage:
    python manage.py remove_employee ankete@alphadirect.co.bw
    python manage.py remove_employee ankete@alphadirect.co.bw --commit
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Deactivate a specific employee and their user account.'

    def add_arguments(self, parser):
        parser.add_argument('email', help='Employee email address')
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **options):
        from payroll.models import Employee

        email = options['email'].strip().lower()
        emp = Employee.objects.filter(email__iexact=email).first()

        if not emp:
            self.stdout.write(f'No employee found with email {email}')
            return

        self.stdout.write(
            f'Found: {emp.full_name} ({emp.email}), '
            f'status={emp.status}, company={getattr(emp.company, "code", "?")}')

        if emp.status == 'terminated':
            self.stdout.write('Already terminated — nothing to do.')
            return

        if options['commit']:
            emp.status = 'terminated'
            emp.save(update_fields=['status', 'updated_at'])
            if emp.user:
                emp.user.is_active = False
                emp.user.save(update_fields=['is_active'])
            self.stdout.write(f'DEACTIVATED {emp.full_name}')
        else:
            self.stdout.write(f'WOULD DEACTIVATE {emp.full_name} (pass --commit)')
