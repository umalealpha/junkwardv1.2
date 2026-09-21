from django.core.management.base import BaseCommand
from django.db.models import Q

from payroll.models import Employee
from hris.models import HRISProfile

EXPATRIATE_NAMES = [
    'Prathap Ganesharajah',
    'Paul Beka',
    'Arun Iyer',
    'Bharath Balasubramanian',
]

CONTROLLER_NAMES = [
    'Paul Beka',
    'Kakale Botana',
]


def _match_active_employee(name):
    tokens = name.lower().split()
    qs = Employee.objects.filter(status='active')
    for token in tokens:
        qs = qs.filter(full_name__icontains=token)

    count = qs.count()
    if count == 0:
        return None, 'UNMATCHED'
    if count > 1:
        return None, 'AMBIGUOUS'
    return qs.first(), 'OK'


class Command(BaseCommand):
    help = 'Seed HRISProfile contract flags from HC lists (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Write profile flag changes.')

    def handle(self, *args, **options):
        commit = bool(options.get('commit', False))
        expatriate_ids = set()
        controller_ids = set()

        for name in EXPATRIATE_NAMES:
            employee, status = _match_active_employee(name)
            if status == 'OK':
                expatriate_ids.add(employee.id)
                self.stdout.write(f"Expatriate list match: {name} -> {employee.full_name}")
            else:
                self.stdout.write(f"Expatriate list match {name}: {status}")

        for name in CONTROLLER_NAMES:
            employee, status = _match_active_employee(name)
            if status == 'OK':
                controller_ids.add(employee.id)
                self.stdout.write(f"Controller list match: {name} -> {employee.full_name}")
            else:
                self.stdout.write(f"Controller list match {name}: {status}")

        ex_grade_q = Q(hris_profile__grade__code__startswith='EX')
        manager_q = (
            Q(job_title__icontains='Underwriting Manager')
            | Q(job_title__icontains='Claims Manager')
            | Q(job_title__icontains='Finance Manager')
            | Q(hris_profile__grade__name__icontains='Underwriting Manager')
            | Q(hris_profile__grade__name__icontains='Claims Manager')
            | Q(hris_profile__grade__name__icontains='Finance Manager')
        )
        rule_controller_qs = Employee.objects.filter(status='active').filter(ex_grade_q | manager_q).distinct()
        for employee in rule_controller_qs:
            controller_ids.add(employee.id)
            self.stdout.write(f"Controller rule match: {employee.full_name}")

        for employee_id in expatriate_ids:
            employee = Employee.objects.get(id=employee_id)
            self._apply_flag(employee, 'is_expatriate', True, commit)

        for employee_id in controller_ids:
            employee = Employee.objects.get(id=employee_id)
            self._apply_flag(employee, 'is_controller', True, commit)

        if not commit:
            self.stdout.write('DRY-RUN: no changes written. Use --commit to write.')

    def _apply_flag(self, employee, field_name, value, commit):
        profile, _ = HRISProfile.objects.get_or_create(employee=employee)
        old_value = getattr(profile, field_name)
        if old_value != value:
            if commit:
                setattr(profile, field_name, value)
                profile.save()
                self.stdout.write(f"SET {employee.full_name} ({employee.employee_number}) {field_name}={value} (was {old_value})")
            else:
                self.stdout.write(f"DRY-RUN would SET {employee.full_name} ({employee.employee_number}) {field_name}={value} (was {old_value})")
        else:
            self.stdout.write(f"UNCHANGED {employee.full_name} ({employee.employee_number}) {field_name}={value}")
