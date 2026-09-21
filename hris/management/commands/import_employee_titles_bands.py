"""
Update employee job titles + assign pay-grade bands from the HR master list.

CFO directive 2026-07-09: the "ADI Employee List" xlsx is the approved source
for every employee's TITLE (Position) and BAND (LEVEL CODE / LEVEL NAME). Load
them into omni. This is a TITLE + BAND change ONLY.

  python manage.py import_employee_titles_bands emp_load.json            # dry run
  python manage.py import_employee_titles_bands emp_load.json --commit
  python manage.py import_employee_titles_bands emp_load.json --company ADIC

Input JSON (parsed from the xlsx off-server so no ID/DOB/salary lands here):
  {
    "employees": [{"email","name","title","band_code","band_name"}...],
    "bands": {"<code>": {"code","name","level","midpoint","members"}...}
  }

Behaviour (deliberately narrow — see CFO "just title changes"):
  - Bands: create hris.Grade for any band_code that does NOT already exist.
    Existing grades are NEVER modified (their level/midpoint/name are left
    exactly as they are).
  - Employees matched by email (case-insensitive):
      * job_title  <- title   (only when it actually differs)
      * HRISProfile.grade <- the band's Grade (HRISProfile created if missing,
        with employee + grade ONLY)
  - Reports two mismatch lists for HR (Dorothy):
      A. list emails NOT found in omni
      B. active omni employees at --company NOT on the list
  - DOES NOT TOUCH access control: no auth User, Group, permission,
    UserCompanyAccess, is_staff/is_superuser, or company assignment is changed.
    Only Employee.job_title and HRISProfile.grade are written.

Dry run by default; --commit applies.
"""
from __future__ import annotations

import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from payroll.models import Employee
from hris.models import Grade, HRISProfile
from core.models import Company


class Command(BaseCommand):
    help = 'Load employee titles + bands from the HR master list JSON. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('json_path')
        parser.add_argument('--commit', action='store_true')
        parser.add_argument('--company', default='ADIC',
                            help='Company.code the list represents — scopes the '
                                 '"in omni but not on the list" report (default ADIC).')

    def handle(self, *args, **opts):
        commit = opts['commit']
        data = json.load(open(opts['json_path'], encoding='utf-8'))
        emps = data['employees']
        bands = data['bands']
        w = self.stdout.write

        # ── 1. Bands: create only the missing ones, never touch existing ─────
        existing_codes = set(Grade.objects.values_list('code', flat=True))
        bands_to_create = [b for c, b in bands.items() if c not in existing_codes]
        w(f'Bands on list: {len(bands)} | already in omni: '
          f'{len(bands) - len(bands_to_create)} | to create: {len(bands_to_create)}')
        for b in bands_to_create:
            w(f'    NEW BAND {b["code"]:<6} {b["name"][:40]:40} L{b["level"]} '
              f'mid={b["midpoint"]:.0f}')

        # ── 2. Match employees, plan title + band writes ─────────────────────
        list_emails = {e['email'].strip().lower() for e in emps if e.get('email')}
        title_changes, band_assigns, no_match, multi = [], [], [], []
        for e in emps:
            email = (e.get('email') or '').strip().lower()
            matches = list(Employee.objects.filter(email__iexact=email)) if email else []
            if not matches:
                no_match.append(e)
                continue
            if len(matches) > 1:
                multi.append((email, len(matches)))
            for emp in matches:
                if (e.get('title') or '') and emp.job_title != e['title']:
                    title_changes.append((emp, emp.job_title, e['title']))
                if e.get('band_code'):
                    band_assigns.append((emp, e['band_code']))

        # ── 3. "In omni but not on the list" (scoped to the list's company) ──
        company = Company.objects.filter(code__iexact=opts['company']).first()
        omni_not_on_list = []
        if company:
            for emp in (Employee.objects
                        .filter(company=company, status=Employee.Status.ACTIVE)
                        .exclude(Q(email='') | Q(email__isnull=True))):
                if emp.email.strip().lower() not in list_emails:
                    omni_not_on_list.append(emp)

        # ── report ───────────────────────────────────────────────────────────
        w('')
        w(self.style.MIGRATE_HEADING('PLAN' + ('' if commit else ' (DRY RUN)')))
        w(f'  list employees:          {len(emps)}')
        w(f'  title changes:           {len(title_changes)}')
        w(f'  band assignments:        {len(band_assigns)}')
        w(f'  emails w/ >1 omni match: {len(multi)}  {multi[:5]}')
        w(f'  A) on list, NOT in omni: {len(no_match)}')
        for e in no_match:
            w(f'       {e.get("email",""):<38} {e.get("name","")}  [{e.get("band_code","")}]')
        w(f'  B) in omni ({opts["company"]}), NOT on list: {len(omni_not_on_list)}')
        for emp in omni_not_on_list:
            w(f'       {emp.email:<38} {emp.full_name}')

        if not commit:
            w(self.style.WARNING('DRY RUN — nothing written. Re-run with --commit.'))
            return

        # ── apply ──────────────────────────────────────────────────────────────
        created_bands = updated_titles = assigned_bands = profiles_made = 0
        with transaction.atomic():
            code_to_grade = {}
            for b in bands_to_create:
                g = Grade.objects.create(
                    code=b['code'], name=b['name'][:80], level=int(b['level'] or 3),
                    spread=40, midpoint=Decimal(str(b.get('midpoint') or 0)),
                    is_active=True,
                )
                code_to_grade[g.code] = g
                created_bands += 1
            for g in Grade.objects.all():
                code_to_grade.setdefault(g.code, g)

            for emp, _old, new in title_changes:
                emp.job_title = new[:100]
                emp.save(update_fields=['job_title', 'updated_at'])
                updated_titles += 1

            for emp, code in band_assigns:
                grade = code_to_grade.get(code)
                if not grade:
                    continue
                profile, made = HRISProfile.objects.get_or_create(employee=emp)
                profiles_made += int(made)
                if profile.grade_id != grade.id:
                    profile.grade = grade
                    profile.save(update_fields=['grade', 'updated_at'])
                    assigned_bands += 1

        w(self.style.SUCCESS(
            f'COMMITTED: bands_created={created_bands} titles_updated={updated_titles} '
            f'bands_assigned={assigned_bands} hris_profiles_created={profiles_made}'))
