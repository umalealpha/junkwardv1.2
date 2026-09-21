"""
Management command: run_all_reports

Runs all seven financial reports against the current data and prints
a human-readable summary to verify the numbers make sense.
"""

from datetime import date

from django.core.management.base import BaseCommand

from reporting.reports import (
    build_ar_aging,
    build_ap_aging,
    build_balance_sheet,
    build_cash_position,
    build_general_ledger,
    build_profit_loss,
    build_trial_balance,
)
from django.utils import timezone


def _get_fy_start(today=None):
    d = today or timezone.localdate()
    return date(d.year, 7, 1) if d.month >= 7 else date(d.year - 1, 7, 1)


class Command(BaseCommand):
    help = 'Run all financial reports and print a summary for verification.'

    def handle(self, *args, **options):
        today    = timezone.localdate()
        fy_start = _get_fy_start(today)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'\nAlpha Direct Financial Reports  |  as of {today}'
            )
        )

        self._trial_balance(today)
        self._profit_loss(fy_start, today)
        self._balance_sheet(today)
        self._ar_aging(today)
        self._ap_aging(today)
        self._cash_position()
        self._general_ledger('1110', fy_start, today)

    # ------------------------------------------------------------------

    def _trial_balance(self, today):
        self.stdout.write(self.style.MIGRATE_HEADING('\n1. TRIAL BALANCE'))
        tb = build_trial_balance(today)
        self.stdout.write(f"   Period: {tb['period_start']} to {tb['as_of']}")
        self.stdout.write(
            f"   {'Code':<8} {'Account':<38} {'Opening':>12} "
            f"{'Dr':>12} {'Cr':>12} {'Closing':>12}"
        )
        self.stdout.write('   ' + '-' * 98)
        for row in tb['accounts']:
            self.stdout.write(
                f"   {row['code']:<8} {row['name'][:38]:<38} "
                f"{row['opening_balance']:>12} "
                f"{row['period_debits']:>12} "
                f"{row['period_credits']:>12} "
                f"{row['closing_balance']:>12}"
            )
        self.stdout.write('   ' + '-' * 98)
        t = tb['totals']
        self.stdout.write(
            f"   {'PERIOD TOTALS':<47} {t['total_debits']:>12} {t['total_credits']:>12}"
        )
        ok = t['balanced']
        self.stdout.write(
            '   ' + (
                self.style.SUCCESS('BALANCED') if ok
                else self.style.ERROR('NOT BALANCED')
            )
        )

    # ------------------------------------------------------------------

    def _profit_loss(self, from_date, to_date):
        self.stdout.write(self.style.MIGRATE_HEADING('\n2. PROFIT AND LOSS'))
        pl = build_profit_loss(from_date, to_date)
        self.stdout.write(f"   Period: {pl['from_date']} to {pl['to_date']}")
        self.stdout.write('')

        self.stdout.write('   REVENUE')
        for r in pl['revenue']['accounts']:
            self.stdout.write(f"     {r['code']:<8} {r['name'][:38]:<38} {r['balance']:>12}")
        self.stdout.write(f"     {'Total Revenue':<47} {pl['revenue']['total']:>12}")

        self.stdout.write('\n   COST OF INSURANCE')
        for r in pl['cost_of_insurance']['accounts']:
            self.stdout.write(f"     {r['code']:<8} {r['name'][:38]:<38} {r['balance']:>12}")
        self.stdout.write(f"     {'Total Cost of Insurance':<47} {pl['cost_of_insurance']['total']:>12}")

        self.stdout.write(f"\n   {'GROSS RESULT':<47} {pl['gross_result']:>12}")

        self.stdout.write('\n   OPERATING EXPENSES')
        for r in pl['operating_expenses']['accounts']:
            self.stdout.write(f"     {r['code']:<8} {r['name'][:38]:<38} {r['balance']:>12}")
        self.stdout.write(f"     {'Total Operating Expenses':<47} {pl['operating_expenses']['total']:>12}")

        label      = 'NET PROFIT' if pl['is_profit'] else 'NET LOSS'
        style      = self.style.SUCCESS if pl['is_profit'] else self.style.ERROR
        net_profit = pl['net_profit']
        self.stdout.write(f"\n   {style(f'{label:<47} {net_profit:>12}')}")

    # ------------------------------------------------------------------

    def _balance_sheet(self, today):
        self.stdout.write(self.style.MIGRATE_HEADING('\n3. BALANCE SHEET'))
        bs = build_balance_sheet(today)
        self.stdout.write(f"   As of: {bs['as_of']}  |  FY start: {bs['fiscal_year_start']}")

        def _section(title, data):
            self.stdout.write(f'\n   {title}')
            for r in data.get('accounts', []):
                self.stdout.write(f"     {r['code']:<8} {r['name'][:38]:<38} {r['balance']:>12}")
            self.stdout.write(f"     {'Total':<47} {data['total']:>12}")

        _section('CURRENT ASSETS',      bs['assets']['current_assets'])
        _section('FIXED ASSETS',        bs['assets']['fixed_assets'])
        self.stdout.write(f"\n   {'TOTAL ASSETS':<47} {bs['assets']['total']:>12}")

        _section('CURRENT LIABILITIES', bs['liabilities']['current_liabilities'])
        if 'provisions' in bs['liabilities']:
            _section('PROVISIONS', bs['liabilities']['provisions'])
        self.stdout.write(f"\n   {'TOTAL LIABILITIES':<47} {bs['liabilities']['total']:>12}")

        eq = bs['equity']
        self.stdout.write('\n   EQUITY')
        for r in eq['accounts']:
            self.stdout.write(f"     {r['code']:<8} {r['name'][:38]:<38} {r['balance']:>12}")
        self.stdout.write(f"     {eq['pl_label'][:47]:<47} {eq['current_year_pl']:>12}")
        self.stdout.write(f"     {'Total Equity':<47} {eq['total']:>12}")

        t = bs['totals']
        self.stdout.write(f"\n   {'TOTAL LIABILITIES + EQUITY':<47} {t['liabilities_and_equity']:>12}")
        self.stdout.write(
            '   ' + (
                self.style.SUCCESS('BALANCED') if t['balanced']
                else self.style.ERROR(f"NOT BALANCED  diff={float(t['total_assets']) - float(t['liabilities_and_equity']):.2f}")
            )
        )

    # ------------------------------------------------------------------

    def _ar_aging(self, today):
        self.stdout.write(self.style.MIGRATE_HEADING('\n4. ACCOUNTS RECEIVABLE AGING'))
        ar = build_ar_aging(today)
        self.stdout.write(f"   As of: {ar['as_of']}")
        if not ar['customers']:
            self.stdout.write('   No open customer invoices.')
            return
        self._print_aging(ar)

    def _ap_aging(self, today):
        self.stdout.write(self.style.MIGRATE_HEADING('\n5. ACCOUNTS PAYABLE AGING'))
        ap = build_ap_aging(today)
        self.stdout.write(f"   As of: {ap['as_of']}")
        if not ap['customers']:
            self.stdout.write('   No open vendor bills.')
            return
        self._print_aging(ap)

    def _print_aging(self, data):
        hdr = (
            f"   {'Customer':<28} {'Invoice':<20} {'Due Date':<12} "
            f"{'Total':>10} {'Paid':>10} {'Balance':>10} {'Bucket':<14} {'Age':>6}"
        )
        self.stdout.write(hdr)
        self.stdout.write('   ' + '-' * 112)
        for cust in data['customers']:
            for inv in cust['invoices']:
                self.stdout.write(
                    f"   {cust['contact_name'][:28]:<28} "
                    f"{inv['invoice_number']:<20} "
                    f"{inv['due_date']:<12} "
                    f"{inv['total_amount']:>10} "
                    f"{inv['amount_paid']:>10} "
                    f"{inv['balance_due']:>10} "
                    f"{inv['age_bucket']:<14} "
                    f"{inv['days_past_due']:>6}"
                )
        self.stdout.write('   ' + '-' * 112)
        t = data['totals']
        self.stdout.write(
            f"   {'TOTAL OUTSTANDING':<75} {t['total_outstanding']:>10}"
        )

    # ------------------------------------------------------------------

    def _cash_position(self):
        self.stdout.write(self.style.MIGRATE_HEADING('\n6. CASH POSITION'))
        cp = build_cash_position()
        self.stdout.write(f"   As of: {cp['as_of']}")
        self.stdout.write(
            f"   {'Code':<8} {'Account':<36} {'CCY':<5} "
            f"{'Native':>12} {'BWP':>12} {'Rate':<12}"
        )
        self.stdout.write('   ' + '-' * 90)
        for row in cp['accounts']:
            if not row['has_activity']:
                continue
            self.stdout.write(
                f"   {row['account_code']:<8} "
                f"{row['account_name'][:36]:<36} "
                f"{row['currency']:<5} "
                f"{row['balance_native']:>12} "
                f"{row['balance_bwp']:>12} "
                f"{row['exchange_rate']:<12}"
            )
        self.stdout.write('   ' + '-' * 90)
        self.stdout.write(
            self.style.SUCCESS(f"   {'TOTAL CASH IN BWP':<52} {cp['total_bwp']:>12}")
        )

    # ------------------------------------------------------------------

    def _general_ledger(self, account_code, from_date, to_date):
        self.stdout.write(
            self.style.MIGRATE_HEADING(f'\n7. GENERAL LEDGER DETAIL — {account_code}')
        )
        gl = build_general_ledger(account_code, from_date, to_date)
        if 'error' in gl:
            self.stdout.write(self.style.ERROR(f"   {gl['error']}"))
            return
        acct = gl['account']
        self.stdout.write(
            f"   {acct['code']} {acct['name']}  "
            f"({acct['account_type']} / {acct['sub_type']})"
        )
        self.stdout.write(
            f"   Period: {gl['from_date']} to {gl['to_date']}  "
            f"Opening balance: {gl['opening_balance']}"
        )
        self.stdout.write(
            f"   {'Date':<12} {'Entry':<20} {'Description'[:35]:<35} "
            f"{'Dr':>12} {'Cr':>12} {'Balance':>12}"
        )
        self.stdout.write('   ' + '-' * 106)
        for ln in gl['lines']:
            self.stdout.write(
                f"   {ln['date']:<12} "
                f"{ln['entry_number']:<20} "
                f"{ln['description'][:35]:<35} "
                f"{ln['debit']:>12} "
                f"{ln['credit']:>12} "
                f"{ln['running_balance']:>12}"
            )
        self.stdout.write('   ' + '-' * 106)
        t = gl['totals']
        self.stdout.write(
            f"   {'TOTALS':<68} {t['total_debits']:>12} {t['total_credits']:>12}"
        )
        self.stdout.write(
            f"   {'Closing balance':<68} {t['closing_balance']:>12}"
        )
