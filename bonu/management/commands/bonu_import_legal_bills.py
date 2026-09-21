"""Load the panel firms' fee-note register into the BONU legal bill register.

    manage.py bonu_import_legal_bills --file rows.json [--dry-run]

The file is a JSON list of the register's own rows (see REQUIRED_COLUMNS in
bonu/legal_bill_import.py). The command is safe to run twice: `source_row` is
the key, so a second run updates the rows it loaded before instead of adding a
second copy of the register.

It refuses to write anything if ANY row fails to parse. A part-loaded register
is worse than no register — it foots to nothing and nobody can tell which half
is missing.
"""

import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from bonu.legal_bill_import import RowError, canonical_firm, normalise_firm, parse_row, reconcile
from bonu.legal_rules import bill_dup_key
from bonu.models import LawFirm, LegalBill


class Command(BaseCommand):
    help = "Load the panel firms' fee-note register (what we were billed and what we paid)."

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True,
                            help='JSON list of register rows.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse, match firms and print the reconciliation; write nothing.')
        parser.add_argument('--captured-by', default='import@alphadirect.co.bw',
                            help='Recorded against every row as who loaded it.')

    def handle(self, *args, **options):
        with open(options['file'], encoding='utf-8') as fh:
            rows = json.load(fh)
        if not isinstance(rows, list) or not rows:
            raise CommandError('The file must be a non-empty JSON list of rows.')

        parsed, errors = [], []
        for row in rows:
            try:
                parsed.append(parse_row(row))
            except RowError as exc:
                errors.append(str(exc))
        if errors:
            for e in errors[:20]:
                self.stderr.write(self.style.ERROR(e))
            raise CommandError(f'{len(errors)} row(s) could not be read. Nothing was written — '
                               'a half-loaded register foots to nothing.')

        totals = reconcile(parsed)

        # ---- match every firm BEFORE writing anything ------------------------
        # A firm created mid-load leaves the register split across a real panel
        # record and a new near-duplicate, and the retainer scorecard reads the
        # real one. So resolve the whole set first and say what happened.
        existing = {normalise_firm(f.name): f for f in LawFirm.objects.all()}
        matched, to_create, aliased = {}, [], []
        for key in sorted({p['firm_key'] for p in parsed}):
            firm = existing.get(key)
            if firm is not None:
                matched[key] = firm
                raw_names = sorted({p['raw_firm'] for p in parsed if p['firm_key'] == key})
                if not any(normalise_firm(n) == key for n in raw_names):
                    aliased.append((raw_names, firm.name))
            else:
                to_create.append(key)

        self.stdout.write(f'Rows read: {totals["rows"]}   firms: {totals["firms"]}')
        self.stdout.write(f'  matched to the existing panel list: {len(matched)}')
        self.stdout.write(f'  new firms to create: {len(to_create)}')
        for key in to_create:
            raw = sorted({p['raw_firm'] for p in parsed if p['firm_key'] == key})
            self.stdout.write(f'      NEW  {key}   (as written: {", ".join(raw)})')
        for raw_names, canonical in aliased:
            self.stdout.write(self.style.WARNING(
                f'      ALIASED  {", ".join(raw_names)}  ->  {canonical}'))

        self.stdout.write('')
        self.stdout.write(f'  {"":<12} {"as the file wrote it":>22}   {"as stored (2 dp)":>18}')
        for label, src, kept in (
                ('Invoiced', 'source_invoiced', 'invoiced'),
                ('Discount', 'source_discount', 'discount'),
                ('Paid', 'source_paid', 'paid'),
                ('Outstanding', 'source_outstanding', 'outstanding')):
            self.stdout.write(
                f'  {label:<12} {totals[src]:>22,.3f}   {totals[kept]:>18,.2f}')
        self.stdout.write(
            f'  Rounding to the two decimals the column stores moves the '
            f'outstanding by {totals["rounding_difference"]:,.3f}.')
        self.stdout.write(f'  Payments with no readable date: {totals["paid_without_date"]}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('\nDRY RUN — nothing written.'))
            return

        with transaction.atomic():
            for key in to_create:
                raw = sorted({p['raw_firm'] for p in parsed if p['firm_key'] == key})[0]
                matched[key] = LawFirm.objects.create(
                    name=raw,
                    notes='Created by the fee-note register import — this firm billed BONU '
                          'but was not on the panel list.')

            created = updated = 0
            for p in parsed:
                firm = matched[p['firm_key']]
                defaults = {
                    'source': LegalBill.Source.EXTERNAL,
                    'firm': firm,
                    'reference': p['reference'],
                    'bill_date': p['bill_date'],
                    'amount': p['amount'],
                    'discount': p['discount'],
                    'amount_paid': p['amount_paid'],
                    'paid_on': p['paid_on'],
                    'stage': p['stage'],
                    'billed_client_name': p['billed_client_name'],
                    'allocation_state': LegalBill.Allocation.UNALLOCATED,
                    'dup_key': bill_dup_key(firm.name, p['amount'], p['reference']),
                    'note': p['note'],
                    'captured_by_email': options['captured_by'],
                }
                _, was_created = LegalBill.objects.update_or_create(
                    source_row=p['source_row'], defaults=defaults)
                created += 1 if was_created else 0
                updated += 0 if was_created else 1

            # ---- prove the register in the database equals the file ----------
            # Inside the transaction on purpose: a load that does not reconcile
            # must leave nothing behind. Raising after the block had closed would
            # have committed all 700 rows and then complained about them.
            # The rows are re-read from the database rather than trusted from
            # memory, so this compares what was STORED against what was parsed.
            loaded = LegalBill.objects.filter(source_row__isnull=False)
            db_invoiced = sum((b.amount for b in loaded), Decimal('0'))
            db_paid = sum((b.amount_paid for b in loaded), Decimal('0'))
            db_discount = sum((b.discount for b in loaded), Decimal('0'))
            db_rows = loaded.count()
            mismatches = [
                f'{name}: database {got:,.2f} against file {want:,.2f}'
                for name, got, want in (
                    ('invoiced', db_invoiced, totals['invoiced']),
                    ('discount', db_discount, totals['discount']),
                    ('paid', db_paid, totals['paid']))
                if got != want]
            if db_rows != totals['rows']:
                mismatches.append(f'rows: database {db_rows} against file {totals["rows"]}')
            if mismatches:
                raise CommandError(
                    'The loaded register does not equal the file it came from, so '
                    'nothing has been written: ' + '; '.join(mismatches))

        self.stdout.write('')
        self.stdout.write(f'Written: {created} new, {updated} updated.')
        self.stdout.write(f'  In the database now: {db_rows} bills, '
                          f'invoiced {db_invoiced:,.2f}, discount {db_discount:,.2f}, '
                          f'paid {db_paid:,.2f}')
        self.stdout.write(self.style.SUCCESS(
            '  The database equals the file, at the two decimals the column stores.'))

        dups = (LegalBill.objects.filter(source_row__isnull=False)
                .values_list('dup_key', flat=True))
        seen, repeated = set(), set()
        for k in dups:
            (repeated if k in seen else seen).add(k)
        if repeated:
            self.stdout.write(self.style.WARNING(
                f'  {len(repeated)} reference(s) billed more than once — see the '
                'duplicates on the Legal bills screen.'))
