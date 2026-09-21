"""
hris/management/commands/import_m365_roster.py

Reconcile the migrated Microsoft 365 user list (licensing.M365ActiveUser) into
the HRIS Employee roster. CFO directive 2026-06-18 (HRIS-004, Oprah): staff who
were synced from M365 but never landed as Employees can't be seen in the HRIS
or assigned a leave approver.

Creates a minimal Employee + HRISProfile for each missing real person:
  full_name / email / department / job_title  ← from M365
  company = NULL    — entity UNASSIGNED. M365 carries no entity and email domain
                      does NOT determine company (@alphadirect.co.bw spans ADIC +
                      Risk Software Africa + Quantum; @insurance.co.bw spans 4),
                      so HR assigns each person's entity afterwards. NULL is
                      honest and never misattributes per-entity headcount/leave.
  status  = active
  external_ref = 'm365-roster-2026-06-18'  (labels the batch — reversible)

Skips: anyone already an Employee (by email); shared / group mailboxes
(single-token display name, or department/auditor/etc. keywords); and exact
normalised-name matches of an existing email-less Employee (avoids duplicates).

Dry-run by default — pass --commit to write. Idempotent (re-runs skip existing).
"""
from __future__ import annotations
from hris.departments import fold_legacy

import re

from django.core.management.base import BaseCommand
from django.db import transaction

from licensing.models import M365ActiveUser
from payroll.models import Employee
from hris.models import HRISProfile


_GROUP_RE = re.compile(
    r'(?i)department|auditor|\bteam\b|\badmin\b|\binfo\b|support|noreply|'
    r'no-reply|shared|mailbox|\bgroup\b|payroll|helpdesk|reception|accounts@|'
    r'procurement|\bparts\b|\bpeople\b|culture|recruit|careers'
)


def _norm(s) -> str:
    return re.sub(r'\s+', ' ', str(s or '').replace('.', ' ')).strip().lower()


def _is_group_mailbox(u) -> bool:
    name = (u.display_name or '').strip()
    if ' ' not in name:                       # a single token is not a person's name
        return True
    return bool(_GROUP_RE.search(f'{name} {u.email}'))


class Command(BaseCommand):
    help = 'Reconcile migrated M365 users into the HRIS Employee roster (entity-unassigned).'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Write the records. Without it, dry-run only.')
        parser.add_argument('--hr-approved-by', default='',
                            help='Email of the HR person who approved adding these people. '
                                 'Required with --commit (CFO rule 19-Sep-2026: HR record comes '
                                 'from HR onboarding, not from the Microsoft licence list).')

    def handle(self, *args, **opts):
        commit = opts['commit']
        if commit and not (opts.get('hr_approved_by') or '').strip():
            self.stderr.write('Refusing to write without --hr-approved-by <HR email>. '
                              'Use People → Onboard for new staff. Showing a dry run instead.')
            commit = False
        emps = list(Employee.objects.all().only('id', 'full_name', 'email'))
        have_email = {e.email.strip().lower() for e in emps if e.email}
        emailless_names = {_norm(e.full_name) for e in emps if not e.email}

        to_create, skipped_existing, skipped_group, skipped_namedup = [], 0, [], []
        for u in M365ActiveUser.objects.all():
            email = (u.email or '').strip().lower()
            if not email or email in have_email:
                skipped_existing += 1
                continue
            if _is_group_mailbox(u):
                skipped_group.append(u.display_name or email)
                continue
            nm = re.sub(r'\s+', ' ', (u.display_name or '').strip())
            if _norm(nm) in emailless_names:
                skipped_namedup.append(f'{nm} <{email}>')
                continue
            to_create.append((u, nm, email))

        self.stdout.write(f'M365 users            : {M365ActiveUser.objects.count()}')
        self.stdout.write(f'Already employees     : {skipped_existing}')
        self.stdout.write(f'Skipped group mailbox : {len(skipped_group)}  {skipped_group}')
        self.stdout.write(f'Skipped name-dup      : {len(skipped_namedup)}  {skipped_namedup}')
        self.stdout.write(f'TO CREATE             : {len(to_create)}')
        for u, nm, email in to_create:
            self.stdout.write(f'   + {nm} <{email}>  dept={u.department or "-"}  title={u.job_title or "-"}')

        if not commit:
            self.stdout.write(self.style.WARNING('DRY-RUN — nothing written. Re-run with --commit.'))
            return

        created = 0
        with transaction.atomic():
            for u, nm, email in to_create:
                emp = Employee.objects.create(
                    employee_number=('M365-' + (u.object_id or '').replace('-', ''))[:30],
                    full_name=nm,
                    email=email,
                    department=(fold_legacy(u.department) or u.department or '')[:100],
                    job_title=(u.job_title or '')[:100],
                    company=None,                 # entity UNASSIGNED — HR sets it
                    status=Employee.Status.ACTIVE,
                    external_ref='m365-roster-2026-06-18',
                )
                HRISProfile.objects.get_or_create(employee=emp)
                created += 1
        self.stdout.write(self.style.SUCCESS(
            f'Created {created} employee(s) + HRIS profiles (company=NULL — HR to assign entity).'))
