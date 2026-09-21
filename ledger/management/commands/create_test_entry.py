"""
Management command: create_test_entry

Creates a test journal entry:
  Dr 1110 FNB BWP operating account  P1,000
  Cr 3100 Share capital               P1,000

Posts it, then prints the trial balance to verify.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from ledger.models import Account, JournalEntry, JournalEntryLine
from ledger.views import _build_trial_balance


class Command(BaseCommand):
    help = 'Create and post a test journal entry, then print the trial balance.'

    def handle(self, *args, **options):
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            self.stderr.write('No superuser found. Run setup_initial_data first.')
            return

        try:
            acc_1110 = Account.objects.get(code='1110')
            acc_3100 = Account.objects.get(code='3100')
        except Account.DoesNotExist as e:
            self.stderr.write(f'Account not found: {e}. Run setup_chart_of_accounts first.')
            return

        from core.models import Currency
        bwp = Currency.objects.get(pk='BWP')

        # Create journal entry
        entry = JournalEntry(
            entry_date    = timezone.localdate(),
            description   = 'Initial capital contribution — test entry',
            journal_type  = JournalEntry.JournalType.GENERAL,
            currency_code = bwp,
            exchange_rate = Decimal('1.00000000'),
            created_by    = admin_user,
            notes         = 'Automated test entry created by create_test_entry command',
        )
        entry.save()
        self.stdout.write(f'\nCreated: {entry.entry_number}')

        # Dr 1110 — P1,000
        JournalEntryLine.objects.create(
            journal_entry = entry,
            account       = acc_1110,
            description   = 'Initial deposit to FNB BWP account',
            debit_amount  = Decimal('1000.00'),
            credit_amount = Decimal('0.00'),
            debit_bwp     = Decimal('1000.00'),
            credit_bwp    = Decimal('0.00'),
        )

        # Cr 3100 — P1,000
        JournalEntryLine.objects.create(
            journal_entry = entry,
            account       = acc_3100,
            description   = 'Share capital introduced',
            debit_amount  = Decimal('0.00'),
            credit_amount = Decimal('1000.00'),
            debit_bwp     = Decimal('0.00'),
            credit_bwp    = Decimal('1000.00'),
        )

        self.stdout.write('Lines added: Dr 1110 P1,000 / Cr 3100 P1,000')

        # Post
        entry.post(user=admin_user)
        self.stdout.write(self.style.SUCCESS(f'Posted: {entry.entry_number}  status={entry.status}'))

        # Trial balance
        tb = _build_trial_balance(end_date=timezone.localdate())
        self.stdout.write(self.style.MIGRATE_HEADING('\nTrial Balance:'))
        self.stdout.write(
            f"  {'Code':<8} {'Name':<40} {'Dr':>14} {'Cr':>14} {'Balance':>14}"
        )
        self.stdout.write('  ' + '-' * 94)
        for row in tb['accounts']:
            self.stdout.write(
                f"  {row['code']:<8} {row['name']:<40} "
                f"{row['total_debits']:>14} {row['total_credits']:>14} "
                f"{row['closing_balance']:>14}"
            )
        totals = tb['totals']
        self.stdout.write('  ' + '-' * 94)
        self.stdout.write(
            f"  {'TOTALS':<49} {totals['total_debits']:>14} {totals['total_credits']:>14}"
        )
        balanced = self.style.SUCCESS('BALANCED') if totals['balanced'] else self.style.ERROR('NOT BALANCED')
        self.stdout.write(f"\n  {balanced}")
