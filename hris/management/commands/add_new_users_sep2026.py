"""
Add the 4 new users requested by Ikanyeng Sechele (Senior IT Associate) on
10-Sep-2026: Unopa (Software Dev), Kelvin (IT), Lorato (Unicoin),
Unalodo (IT_Unicoin).

Usage:
    python manage.py add_new_users_sep2026
    python manage.py add_new_users_sep2026 --commit
"""
import logging
import uuid
from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)

NEW_USERS = [
    {
        'full_name': 'Unopa',
        'department': 'Software Development',
        'job_title': 'Software Developer',
        'company_code': 'ADIC',
        'manager_email': 'pganesharajah@alphadirect.co.bw',
    },
    {
        'full_name': 'Kelvin Kimani',
        'department': 'Admin & IT',
        'job_title': 'IT',
        'company_code': 'ADIC',
        'manager_email': 'pganesharajah@alphadirect.co.bw',
    },
    # Lorato and Unalodo are Unicoin — only create if they appear on payroll.
    # Per CFO: "ignore unicoin people most of them are agents". These two are
    # added ONLY as Employee records for IT tracking; they will NOT have payslips
    # unless HR confirms they are salaried staff.
    {
        'full_name': 'Lorato',
        'department': 'UniCoin',
        'job_title': 'Unicoin',
        'company_code': 'UNI',
        'manager_email': None,
        'is_agent': True,
    },
    {
        'full_name': 'Unalodo',
        'department': 'Admin & IT',
        'job_title': 'IT_Unicoin',
        'company_code': 'UNI',
        'manager_email': None,
        'is_agent': True,
    },
]


class Command(BaseCommand):
    help = 'Add 4 new users requested by Ikanyeng (Sep 2026).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **options):
        from payroll.models import Employee, Company

        commit = options['commit']
        companies = {c.code: c for c in Company.objects.all()}

        for u in NEW_USERS:
            co = companies.get(u['company_code'])
            if co is None:
                self.stdout.write(f"  SKIP {u['full_name']}: company {u['company_code']} not found")
                continue

            existing = Employee.objects.filter(full_name__iexact=u['full_name']).first()
            if existing:
                self.stdout.write(
                    f"  EXISTS {u['full_name']}: employee #{existing.employee_number} "
                    f"({existing.email})")
                continue

            if commit:
                emp = Employee.objects.create(
                    full_name=u['full_name'],
                    employee_number=f"NEW-{uuid.uuid4().hex[:8].upper()}",
                    department=u['department'],
                    job_title=u['job_title'],
                    company=co,
                    status=Employee.Status.ACTIVE,
                )
                self.stdout.write(f"  CREATED {u['full_name']} (#{emp.employee_number})")
            else:
                self.stdout.write(
                    f"  WOULD CREATE {u['full_name']} in {u['company_code']} "
                    f"({u['department']})")

        mode = 'COMMITTED' if commit else 'DRY RUN'
        self.stdout.write(f'\n{mode}. After committing, run setup_employee_users '
                          'to create their login accounts.')
