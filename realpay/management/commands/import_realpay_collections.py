"""Import RealPay Collections source CSVs into the row-level tables.

CFO directive 2026-06-05 (RealPay Collections module). Loads the three reports
that power the Failed/Error Debit Tracker (Objective 1) and the Collections
Dashboard (Objective 2):

  --txn      RealPay Transaction Report CSV   -> RealPayTransaction(source=transaction)
  --billing  Client Billing detail CSV        -> RealPayTransaction(source=billing)
  --codes    Response Code Report CSV         -> RealPayResponseCode

Each provided source is reloaded idempotently (existing rows for that source
are deleted, then re-inserted) so re-running never double-counts.

Validated column names (see build-spec + live CSV headers):
  Transaction Report : Installment Date, Merchant, ClientNumber, ClientName,
                       ContractNumber, ContractSequence, InstSeq,
                       InstallmentAmount, TotalAmount, Collected Amount,
                       Current Status, Result, Client Bank
  Client Billing     : Product, BeneficiaryNumber, Transaction Date,
                       ContractSequence, InstallmentSequence, ClientNumber,
                       AmountRequested, AmountCollected, Result
  Response Codes     : Product Code, Response Code, Response Description

Date formats handled: 'YYYY/MM/DD HH:MM' (txn) and 'YYYY-MM-DD HH:MM' (billing).
Amounts handled: plain ('79', '0') and thousands-comma ('1,077.62'); blank -> 0.

Usage (on prod, CSVs uploaded to MEDIA_ROOT/realpay-collections/):
  python manage.py import_realpay_collections \
      --codes   "/app/media/realpay-collections/Response Codes Report.csv" \
      --txn     "/app/media/realpay-collections/transaction report.csv" \
      --billing "/app/media/realpay-collections/client_billing.csv"
"""
from __future__ import annotations

import csv
import datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

from realpay.models import (
    RealPayImportControl,
    RealPayResponseCode,
    RealPayTransaction,
    normalize_response_code,
)

ZERO = Decimal('0.00')
CHUNK = 2000


def _dec(raw) -> Decimal:
    """Parse a money cell tolerant of thousands-commas and the common reversal
    notations (parenthesised and trailing-minus = negative). Non-finite or
    unparseable values return 0 (the live RealPay export uses none of these,
    but we harden rather than silently mis-sum — forensic-audit fix 2026-06-05)."""
    s = ('' if raw is None else str(raw)).strip().replace(',', '')
    if not s or s in ('-', 'NULL', 'null', 'None'):
        return ZERO
    neg = False
    if s.startswith('(') and s.endswith(')'):    # (50.00) -> -50.00
        s, neg = s[1:-1].strip(), True
    elif s.endswith('-'):                          # 50.00- -> -50.00
        s, neg = s[:-1].strip(), True
    # strip any leading currency symbol/letters (e.g. R50.00, P50.00)
    s = s.lstrip('RP$ \t')
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return ZERO
    if not d.is_finite():        # reject NaN / Infinity so SUM never poisons
        return ZERO
    return -d if neg else d


def _date(raw):
    """Parse the date portion of a RealPay timestamp; tolerant of / or - and
    an optional trailing time. Returns a datetime.date or None."""
    s = ('' if raw is None else str(raw)).strip()
    if not s:
        return None
    datepart = s.split(' ')[0].replace('/', '-')
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%m-%d-%Y'):
        try:
            return datetime.datetime.strptime(datepart, fmt).date()
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = 'Import RealPay Collections CSVs (transaction report, client billing, response codes).'

    def add_arguments(self, parser):
        parser.add_argument('--txn', help='Path to the RealPay Transaction Report CSV.')
        parser.add_argument('--billing', help='Path to the Client Billing detail CSV.')
        parser.add_argument('--codes', help='Path to the Response Code Report CSV.')
        parser.add_argument('--batch', default='', help='Optional import batch tag.')
        parser.add_argument(
            '--replace-from', dest='replace_from', default='',
            help=('Tail-refresh mode (YYYY-MM-DD): delete only existing rows for the '
                  'source with txn_date >= this date, then insert the CSV — instead of '
                  'wiping the whole source. Use to extend/refresh a month (e.g. load a '
                  'June-only export with --replace-from 2026-06-01) without touching '
                  'earlier history. Omit for the default full-source replace.'))

    def handle(self, *args, **opts):
        if not any(opts.get(k) for k in ('txn', 'billing', 'codes')):
            raise CommandError('Provide at least one of --txn / --billing / --codes.')
        batch = opts['batch'] or datetime.datetime.now().strftime('imp%Y%m%d%H%M')

        replace_from = None
        if opts.get('replace_from'):
            replace_from = _date(opts['replace_from'])
            if replace_from is None:
                raise CommandError(f"--replace-from must be YYYY-MM-DD: {opts['replace_from']!r}")

        if opts.get('codes'):
            self._load_codes(opts['codes'])
        if opts.get('txn'):
            self._load_txn(opts['txn'], batch, replace_from)
        if opts.get('billing'):
            self._load_billing(opts['billing'], batch, replace_from)
        self.stdout.write(self.style.SUCCESS('RealPay Collections import complete.'))

    # ---- response codes -------------------------------------------------
    def _load_codes(self, path):
        rows = []
        seen = set()
        with open(path, encoding='utf-8-sig', errors='ignore', newline='') as f:
            for r in csv.DictReader(f):
                pc = (r.get('Product Code') or '').strip()
                rc = (r.get('Response Code') or '').strip()
                if not pc or not rc:
                    continue
                key = (pc, rc)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(RealPayResponseCode(
                    product_code=pc,
                    response_code=rc,
                    code_norm=normalize_response_code(rc),
                    description=(r.get('Response Description') or '').strip()[:200],
                ))
        with db_transaction.atomic():
            RealPayResponseCode.objects.all().delete()
            RealPayResponseCode.objects.bulk_create(rows, batch_size=CHUNK)
        self.stdout.write(self.style.SUCCESS(f'Response codes loaded: {len(rows)}'))

    # ---- transaction report (Objective 1) -------------------------------
    def _load_txn(self, path, batch, replace_from=None):
        rows = []
        n = 0
        with open(path, encoding='utf-8-sig', errors='ignore', newline='') as f:
            for r in csv.DictReader(f):
                rc = (r.get('Result') or '').strip()
                rows.append(RealPayTransaction(
                    source=RealPayTransaction.Source.TRANSACTION,
                    txn_date=_date(r.get('Installment Date')),
                    client_number=(r.get('ClientNumber') or '').strip()[:40],
                    client_name=(r.get('ClientName') or '').strip()[:160],
                    product='',  # Transaction Report has no product code (OD-6)
                    merchant=(r.get('Merchant') or '').strip()[:120],
                    contract_number=(r.get('ContractNumber') or '').strip()[:40],
                    contract_sequence=(r.get('ContractSequence') or '').strip()[:20],
                    inst_seq=(r.get('InstSeq') or '').strip()[:20],
                    installment_amount=_dec(r.get('InstallmentAmount')),
                    total_amount=_dec(r.get('TotalAmount')),
                    collected_amount=_dec(r.get('Collected Amount')),
                    current_status=(r.get('Current Status') or '').strip().upper()[:20],
                    result_code=rc[:20],
                    result_code_norm=normalize_response_code(rc),
                    client_bank=(r.get('Client Bank') or '').strip()[:60],
                    import_batch=batch,
                ))
                n += 1
        self._replace(RealPayTransaction.Source.TRANSACTION, rows, batch, replace_from)
        self.stdout.write(self.style.SUCCESS(f'Transaction Report rows loaded: {n}'))

    # ---- client billing (Objective 2) -----------------------------------
    def _load_billing(self, path, batch, replace_from=None):
        """Load a Client Billing export through the shared billing importer.

        This used to parse the CSV itself and then call _replace, which DELETES
        every billing row for the source before writing. That made the command
        and the upload path fight each other: one run of this command wiped
        everything the upload had loaded, dropped tracking_start_date back to
        NULL, and silently flipped the dashboard off the billing basis and back
        onto the Graphite estimate.

        So it now uses the same parser and the same upsert as the upload
        (Bokani, bug 6a48367f). merge_rows is idempotent on row_key, so a
        re-run corrects rows instead of clearing them, and replace_from is no
        longer needed — nothing is being replaced.
        """
        from realpay.billing_import import (BillingParseError, merge_rows,
                                            parse_client_billing)
        with open(path, 'rb') as f:
            data = f.read()
        try:
            parsed = parse_client_billing(data, path)
        except BillingParseError as e:
            raise CommandError(str(e))
        result = merge_rows(parsed['rows'], batch)
        self.stdout.write(self.style.SUCCESS(
            f"Client Billing rows loaded: {result.get('rows_read', len(parsed['rows']))} "
            f"(new {result.get('created', '?')}, updated {result.get('updated', '?')})"))

    # ---- shared idempotent replace --------------------------------------
    def _replace(self, source, rows, batch, replace_from=None):
        settled = [r for r in rows if (r.collected_amount or ZERO) > ZERO]
        collected_sum = sum((r.collected_amount for r in settled), ZERO)
        with db_transaction.atomic():
            if replace_from is not None:
                # Tail refresh: only clear the overlapping date range for this
                # source (txn_date >= replace_from), keep earlier history + its
                # control rows. Idempotent for re-loading the same tail range.
                deleted = (RealPayTransaction.objects
                           .filter(source=source, txn_date__gte=replace_from)
                           .delete())
                self.stdout.write(f'  tail-replace[{source}] from {replace_from}: '
                                  f'removed {deleted[0]} existing row(s)')
            else:
                # Default: full-source replace (wipe + reload).
                RealPayTransaction.objects.filter(source=source).delete()
                RealPayImportControl.objects.filter(source=source).delete()
            for i in range(0, len(rows), CHUNK):
                RealPayTransaction.objects.bulk_create(rows[i:i + CHUNK], batch_size=CHUNK)
            RealPayImportControl.objects.create(
                source=source, batch=batch,
                row_count=len(rows), settled_count=len(settled),
                collected_sum=collected_sum,
            )
        self.stdout.write(
            f'  control[{source}]: rows={len(rows)} settled={len(settled)} '
            f'collected_sum={collected_sum}')
