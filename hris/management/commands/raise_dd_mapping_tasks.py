"""
Ask HR to map the Development Dialogues that could not be matched to a person.

    python manage.py raise_dd_mapping_tasks            # dry run
    python manage.py raise_dd_mapping_tasks --commit

CFO 2026-08-01: *"the development dialogs we couldn't match put it as a task for
Unami and Dorothy so they can themselves fix the email id and names of the files,
they should be able to map them."*

Two kinds of orphan come out of the FY2025 import:

  * **no owner** — the workbook carries no work email and its name does not match
    exactly one active employee, so the row exists but can never appear on the
    9-box grid;
  * **unreadable** — the file is not an Excel workbook (.csv, .xlsb), so no scores
    could be read at all.

Deliberately NOT auto-guessed. Fuzzy-matching "PHENYO M" or "8 DD Kutlo" against
the payroll roster would eventually attach somebody's appraisal to the wrong
person, which is worse than a missing row — HR knows who these people are, the
importer does not. So this raises a task and lists the evidence.

One open task per HR owner; re-running never duplicates it.
"""
from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand

from hris.models import HRDocument
from hris.talent_cockpit_models import DevelopmentDialogue
from django.utils import timezone

HR_EMAILS = ('ubutale@alphadirect.co.bw',        # Unami Butale — CHCO
             'dikgopoleng@alphadirect.co.bw')    # Dorothy Ikgopoleng — Human Capital
CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'
SOURCE = 'dd_mapping'
DEADLINE_DAYS = 5
DUE_TIME = datetime.time(16, 0)
PERIOD = 'FY2025'


def unmatched_rows(period: str = PERIOD):
    """FY2025 dialogues that exist but can never reach the grid."""
    out = []
    for d in DevelopmentDialogue.objects.filter(period=period).order_by('name'):
        if not (d.email or '').strip() and d.employee_id is None:
            src = ((d.payload or {}).get('source_document') or '').split('/')[-1]
            out.append((d.name or '(no name)', src))
    return out


def unreadable_documents():
    """Vault documents the importer cannot open at all."""
    out = []
    for doc in HRDocument.objects.filter(category__icontains='dialog'):
        name = str(getattr(doc.file, 'name', '') or '')
        if name and not name.lower().endswith(('.xlsx', '.xlsm')):
            out.append(((doc.title or '').replace('Development Dialogue —', '').strip(),
                        name.split('/')[-1]))
    return sorted(out)


def _body(rows, bad_files) -> str:
    lines = [
        f'{len(rows)} Development Dialogue(s) from the FY2025 pack are in omni but cannot '
        f'appear on the 9-Box grid, and {len(bad_files)} file(s) cannot be read at all.',
        '',
        'WHY: the workbook has no work email in it, and the name on the file does not match '
        'exactly one person on the payroll. We deliberately do NOT guess — attaching an '
        'appraisal to the wrong person is worse than leaving it off the grid. You know who '
        'these people are.',
        '',
        f'WHAT WE NEED, per line below: the person\'s FULL NAME as it appears on payroll, and '
        f'their work email. Reply on this task with the list and we will re-run the import; '
        f'they then appear on the grid straight away.',
        '',
        'NO OWNER — needs a name + work email:',
    ]
    for name, src in rows:
        lines.append(f'  • {name}   (file: {src})')
    if bad_files:
        lines += ['', 'CANNOT BE READ — please re-send these two as .xlsx:']
        for name, src in bad_files:
            lines.append(f'  • {name}   (file: {src})')
    lines += ['',
              'Nothing here is lost — every FY2025 review is stored and locked. This is only '
              'about connecting each one to the right person so it shows on the grid.']
    return '\n'.join(lines)


class Command(BaseCommand):
    help = 'Raise an HR task to map unmatched FY2025 Development Dialogues.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')
        parser.add_argument('--period', default=PERIOD)

    def handle(self, *args, **opts):
        from django.contrib.auth.models import User
        from core.models import OmniTask

        rows = unmatched_rows(opts['period'])
        bad = unreadable_documents()
        if not rows and not bad:
            self.stdout.write(self.style.SUCCESS('Nothing unmatched — no task needed.'))
            return

        cfo = User.objects.filter(email__iexact=CFO_EMAIL).first()
        assigner = cfo or User.objects.filter(is_superuser=True).order_by('id').first()
        if assigner is None:
            self.stderr.write(self.style.ERROR('No assigner user found.'))
            return

        title = (f'Map {len(rows)} Development Dialogue(s) to the right person '
                 f'({opts["period"]})')
        body = _body(rows, bad)
        due = timezone.localdate() + datetime.timedelta(days=DEADLINE_DAYS)
        open_states = [OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]
        made, skipped, missing = [], [], []

        for email in HR_EMAILS:
            who = User.objects.filter(email__iexact=email).first()
            if who is None:
                missing.append(email)
                continue
            if OmniTask.objects.filter(source=SOURCE, assignee=who,
                                       status__in=open_states).exists():
                skipped.append(email)
                continue
            if not opts['commit']:
                made.append(email)
                continue
            OmniTask.objects.create(
                assigner=assigner, assignee=who, title=title, body=body,
                priority=OmniTask.Priority.HIGH, source=SOURCE,
                due_at=due, due_time=DUE_TIME,
                week_of=due - datetime.timedelta(days=due.weekday()))
            made.append(email)

        verb = 'would raise' if not opts['commit'] else 'raised'
        self.stdout.write(self.style.SUCCESS(
            f'{len(rows)} unmatched + {len(bad)} unreadable → {verb} {len(made)} task(s): '
            f'{", ".join(made) or "none"}'))
        if skipped:
            self.stdout.write(self.style.WARNING(
                f'  already has an open task: {", ".join(skipped)}'))
        for m in missing:
            self.stdout.write(self.style.WARNING(f'  no omni user for {m} — not assigned'))
        if not opts['commit']:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing written.'))
