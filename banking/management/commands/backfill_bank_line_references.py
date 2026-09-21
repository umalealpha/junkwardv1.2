"""
Fill in CR-001's claim / invoice / payment-basis fields on bank statement lines
that were imported BEFORE those fields existed.

WHY THIS IS A COMMAND AND NOT A DATA MIGRATION
----------------------------------------------
These rows are live financial records. A data migration would rewrite every one
of them the moment the release is deployed, with nobody having chosen it and no
way to look at the result first. So the schema migration adds the columns and
stops; filling them is a deliberate act the CFO triggers, can preview, and can
run again later without harm.

It is RE-RUNNABLE by design. By default it only touches rows that still have no
claim reference, no invoice reference and an UNKNOWN basis, so running it twice
changes nothing the second time. `--all` re-derives every row — use it after the
parser in banking/line_references.py has been improved, and only then.

It is DRY RUN BY DEFAULT. Nothing is written unless you pass --commit.

    # Look first — writes nothing:
    python manage.py backfill_bank_line_references

    # Then write:
    python manage.py backfill_bank_line_references --commit

    # Narrow it down:
    python manage.py backfill_bank_line_references --from 2026-07-01 --to 2026-07-31
    python manage.py backfill_bank_line_references --statement BS-000123 --commit

This command NEVER moves money, never posts a journal and never touches an
amount, a date or a dedupe key. It writes three derived text fields and nothing
else.
"""
from __future__ import annotations

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from banking.line_references import PaymentBasis, extract_line_references
from banking.models import BankStatementLine


class Command(BaseCommand):
    help = ('Fill CR-001 claim/invoice/basis fields on bank statement lines '
            'imported before those fields existed. Dry run unless --commit.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--commit', action='store_true',
            help='Actually write. Without this the command only reports.',
        )
        parser.add_argument(
            '--all', action='store_true',
            help='Re-derive EVERY line, including ones already filled in. Use '
                 'only after the parser has changed.',
        )
        parser.add_argument('--from', dest='date_from', default=None,
                            help='Only lines on/after this date (YYYY-MM-DD).')
        parser.add_argument('--to', dest='date_to', default=None,
                            help='Only lines on/before this date (YYYY-MM-DD).')
        parser.add_argument('--statement', default=None,
                            help='Only lines on this statement number.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Stop after this many lines (0 = no limit).')

    def handle(self, *args, **opts):
        qs = BankStatementLine.objects.all().order_by('transaction_date', 'id')

        if not opts['all']:
            qs = qs.filter(
                claim_reference='', invoice_reference='',
            ).filter(payment_basis__in=['', PaymentBasis.UNKNOWN])

        for flag, lookup in (('date_from', 'transaction_date__gte'),
                             ('date_to', 'transaction_date__lte')):
            raw = opts[flag]
            if raw:
                try:
                    qs = qs.filter(**{lookup: date.fromisoformat(raw)})
                except ValueError:
                    raise CommandError(
                        f'--{flag.replace("date_", "")} must be YYYY-MM-DD, got {raw!r}'
                    )

        if opts['statement']:
            qs = qs.filter(statement__statement_number=opts['statement'])
        if opts['limit']:
            qs = qs[:opts['limit']]

        considered = changed = 0
        by_basis: dict[str, int] = {}
        samples: list[str] = []

        # Chunked so a full-history run does not pull every line into memory.
        for line in qs.iterator(chunk_size=500):
            considered += 1
            refs = extract_line_references(line.description, line.reference)
            new = (refs.claim_reference, refs.invoice_reference, refs.payment_basis)
            old = (line.claim_reference, line.invoice_reference,
                   line.payment_basis or PaymentBasis.UNKNOWN)
            if new == old:
                continue
            changed += 1
            by_basis[refs.payment_basis] = by_basis.get(refs.payment_basis, 0) + 1
            if len(samples) < 10:
                samples.append(
                    f'  {line.transaction_date}  {str(line.description)[:44]:44} '
                    f'-> claim={refs.claim_reference or "-":14} '
                    f'invoice={refs.invoice_reference or "-":12} '
                    f'basis={refs.payment_basis}'
                )
            if opts['commit']:
                line.claim_reference = refs.claim_reference
                line.invoice_reference = refs.invoice_reference
                line.payment_basis = refs.payment_basis
                # update_fields is the whole safety story: three derived text
                # columns, nothing else on the row can be touched by this.
                with transaction.atomic():
                    line.save(update_fields=[
                        'claim_reference', 'invoice_reference', 'payment_basis',
                    ])

        self.stdout.write('')
        self.stdout.write(f'Lines considered : {considered}')
        self.stdout.write(f'Lines that differ: {changed}')
        for basis, n in sorted(by_basis.items()):
            self.stdout.write(f'  {PaymentBasis.LABELS.get(basis, basis):38} {n}')
        if samples:
            self.stdout.write('')
            self.stdout.write('First few:')
            for s in samples:
                self.stdout.write(s)
        self.stdout.write('')
        if opts['commit']:
            self.stdout.write(self.style.SUCCESS(
                f'WRITTEN: {changed} line(s) updated.'))
        else:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — nothing was written. Re-run with --commit to write.'))
