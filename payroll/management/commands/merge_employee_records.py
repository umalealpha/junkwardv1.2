"""
payroll/management/commands/merge_employee_records.py

Merge a DUPLICATE payroll.Employee row into the real one: move everything that
points at the duplicate onto the survivor, then retire the duplicate.

Why this exists (CFO directive 31 Jul 2026): the July 2026 payroll run created a
SECOND employee row for people whose name it could not match, instead of
attaching to the row that already existed. Galaletseng Dipitso ended up with
"Galaletseng Dipitso" (ADIC_342, Compliance, 62 payslips, a login) and
"Galeletsang Dipitso" (ADIC-GALELETSANGD, no login, no department, holding her
one July payslip). Her July payslip was sitting on a row she could never see.

Five employees in the July run had no login; four of them have a twin.

SAFETY — this moves payroll records, so:
  * DRY-RUN by default.
  * REFUSES if survivor and duplicate both hold a payslip for the SAME period —
    that is a double-pay risk and needs a human to say which is right.
  * REFUSES if either row has a login and they differ — deciding whose login is
    whose is not a script's call.
  * The duplicate is NEVER hard-deleted. It is marked terminated with the merge
    recorded in external_ref, because payroll history must stay auditable
    (Employee has no soft-delete column, and Finance's standing rule is that
    nothing is hard-deleted).
  * Every move is printed before it happens.

    python manage.py merge_employee_records --keep <uuid> --dup <uuid>
    python manage.py merge_employee_records --keep <uuid> --dup <uuid> --commit
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from payroll.models import Employee
from django.utils import timezone


class Command(BaseCommand):
    help = 'Merge a duplicate Employee row into the real one (dry-run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--keep', required=True, help='UUID of the row to KEEP.')
        parser.add_argument('--dup', required=True, help='UUID of the DUPLICATE to retire.')
        parser.add_argument('--commit', action='store_true', help='Write. Without it, dry-run.')

    def handle(self, *args, **opts):
        commit = opts['commit']
        try:
            keep = Employee.objects.get(pk=opts['keep'])
            dup = Employee.objects.get(pk=opts['dup'])
        except Employee.DoesNotExist as e:
            self.stderr.write(f'Employee not found: {e}')
            return

        if keep.pk == dup.pk:
            self.stderr.write('--keep and --dup are the same row.')
            return

        self.stdout.write(f'KEEP : {keep.full_name!r}  num={keep.employee_number!r}  '
                          f'dept={keep.department or "-"}  user={keep.user_id}  status={keep.status}')
        self.stdout.write(f'DUP  : {dup.full_name!r}  num={dup.employee_number!r}  '
                          f'dept={dup.department or "-"}  user={dup.user_id}  status={dup.status}')

        # ── Guard: two different logins ──────────────────────────────────
        if dup.user_id and keep.user_id and dup.user_id != keep.user_id:
            self.stderr.write(self.style.ERROR(
                f'REFUSED: both rows carry a DIFFERENT login (keep={keep.user_id}, '
                f'dup={dup.user_id}). Which login belongs to this person is a human '
                f'decision, not a merge rule.'))
            return

        # ── Guard: same-period payslips would double up ──────────────────
        from payroll.models import Payslip
        dup_periods = set(Payslip.objects.filter(employee=dup).values_list('period_id', flat=True))
        keep_periods = set(Payslip.objects.filter(employee=keep).values_list('period_id', flat=True))
        clash = dup_periods & keep_periods
        if clash:
            self.stderr.write(self.style.ERROR(
                f'REFUSED: both rows hold a payslip for {len(clash)} of the same '
                f'period(s): {sorted(str(c) for c in clash)}. Moving them would give '
                f'this person two payslips for one month. Payroll must decide which '
                f'is correct first.'))
            return

        # ── Plan every reverse relation ──────────────────────────────────
        moves = []
        for rel in Employee._meta.related_objects:
            model, field = rel.related_model, rel.field.name
            try:
                qs = model.objects.filter(**{field: dup})
                n = qs.count()
            except Exception:                                  # noqa: BLE001
                continue
            if n:
                moves.append((model, field, n, list(qs[:20])))

        if not moves:
            self.stdout.write('Nothing points at the duplicate — it is an empty row.')
        for model, field, n, sample in moves:
            self.stdout.write(f'  move {n:4} x {model.__name__}.{field}  ->  KEEP')
            for obj in sample:
                extra = ''
                for f in ('period', 'gross_amount', 'net_amount', 'status'):
                    if hasattr(obj, f):
                        extra += f' {f}={getattr(obj, f)}'
                self.stdout.write(f'        {obj.pk}{extra}')

        # Fields worth carrying over when the survivor is missing them.
        fill = {}
        for f in ('department', 'job_title', 'email', 'phone', 'hire_date', 'national_id'):
            kv, dv = getattr(keep, f, None), getattr(dup, f, None)
            if (kv in (None, '')) and dv not in (None, ''):
                fill[f] = dv
        if fill:
            self.stdout.write(f'  fill blank fields on KEEP from DUP: {fill}')

        self.stdout.write(f'  then mark DUP terminated, external_ref = "merged-into:{keep.employee_number}"')

        if not commit:
            self.stdout.write(self.style.WARNING('\nDRY-RUN — nothing written. Re-run with --commit.'))
            return

        moved = 0
        with transaction.atomic():
            for model, field, _n, _s in moves:
                moved += model.objects.filter(**{field: dup}).update(**{field: keep})
            if fill:
                for f, v in fill.items():
                    setattr(keep, f, v)
                keep.save(update_fields=list(fill))
            dup.status = Employee.Status.TERMINATED
            dup.external_ref = f'merged-into:{keep.employee_number}'
            if hasattr(dup, 'termination_date') and not dup.termination_date:
                from django.utils import timezone
                dup.termination_date = timezone.localdate()
            dup.save(update_fields=['status', 'external_ref', 'termination_date'])

        self.stdout.write(self.style.SUCCESS(
            f'Moved {moved} record(s) onto {keep.full_name!r}; duplicate retired '
            f'(terminated, not deleted — payroll history stays auditable).'))
