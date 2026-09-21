"""
Import the FY2025 Development Dialogue workbooks into 9-box placements.

    python manage.py import_dd_workbooks              # dry run — reports, writes nothing
    python manage.py import_dd_workbooks --commit
    python manage.py import_dd_workbooks --commit --period FY2025

Dorothy's `Development Dialogues 2025.zip` (18 Jul 2026) is sitting in the HR
document vault as 37 `development_dialogue` documents. This turns each workbook
into a DevelopmentDialogue row so the person actually appears on the 9-box grid.

Rows are written LOCKED: FY2025 is a closed period, the review already happened,
and a locked dialogue cannot be edited through the cockpit or deleted by anyone
(see DevelopmentDialogue.delete). Re-running is safe — an existing row for the
same person+period is refreshed in place, never duplicated, and a row that is
already locked is left completely alone unless --relock is passed.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import models, transaction

from hris.dd_workbook_import import parse_workbook
from hris.models import HRDocument
from hris.talent_cockpit_models import DevelopmentDialogue
from payroll.models import Employee

DEFAULT_PERIOD = 'FY2025'


class Command(BaseCommand):
    help = 'Import Development Dialogue workbooks from the HR vault into 9-box rows.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually write. Without it, nothing is saved.')
        parser.add_argument('--period', default=DEFAULT_PERIOD)
        parser.add_argument('--relock', action='store_true',
                            help='Refresh rows that are already locked (default: skip them).')

    def handle(self, *args, **opts):
        period = opts['period'].strip() or DEFAULT_PERIOD
        commit = bool(opts['commit'])
        docs = list(HRDocument.objects.filter(category__icontains='dialog')
                    .select_related('employee__employee'))
        created = updated = skipped_locked = failed = 0
        superseded = unplaceable = 0
        problems = []

        for doc in docs:
            name_hint = (doc.title or '').replace('Development Dialogue —', '').strip()
            fname = str(getattr(doc.file, 'name', '') or '')
            if not fname.lower().endswith(('.xlsx', '.xlsm')):
                failed += 1
                problems.append(f'{name_hint or fname}: not an Excel workbook ({fname[-8:]})')
                continue
            try:
                doc.file.open('rb')
                parsed = parse_workbook(doc.file)
            except Exception as e:    # noqa: BLE001
                failed += 1
                problems.append(f'{name_hint}: {type(e).__name__}')
                continue
            finally:
                try:
                    doc.file.close()
                except Exception:    # noqa: BLE001
                    pass

            if parsed.get('error'):
                failed += 1
                problems.append(f'{name_hint}: {parsed["error"]}')
                continue
            # BOTH axes or nothing. A one-axis workbook would write a LOCKED,
            # permanent row whose other axis silently defaults to 0.5 — a
            # fabricated "medium" on somebody's record (Fable, 2026-07-31).
            if parsed.get('performance') is None or parsed.get('potential') is None:
                failed += 1
                missing = 'performance' if parsed.get('performance') is None else 'potential'
                problems.append(f'{name_hint}: no {missing} total — skipped rather than guessed')
                continue

            emp = self._employee_for(doc, parsed, name_hint)
            # NAME PRECEDENCE: the payroll link first, then the vault document's
            # own title, and only then the workbook cell. Row 3 of the Nine Box
            # sheet is supposed to hold the employee's name, but in several real
            # files it holds the SUPERVISOR's — the live run labelled three
            # different people "Arun". The document title
            # ("Development Dialogue — <Name>") is what Dorothy actually filed it
            # under, so it beats a cell that demonstrably lies.
            person = (getattr(emp, 'full_name', '') or name_hint
                      or parsed.get('name') or '').strip()
            email = (getattr(emp, 'email', '') or '').strip()
            if not person and not email:
                failed += 1
                problems.append(f'{fname}: cannot identify the person')
                continue

            key = (email or person).lower()
            ref = f'{key}::{period}'[:80]     # ref column is 80 chars
            row = DevelopmentDialogue.objects.filter(ref=ref).first()
            if row is not None and row.locked and not opts['relock']:
                skipped_locked += 1
                continue

            if not commit:
                created += 1 if row is None else 0
                updated += 1 if row is not None else 0
                continue

            with transaction.atomic():
                row = row or DevelopmentDialogue(ref=ref)
                is_new = row.pk is None
                row.person_key = key
                row.name = person[:200]
                row.email = email[:200]
                row.department = (parsed.get('department')
                                  or getattr(emp, 'department', '') or '')[:200]
                row.position = (parsed.get('position')
                                or getattr(emp, 'job_title', '') or '')[:200]
                row.period = period[:120]
                if parsed.get('performance') is not None:
                    row.performance = parsed['performance']
                if parsed.get('potential') is not None:
                    row.potential = parsed['potential']
                row.overall = parsed.get('overall')
                row.employee = emp
                # Do NOT make this the person's current dialogue if they already
                # have a different one. Two current rows means my-dialogue can hand
                # someone the locked FY2025 record instead of their live draft, and
                # every future period clones them twice (Fable, 2026-07-31).
                other_current = (DevelopmentDialogue.objects
                                 .filter(is_current=True)
                                 .filter(models.Q(email__iexact=row.email) if row.email
                                         else models.Q(person_key=key))
                                 .exclude(ref=ref).exists())
                row.is_current = not other_current
                if other_current:
                    superseded += 1
                row.locked = True          # FY2025 is closed — permanent record
                payload = dict(row.payload or {})
                payload.update({
                    'source': 'FY2025 workbook import',
                    'source_document': fname,
                    'raw_performance': parsed.get('raw_performance'),
                    'raw_potential': parsed.get('raw_potential'),
                    'name': person, 'dept': row.department, 'period': period,
                    'performance': row.performance, 'potential': row.potential,
                    'overall': row.overall,
                })
                row.payload = payload
                row.save()
                if not row.email and row.employee_id is None:
                    unplaceable += 1
                    problems.append(f'{person}: saved but has no email and no payroll '
                                    f'link — cannot appear on the 9-box grid')
                created, updated = (created + 1, updated) if is_new else (created, updated + 1)

        verb = 'would be' if not commit else ''
        self.stdout.write(self.style.SUCCESS(
            f'{len(docs)} document(s): {created} created{verb and " " + verb}, {updated} updated, '
            f'{skipped_locked} already locked (skipped), {failed} could not be parsed.'))
        if superseded:
            self.stdout.write(self.style.WARNING(
                f'  {superseded} row(s) kept as history — the person already has a current dialogue.'))
        if unplaceable:
            self.stdout.write(self.style.WARNING(
                f'  {unplaceable} row(s) cannot reach the grid (no email, no payroll link).'))
        for p in problems:
            self.stdout.write(self.style.WARNING(f'  ! {p}'))
        if not commit:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing was written. Re-run with --commit.'))

    def _employee_for(self, doc, parsed, name_hint):
        """The payroll record this workbook is about: the document's own link
        first, then email/name. Never guesses between duplicate names."""
        prof = getattr(doc, 'employee', None)
        emp = getattr(prof, 'employee', None)
        if emp is not None:
            return emp
        for candidate in (parsed.get('name'), name_hint):
            nm = (candidate or '').strip()
            if not nm:
                continue
            hits = list(Employee.objects.filter(full_name__iexact=nm, status='active')[:2])
            if len(hits) == 1:
                return hits[0]
        return None
