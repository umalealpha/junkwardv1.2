"""Import RealPay collections against subrogation cases (record funds).

RealPay runs a dedicated "Alpha Direct Third Parties" merchant where each
at-fault third party is a RealPay client whose CLIENT NUMBER is the claim
reference (verified against the live portal 2026-08-17 — e.g. 20180657,
G2025003787). When a third party pays, it shows on the RealPay successful-
collections report; this loads those as SubrogationReceipt rows.

Source = the RealPay Transaction / successful-collections CSV (same columns the
premium collections importer already reads). Confirmed header:
  Installment Date, Merchant, ClientNumber, ClientName, ContractNumber,
  ContractSequence, InstSeq, InstallmentAmount, TotalAmount, Collected Amount,
  ..., Current Status, Result, Client Bank

Rules (Keetile Mokhendo's spec + notebook safety):
  * Post SUCCESSFUL collections only (Current Status == SUCCESSFUL).
  * Match ClientNumber → Subrogation.claim_reference (normalised the same way
    the register loader normalises claim numbers). Unmatched rows are reported,
    never guessed onto a case.
  * Reject duplicated instalment lines: each receipt carries a stable
    realpay_txn_id (merchant client + contract sequence + instalment seq), and
    the model's partial-unique constraint refuses a second identical line.
  * Only the "Alpha Direct Third Parties" merchant is imported — a multi-merchant
    export (the sample also carried Genric Insurance) must not post other
    insurers' collections. Override the exact name with --merchant.
  * DRY RUN by default; --commit to write.
"""
from __future__ import annotations

import csv

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from claims.realpay_import import DEFAULT_MERCHANT, import_realpay_rows


class Command(BaseCommand):
    help = 'Import RealPay collections against subrogation cases. Dry run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--merchant', default=DEFAULT_MERCHANT,
                            help=f'RealPay merchant to import (default "{DEFAULT_MERCHANT}").')
        parser.add_argument('--created-by', default=None)
        parser.add_argument('--commit', action='store_true',
                            help='Actually write. Omitted = dry run, everything rolled back.')

    def handle(self, *args, **opts):
        try:
            with open(opts['csv_path'], newline='', encoding='utf-8-sig') as f:
                rows = list(csv.DictReader(f))
        except FileNotFoundError:
            raise CommandError(f"CSV not found: {opts['csv_path']}")
        if not rows:
            raise CommandError('CSV has no data rows.')

        creator = (User.objects.filter(username=opts['created_by']).first()
                   if opts['created_by'] else User.objects.filter(is_superuser=True).first())
        if creator is None:
            creator = User.objects.order_by('id').first()
        if creator is None:
            raise CommandError('No user available to stamp as creator.')

        stats, posted_amount, unmatched_refs = import_realpay_rows(
            rows, merchant=opts['merchant'], creator=creator, commit=opts['commit'])
        self._report(stats, posted_amount, unmatched_refs, opts['commit'])
        if not opts['commit']:
            self.stdout.write(self.style.SUCCESS(
                'Dry run — rolled back, nothing written. Re-run with --commit to post.'))

    def _report(self, stats, posted_amount, unmatched_refs, committed):
        w = self.stdout.write
        w('')
        w('RealPay → subrogation import — ' + ('COMMIT' if committed else 'DRY RUN'))
        w('-' * 52)
        for k, v in stats.items():
            w(f'  {k:16s} {v:>8}')
        w('-' * 52)
        w(f'  posted amount    {posted_amount:>12,.2f}')
        if unmatched_refs:
            shown = ', '.join(unmatched_refs[:10])
            more = f' (+{len(unmatched_refs) - 10} more)' if len(unmatched_refs) > 10 else ''
            w(self.style.WARNING(f'  UNMATCHED client numbers (no such claim): {shown}{more}'))
