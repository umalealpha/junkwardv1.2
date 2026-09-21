"""
Fold every Employee.department onto the CFO-approved list (register row L-DEPT).

On 18-Sep-2026 live Omni held 21 spellings for 15 departments ('Finance' and
'Finance & Planning', 'Uni Coin' and 'Unicoin', 'C-Suite' and 'Exco', ...).
Every one maps through hris.departments.fold_legacy — the list the CFO approved
the same day.

SAFETY:
  * DRY-RUN by default: prints every change, writes nothing.
  * Only a known spelling moves. Blank and unknown values are left alone and
    listed, never guessed (a guessed department would be worse than a blank).
  * Idempotent: a second --commit changes nothing.
  * Every change writes one core.AuditLog row holding the old value, so it can
    be reversed by hand from the audit trail.
  * Only payroll.Employee.department — PO departments, roles and profile
    departments are separate fields and are not touched.

    python manage.py fold_employee_departments
    python manage.py fold_employee_departments --commit
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import AuditLog
from hris.departments import fold_legacy
from payroll.models import Employee


def plan_folds():
    """(employee, old, new) for every row whose department is a known legacy spelling."""
    moves, unknown = [], Counter()
    for emp in Employee.objects.exclude(department='').order_by('full_name'):
        old = emp.department
        new = fold_legacy(old)
        if new is None:
            unknown[old] += 1
        elif new != old:
            moves.append((emp, old, new))
    return moves, unknown


class Command(BaseCommand):
    help = 'Fold Employee.department onto the approved list. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Write the changes (default is a dry run).')

    def handle(self, *args, **opts):
        commit = opts['commit']
        moves, unknown = plan_folds()
        for emp, old, new in moves:
            self.stdout.write(f'  {"MOVE " if commit else "WOULD"} {emp.full_name:32} {old!r} -> {new!r}')
        for value, n in sorted(unknown.items()):
            self.stdout.write(f'  LEFT  {value!r} x{n} (not on the approved list, no known alias)')
        if commit and moves:
            with transaction.atomic():
                for emp, old, new in moves:
                    # Re-read under a lock: a department edited since the plan
                    # was printed is left alone, never overwritten.
                    emp = Employee.objects.select_for_update().get(pk=emp.pk)
                    if emp.department != old:
                        self.stdout.write(f'  SKIP  {emp.full_name}: changed since the plan')
                        continue
                    emp.department = new
                    emp.save(update_fields=['department', 'updated_at'], skip_audit=True)
                    AuditLog.objects.create(
                        table_name='payroll.Employee',
                        record_id=str(emp.pk),
                        action=AuditLog.Action.UPDATE,
                        old_values={'department': old},
                        new_values={'department': new},
                        description=f'Department folded to the approved list: {old!r} -> {new!r} (L-DEPT)',
                    )
        verb = 'Moved' if commit else 'Would move'
        self.stdout.write(f'{verb} {len(moves)}; left {sum(unknown.values())} unknown.')
