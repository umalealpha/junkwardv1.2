"""
Management command: import_vendor_bank_accounts

Bulk-loads vendor bank accounts from a spreadsheet or CSV. Each row creates a
DRAFT record — a Finance Manager still has to approve it before the account can
be used for an outbound payment.

This is now a thin wrapper around `procurement.vendor_bank_upload`, which is the
SAME code the upload screen on /vendor-banking runs. That matters: this command
used to carry its own rules, and it happily created a DRAFT on a DIFFERENT
account for an existing supplier — the exact thing the CFO asked to be refused
("if a person is changing the bank account details it rejects, saying 'Why are
you doing this because you paid this person with another bank account?'", 2026-08-20).
A control that holds on the screen and not on the command line is not a control.

Columns are matched by MEANING, so your own headings are fine — 'Supplier',
'Bankers', 'A/C No.', 'Sort Code' all work. Excel (xlsx / xlsb / xls / ods) and
CSV are both read. A template is at data/vendor_bank_accounts_template.csv.

Behaviour:
  - Reports what will happen to every row, then does only that.
  - A row for a supplier we already pay on a DIFFERENT account is HELD, never
    loaded. Those go through the screen one at a time, with a reason.
  - A row whose supplier is unknown creates the supplier, in --company.
  - --dry-run reports and writes nothing.

Usage:
  python manage.py import_vendor_bank_accounts --file data/suppliers.xlsx \
      --company ADIC --dry-run
  python manage.py import_vendor_bank_accounts --file data/suppliers.xlsx \
      --company ADIC --user prathap
"""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from procurement.vendor_bank_upload import Verdict, apply_plan, build_plan


class Command(BaseCommand):
    help = ('Bulk-import vendor bank accounts from a spreadsheet or CSV '
            '(creates DRAFT records, using the same rules as the screen).')

    def add_arguments(self, parser):
        parser.add_argument('--file', '--csv', dest='file', required=True,
                            help='Path to the .xlsx / .xlsb / .xls / .ods / .csv file.')
        parser.add_argument('--company', required=True,
                            help='Company code (e.g. ADIC) the suppliers belong '
                                 'to. Required: a supplier with no company is '
                                 'invisible in every company-filtered screen.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would happen and write nothing.')
        parser.add_argument('--user', default=None,
                            help='Username to record as the creator. Defaults '
                                 'to the first superuser.')

    def handle(self, *args, **options):
        from core.models import Company

        path = Path(options['file'])
        if not path.is_file():
            raise CommandError(f'File not found: {path}')

        company = (Company.objects.filter(code__iexact=options['company']).first()
                   or Company.objects.filter(id=options['company']).first()
                   if options['company'] else None)
        if company is None:
            raise CommandError(f'No company matching {options["company"]!r}.')

        username = options.get('user')
        if username:
            creator = User.objects.filter(username=username).first()
            if creator is None:
                raise CommandError(f'User {username} not found.')
        else:
            creator = User.objects.filter(is_superuser=True).order_by('pk').first()
            if creator is None:
                raise CommandError('No superuser found and no --user supplied.')

        plan = build_plan(path.read_bytes(), path.name, str(company.id))
        summary = plan.public()

        if summary['missing_columns']:
            missing = ', '.join(summary['missing_columns']).replace('_', ' ')
            raise CommandError(
                f'The sheet needs a column for: {missing}. Headings are matched '
                f'by meaning, so any sensible wording works.')

        self.stdout.write(f'\nRead {summary["total_rows"]} row(s) from {path.name}')
        self.stdout.write(f'  columns understood: {summary["columns_understood"]}')
        if summary['columns_ignored']:
            self.stdout.write(f'  columns ignored:    {summary["columns_ignored"]}')
        self.stdout.write('')
        for row in plan.rows:
            mark = '+' if row.will_write else '-'
            ends = f'…{row.account_number[-4:]}' if row.account_number else '—'
            self.stdout.write(
                f'  {mark} row {row.line:>4}  {(row.vendor_name or "?")[:38]:<38} '
                f'{ends:<8} {Verdict.LABELS.get(row.verdict, row.verdict)}'
                + (f' — {row.reason}' if row.reason else ''))

        self.stdout.write(self.style.SUCCESS(
            f'\n  will load:       {summary["will_load"]}\n'
            f'  held (bank changed): {summary["held"]}\n'
            f'  already on file: {summary["already_on_file"]}\n'
            f'  cannot be read:  {summary["rejected"]}\n'))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                'Dry run — nothing was written.'))
            return

        result = apply_plan(plan, creator, str(company.id))
        self.stdout.write(self.style.SUCCESS(
            f'Created {result["created"]} DRAFT bank account(s)'
            + (f' and {result["suppliers_created"]} new supplier(s)'
               if result['suppliers_created'] else '')
            + f' for {company.code}.'))
        for problem in result['problems']:
            self.stdout.write(self.style.ERROR(f'  FAILED {problem}'))
        if result['held']:
            self.stdout.write(self.style.WARNING(
                f'{result["held"]} row(s) were HELD because they would move a '
                f'supplier onto a different account. Add those on '
                f'/vendor-banking one at a time, so the reason is recorded.'))
        self.stdout.write(self.style.SUCCESS(
            '\nEverything created is a DRAFT. A Finance Manager must approve '
            'each one on /vendor-banking before it can be paid.'))
