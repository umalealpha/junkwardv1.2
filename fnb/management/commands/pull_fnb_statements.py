"""
Management command: pull_fnb_statements

Pull the FNB statement for one or all bank accounts over a period.

  python manage.py pull_fnb_statements --account 62123456789 --days 7
  python manage.py pull_fnb_statements --all --days 31
"""
import datetime

from django.core.management.base import BaseCommand, CommandError

from banking.models import BankAccount
from fnb.client import FNBNotConfigured, FNBAPIError
from fnb.statements import pull_statement
from django.utils import timezone


def watched_statement_accounts():
    """The accounts `--all` pulls: the ones FLAGGED for FNB, never a name match.

    Until 2026-09-20 this was `Q(bank_name__icontains='FNB') |
    Q(account_name__icontains='FNB')`. That match is why three of the CFO's
    six key accounts had never once been read — Veritas Capital Mgmt, Risk
    Software Africa and Unicoin - Current AC all carry bank_name "First
    National Bank Botswana", which does not contain the letters FNB — and why
    the FNBB Credit Card Control A/C, which is a card and not a bank account,
    was pulled every morning and failed with HTTP 400 every morning.

    is_active and the empty-account-number guard are kept from the old query:
    the FNB API needs a real account number, and a deactivated account should
    not be called even if somebody left the flag on.
    """
    return (BankAccount.objects
            .filter(is_active=True, fnb_statement_pull=True)
            .exclude(account_number__in=('', '0'))
            .order_by('account_number'))


class Command(BaseCommand):
    help = 'Pull recent bank statements from FNB Botswana via API.'

    def add_arguments(self, parser):
        parser.add_argument('--account', help='BankAccount.account_number to pull for.')
        parser.add_argument('--all', action='store_true',
                            help='Pull for every active BankAccount flagged '
                                 'fnb_statement_pull.')
        parser.add_argument('--days', type=int, default=7,
                            help='Days back from today (default 7).')

    def handle(self, *args, **options):
        to_d   = timezone.localdate()
        from_d = to_d - datetime.timedelta(days=options['days'])

        if options['all']:
            qs = watched_statement_accounts()
        elif options.get('account'):
            qs = BankAccount.objects.filter(account_number=options['account'])
        else:
            raise CommandError('Pass --account <number> or --all.')

        if not qs.exists():
            raise CommandError('No matching BankAccount(s).')

        total = 0
        for ba in qs:
            try:
                stmt = pull_statement(ba, from_date=from_d, to_date=to_d)
            except FNBNotConfigured as e:
                raise CommandError(f'FNB not configured: {e}')
            except FNBAPIError as e:
                self.stdout.write(self.style.ERROR(
                    f'  ! {ba.account_number}: HTTP {e.status_code} {e.body[:200]}'
                ))
                continue
            n = stmt.lines.count() if hasattr(stmt, 'lines') else stmt.bankstatementline_set.count()
            closing = ('unknown (bank sent no balance)'
                       if stmt.closing_balance is None
                       else f'{stmt.closing_balance} {ba.currency_code_id}')
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ {ba.account_number} ({ba.account_name}): {n} lines, '
                f'closing {closing}'
            ))
            total += n

        self.stdout.write(self.style.SUCCESS(
            f'\nTotal statement lines imported: {total} '
            f'({from_d.isoformat()}..{to_d.isoformat()})'
        ))
