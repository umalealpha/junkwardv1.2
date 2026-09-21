"""payroll/management/commands/seed_group_staff.py

Seeds the 18 group-company staff from the CFO-provided "Group Staff Names"
file (2026-05-14) into the payroll Employee table. Idempotent: skips rows
that already exist for the same (company, full_name) pair.

Run:
    python manage.py seed_group_staff
"""
from django.core.management.base import BaseCommand
from django.db import transaction


STAFF = [
    # (company_code, full_name)
    ('UNI',  'Ndibo Botho M.O Makepe'),
    ('UNI',  'Lemogang Machola'),
    ('UNI',  'Caroline Refilwe Otukile'),
    ('UNI',  'Gorata Taele'),
    ('UNI',  'Conrad Seretse'),
    ('UNI',  'Gabriel Amantle C. Mompati'),
    ('UNI',  'Morekolodi Motamma'),
    ('UNI',  'Natasha Nthite'),
    ('UNI',  'Motlatsi Molefe'),
    ('UNI',  'Phatsimo Ojang'),
    ('UNI',  'Tankiso Mabeo'),
    ('UNI',  'Olebogeng Matildah Matsididi'),
    ('ADRG', 'Charmaine Bamusi'),
    ('ADRG', 'Arjun Parameswaran'),
    ('ADRG', 'Shingidzano Lesetedi'),
    ('ADRG', 'Emily Chilongo'),
    ('QIH',  'Goabaone Oprah Mogomotsi'),
    ('QIH',  'Gosego Makone'),
    # Veritas — CFO follow-up 2026-05-14, original sheet was empty.
    ('VCM',  'Moses Ncube'),
    ('VCM',  'Tshephang Motswagae'),
    ('VCM',  'Lesego Weni Kobe'),
    ('VCM',  'Tumiso Innocent Motseko'),
]

EXTERNAL_REF = 'group_staff_xlsb_2026-05-14'


class Command(BaseCommand):
    help = 'Seed the 18 group-company staff into payroll.Employee (idempotent).'

    @transaction.atomic
    def handle(self, *args, **options):
        from core.models import Company
        from payroll.models import Employee

        # Pick a starting employee_number above any existing GRP-prefixed numbers
        # so we never collide with the unique constraint on payroll.Employee.employee_number.
        existing_numbers = Employee.objects.filter(
            employee_number__startswith='GRP-2026-'
        ).values_list('employee_number', flat=True)
        max_seq = 0
        for n in existing_numbers:
            try:
                max_seq = max(max_seq, int(n.rsplit('-', 1)[-1]))
            except (ValueError, IndexError):
                pass
        next_seq = max_seq + 1

        created = existed = 0
        missing_co = []

        for co_code, full_name in STAFF:
            try:
                co = Company.objects.get(code=co_code)
            except Company.DoesNotExist:
                missing_co.append((co_code, full_name))
                continue

            if Employee.objects.filter(company=co, full_name=full_name).exists():
                existed += 1
                continue

            Employee.objects.create(
                company=co,
                full_name=full_name,
                employee_number=f'GRP-2026-{next_seq:04d}',
                status=Employee.Status.ACTIVE,
                external_ref=EXTERNAL_REF,
            )
            next_seq += 1
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f'EMPLOYEES: created={created} already_existed={existed}'
        ))
        self.stdout.write('PER_COMPANY:')
        for co_code in ['UNI', 'ADRG', 'QIH', 'VCM']:
            co = Company.objects.filter(code=co_code).first()
            if not co:
                self.stdout.write(f'  {co_code}: company missing')
                continue
            total = Employee.objects.filter(company=co).count()
            new_grp = Employee.objects.filter(
                company=co, external_ref=EXTERNAL_REF
            ).count()
            self.stdout.write(f'  {co_code}: total={total} new_grp_seed={new_grp}')

        if missing_co:
            self.stdout.write(self.style.WARNING(
                f'MISSING_COMPANIES (skipped): {missing_co}'
            ))
