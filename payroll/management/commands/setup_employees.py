"""
payroll/management/commands/setup_employees.py

Idempotent — seeds the 80 Alpha Direct staff members provided by the CFO,
populating only full_name + department. Salary, ID, banking, and other PII
are NOT seeded; the CFO/HR fills them in via the admin or API.

Run:
    python manage.py setup_employees
"""

from django.core.management.base import BaseCommand

from hris.departments import fold_legacy
from payroll.models import Employee


# (full_name, department) — exactly as supplied by the CFO 2026-05-09.
# Names left in their submitted form (incl. trailing whitespace tolerated).
EMPLOYEES = [
    ('Ikanyeng Sechele',              'Information Technology'),
    ('Wangu Moses',                   'Claims'),
    ('Lorato Nkane',                  'Administration'),
    ('Moemedi Mositiemang',           'Administration'),
    ('Galaletsang Dipitso',           'Compliance'),
    ('Goitsemang Ngwako',             'Administration'),
    ('Unami Butale',                  'Senior Management'),
    ('Kutlo Lone Ntshole',            'Underwriting'),
    ('Arun Iyer',                     'C-Suite'),
    ('Bame Pearl Sebape',             'Underwriting'),
    ('Gomolemo Sebudula',             'Underwriting'),
    ('Kakale Botana',                 'Compliance'),
    ('Onneile Ramakwati',             'Compliance'),
    ('Bonang Lentswe',                'Claims'),
    ('Bonno Ben',                     'Underwriting'),
    ('Kago Tshutlhedi',               'Finance & Planning'),
    ('Kotswana Kotswana',             'Business Development'),
    ('Laone Angela Thebe',            'Finance & Planning'),
    ('Larona Gofaone Dean Baleseng',  'Claims'),
    ('Lefika Basotli',                'Finance & Planning'),
    ('Letsweletse Marumo',            'Business Development'),
    ('Leungo Patiko',                 'Underwriting'),
    ('Louis Gofaone Ntshekang',       'Business Development'),
    ('Nametso Kelly Kebaetse Albert', 'Business Development'),
    ('Marang Sethe Makhoana',         'Health Insurance'),
    ('Oitiretse Kao Galotshoge',      'Claims'),
    ('Milidzani Muzila',              'Finance & Planning'),
    ('Opelo Rosemary Mokime',         'Compliance'),
    ('Prathap Ganesharajah',          'C-Suite'),
    ('Prince Mosweu',                 'Claims'),
    ('Randy Taukobong',               'Claims'),
    ('Ritah Rethabile N. Tonkope',    'Special Projects'),
    ('Segolame Masilo',               'Claims'),
    ('Losika Motlhobogwa',            'Claims'),
    ('Lame Patience Setsiba',         'Business Development'),
    ('Naomi Natasha Pheko',           'Compliance'),
    ('Tlamelo Chimidza',              'Finance & Planning'),
    ('Tlotlo Rabakane',               'Business Development'),
    ('Zwiseko Monyatsi',              'Business Development'),
    ('Bokani Makosha',                'Data Analytics'),
    ('Loapi Keotlhoboge',             'Special Projects'),
    ('Bakang Mhusiwa',                'Finance & Planning'),
    ('Oratile Ria Tlhomelang',        'Compliance'),
    ('Phemo Ofile Kgosi',             'Data Analytics'),
    ('Ofhimile Seabe Modisa',         'Underwriting'),
    ('Gaolatlhe Beauty Marumo',       'Business Development'),
    ('Kelebogile Gaothobogwe',        'Claims'),
    ('Kelebogile Molefe',             'Information Technology'),
    ('Lungisile Arona C. Gaorekwe',   'Data Analytics'),
    ('Wame Petronel Matlhare',        'Administration'),
    ('Thapelo Patricia Morapedi',     'Human Resource'),
    ('Tumisang Raseila',              'Finance & Planning'),
    ('Shane Thabo Khupe',             'Finance & Planning'),
    ('Koketso Tlotlego Kgetse',       'Finance & Planning'),
    ('Gaolebale Shale Machobane',     'Underwriting'),
    ('Bakang Ray Rebagamang',         'Underwriting'),
    ('Lindani Mababa',                'Finance & Planning'),
    ('Nametsegang Steven Lehuma',     'Business Development'),
    ('Pako Lisley Kago',              'Finance & Planning'),
    ('Rose Mmabatho Mokgware',        'Finance & Planning'),
    ('Tlotlo Maswabi',                'Administration'),
    ('Dorothy K. Ikgopoleng',         'Human Resource'),
    ('Meduduetso Ayanda A. Tlagae',   'Special Projects'),
    ('Phomolo Johane',                'Business Development'),
    ('Karabo Borupile',               'Claims'),
    ('Patience Phesodi',              'Business Development'),
    ('Paul Beka',                     'Senior Management'),
    ('Keneilwe Jane',                 'Health Insurance'),
    ('Aobakwe Angel Morris',          'Claims'),
    ('Bharath Balasubramanian',       'Sales And Marketing'),
    ('Brian Ngele',                   'Underwriting'),
    ('Christopher Kelefatse',         'Underwriting'),
    ('Dimpho Amogelang Motseothata',  'Business Development'),
    ('Judith Kahuma',                 'Health Insurance'),
    ('Babusi Brian Rasenyai',         'Data Analytics'),
    ('Ditso Pako Motlhabane',         'Claims'),
    ('Elaine Mokone',                 'Underwriting'),
    ('Keetile Mokhendo',              'Finance & Planning'),
    ('Legakwa Tsala Ntabeni',         'Finance & Planning'),
    ('Bakang Valela',                 'Business Development'),
    ('Bontle Precious Tendani',       'Finance & Planning'),
]


class Command(BaseCommand):
    help = 'Seed Alpha Direct staff (full_name + department). Idempotent.'

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for full_name, dept in EMPLOYEES:
            dept = fold_legacy(dept) or dept  # approved spelling only (L-DEPT)
            obj, was_created = Employee.objects.get_or_create(
                full_name=full_name,
                defaults={'department': dept, 'status': Employee.Status.ACTIVE},
            )
            if was_created:
                created += 1
            else:
                if obj.department != dept:
                    obj.department = dept
                    obj.save()
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nEmployees: {created} created, {skipped} already present.'
        ))
