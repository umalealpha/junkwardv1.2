"""
Management command: import_test_statement

Creates an FNB CSV bank statement format, a BankAccount for GL account 1110,
imports a 6-line test statement, runs the reconciliation engine, and prints
a full reconciliation report.

Test transactions
-----------------
  1. +9,120  REF-TEST-CUST-001    Payment received Alpha Motors   -> PAY-IN  (95%)
  2. -9,000  REF-TEST-BROKER-001  Broker commission payment       -> PAY-OUT (95%, net of WHT)
  3.   -570  (no ref)             Vendor payment Office Depot     -> unmatched
  4.    -50  (no ref)             Monthly account charges         -> excluded (pre-excluded)
  5.   +500  (no ref)             Unknown deposit                 -> unmatched
  6.   -120  (no ref)             Office stationery               -> unmatched
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from banking.models import BankAccount, BankStatementFormat, BankStatementLine
from banking.services import (
    BankStatementImporter,
    ReconciliationEngine,
    get_reconciliation_report,
)
from ledger.models import Account


TEST_CSV = (
    "Date,Transaction Description,Reference Number,Amount,Balance\n"
    "24/03/2026,Payment received Alpha Motors,REF-TEST-CUST-001,9120.00,9120.00\n"
    "24/03/2026,Broker commission payment,REF-TEST-BROKER-001,-9000.00,120.00\n"
    "24/03/2026,Vendor payment Office Depot,,-570.00,-450.00\n"
    "24/03/2026,Monthly account charges,,-50.00,-500.00\n"
    "24/03/2026,Unknown deposit,,500.00,0.00\n"
    "24/03/2026,Office stationery,,-120.00,-120.00\n"
)


class Command(BaseCommand):
    help = 'Import a test bank statement and run the reconciliation engine.'

    def handle(self, *args, **options):
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stderr.write('No superuser found. Run setup_initial_data first.')
            return

        # 1. Ensure FNB statement format exists
        fmt = self._get_or_create_format()

        # 2. Ensure BankAccount for GL account 1110 exists
        bank_account = self._get_or_create_bank_account(admin_user)
        if not bank_account:
            return

        # 3. Idempotency check
        existing = bank_account.statements.filter(
            file_name='test_fnb_statement.csv'
        ).first()
        if existing:
            self.stdout.write(
                f'  Skipped import — {existing.statement_number} already exists.'
            )
            statement = existing
        else:
            # 4. Import the test CSV
            self.stdout.write(self.style.MIGRATE_HEADING('\nImporting test statement...'))
            importer  = BankStatementImporter(fmt)
            statement = importer.import_csv(
                TEST_CSV,
                bank_account,
                user      = admin_user,
                file_name = 'test_fnb_statement.csv',
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f'  Imported: {statement.statement_number}'
                    f'  lines={statement.line_count}'
                    f'  closing={statement.closing_balance}'
                )
            )

            # 5. Pre-exclude bank charges before auto-matching
            charges_line = statement.lines.filter(
                description__icontains='account charges'
            ).first()
            if charges_line:
                charges_line.match_status = BankStatementLine.MatchStatus.EXCLUDED
                charges_line.notes        = 'Bank charges - no payment match'
                charges_line.save()
                self.stdout.write(
                    f'  Excluded bank charges (line {charges_line.line_number})'
                )

        # 6. Run the reconciliation engine
        self.stdout.write(self.style.MIGRATE_HEADING('\nRunning reconciliation engine...'))
        engine = ReconciliationEngine(statement)
        result = engine.run()
        self.stdout.write(
            self.style.SUCCESS(
                f'  Matched: {result["matched"]}'
                f'  Unmatched: {result["unmatched"]}'
                f'  Total: {result["total"]}'
            )
        )

        # 7. Print line-by-line results
        self.stdout.write(self.style.MIGRATE_HEADING('\nStatement Lines:'))
        hdr = (
            f"  {'#':<4} {'Date':<12} {'Description':<34} "
            f"{'Ref':<22} {'Amount':>10} {'Status':<20} Match"
        )
        self.stdout.write(hdr)
        self.stdout.write('  ' + '-' * 112)

        for line in (
            statement.lines
            .select_related('matched_payment')
            .order_by('line_number')
        ):
            match_info = ''
            if line.matched_payment:
                match_info = (
                    f"{line.matched_payment.payment_number} "
                    f"({line.match_confidence}%)"
                )
            self.stdout.write(
                f"  {line.line_number:<4} "
                f"{str(line.transaction_date):<12} "
                f"{line.description[:34]:<34} "
                f"{(line.reference or '')[:22]:<22} "
                f"{str(line.amount):>10} "
                f"{line.match_status:<20} "
                f"{match_info}"
            )

        # 8. Reconciliation report
        self.stdout.write(self.style.MIGRATE_HEADING('\nReconciliation Report:'))
        report = get_reconciliation_report(statement)

        self.stdout.write(
            f"  Bank closing balance  : "
            f"{report['bank_closing_balance'] if report['bank_closing_balance'] is not None else 'unknown':>12}"
        )
        self.stdout.write(
            f"  GL account balance    : {report['gl_balance']:>12}"
        )
        self.stdout.write(
            f"  Difference            : "
            f"{report['difference'] if report['difference'] is not None else 'unknown':>12}"
        )

        lc = report['lines']
        self.stdout.write(f"\n  Total lines           : {lc['total']}")
        self.stdout.write(f"  Auto-matched          : {lc['auto_matched']}")
        self.stdout.write(f"  Manually matched      : {lc['manually_matched']}")
        self.stdout.write(f"  Excluded              : {lc['excluded']}")
        self.stdout.write(f"  Unmatched             : {lc['unmatched']}")

        if report['unmatched_lines'].exists():
            self.stdout.write('\n  Unmatched lines:')
            for line in report['unmatched_lines']:
                self.stdout.write(
                    f"    L{line.line_number:03d}  {line.transaction_date}"
                    f"  {str(line.amount):>10}  {line.description[:50]}"
                )

        self.stdout.write('')
        if report['is_reconciled']:
            self.stdout.write(
                '  ' + self.style.SUCCESS(
                    'RECONCILED - Bank balance matches GL balance'
                )
            )
        else:
            self.stdout.write(
                '  ' + self.style.WARNING(
                    f'NOT RECONCILED - Difference: {report["difference"]}'
                )
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_create_format(self):
        fmt, created = BankStatementFormat.objects.get_or_create(
            name='FNB BWP Current Account',
            defaults={
                'bank_name':          'First National Bank',
                'delimiter':          ',',
                'encoding':           'utf-8',
                'skip_rows':          0,
                'date_column':        'Date',
                'date_format':        '%d/%m/%Y',
                'description_column': 'Transaction Description',
                'reference_column':   'Reference Number',
                'amount_column':      'Amount',
                'balance_column':     'Balance',
                'sign_convention':    BankStatementFormat.SignConvention.DEBIT_NEGATIVE,
            },
        )
        verb = 'Created' if created else 'Using'
        self.stdout.write(f'  {verb} format: {fmt.name}')
        return fmt

    def _get_or_create_bank_account(self, user):
        try:
            gl_account = Account.objects.get(code='1110')
        except Account.DoesNotExist:
            self.stderr.write(
                '  Account 1110 not found. Run setup_chart_of_accounts first.'
            )
            return None

        # Ensure the GL account is flagged as a bank account
        if not gl_account.is_bank_account:
            Account.objects.filter(pk=gl_account.pk).update(is_bank_account=True)
            gl_account.refresh_from_db()
            self.stdout.write('  Set account 1110 is_bank_account=True')

        existing = BankAccount.objects.filter(gl_account=gl_account).first()
        if existing:
            self.stdout.write(f'  Using BankAccount   : {existing}')
            return existing

        bank_account = BankAccount(
            gl_account       = gl_account,
            bank_name        = 'First National Bank',
            account_name     = 'FNB BWP Operating Account',
            account_number   = '62012345678',
            branch_code      = '280172',
            currency_code_id = 'BWP',
            is_active        = True,
        )
        bank_account.save(audit_user=user)
        self.stdout.write(f'  Created BankAccount : {bank_account}')
        return bank_account
