"""
Management command: pull_fnb_balances

Ask FNB what is actually in each watched account, and write one
``banking.BankBalanceSnapshot`` per account per run — including the runs that
fail, because "we could not read it" is an answer the CFO's screen needs and a
missing row cannot give it.

    python manage.py pull_fnb_balances                        # every watched account
    python manage.py pull_fnb_balances --account 62403392335  # just one
    python manage.py pull_fnb_balances --dry-run              # ask, print, write nothing
    python manage.py pull_fnb_balances --source manual        # label a hand-run

Three things worth knowing before changing it:

*   It asks for a ONE-DAY window (fromDate == toDate == today). The balance
    block comes back regardless of the transaction list, and a one-day window
    means this job creates no BankStatement and no BankStatementLine rows at
    all. Three pulls a day must not multiply statement rows — a seven-day
    window re-saved every morning is what put 20,779 duplicate lines into
    BankStatementLine (cleaned 2026-09-20).
*   It is sequential and slow on purpose. FNB rate-limits 10 requests a minute
    per endpoint (enforced in fnb/client.py, which sleeps) and a statement call
    takes 20-90 seconds. Six accounts is several minutes. It is scheduled; that
    is fine.
*   Which accounts are watched is a FLAG (``BankAccount.fnb_balance_watch``),
    never a name match. The name match is why Veritas, Risk Software and
    Unicoin had never once been read, and why a credit card was.

Omni never moves money. This reads.
"""
import builtins

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from banking.balances import watched_accounts
from banking.models import BankAccount, BankBalanceSnapshot


class Command(BaseCommand):
    help = "Read each watched account's balance from FNB into a snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            '--account',
            help='Read one BankAccount.account_number instead of every watched one.')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Ask the bank and print the answer, but write no snapshot.')
        parser.add_argument(
            '--source', choices=['scheduled', 'manual'], default='scheduled',
            help='How this run was started (recorded on every snapshot).')

    def handle(self, *args, **options):
        # Imported here, not at module level, so the tests can patch
        # fnb.statements.fetch_balances and have this command see the patch.
        from fnb import statements as fnb_statements
        from fnb.client import FNBAPIError, FNBNotConfigured

        dry_run = options['dry_run']
        source  = options['source']
        as_of   = timezone.localdate()

        if options.get('account'):
            accounts = list(BankAccount.objects.filter(
                account_number=options['account']))
            if not accounts:
                raise CommandError(
                    f"No BankAccount with account number {options['account']}.")
        else:
            accounts = list(watched_accounts())
            if not accounts:
                raise CommandError(
                    'No account is flagged fnb_balance_watch — nothing to read. '
                    'Set the flag on the accounts Finance wants watched.')

        self.stdout.write(
            f'Reading {len(accounts)} account(s) for {as_of.isoformat()}'
            f'{" (DRY RUN — nothing will be written)" if dry_run else ""}.')

        counts = {'ok': 0, 'no_balance_returned': 0, 'failed': 0}

        for account in accounts:
            # Stamped per account, not per run: these calls take 20-90 seconds
            # each, so one shared timestamp would claim the last account was
            # read minutes before it was.
            taken_at   = timezone.now()
            opening    = closing = None
            error_text = ''

            try:
                opening, closing = fnb_statements.fetch_balances(
                    account, on_date=as_of)
            except FNBNotConfigured as exc:
                # Nothing will work for any account — say so once and stop,
                # rather than writing six identical failures.
                raise CommandError(f'FNB not configured: {exc}')
            except FNBAPIError as exc:
                outcome    = BankBalanceSnapshot.Outcome.FAILED
                error_text = f'HTTP {exc.status_code} {exc.body[:500]}'
            except builtins.Exception as exc:  # noqa: BLE001
                outcome    = BankBalanceSnapshot.Outcome.FAILED
                error_text = f'{type(exc).__name__}: {exc}'[:500]
            else:
                outcome = (BankBalanceSnapshot.Outcome.OK if closing is not None
                           else BankBalanceSnapshot.Outcome.NO_BALANCE_RETURNED)

            counts[outcome] = counts.get(outcome, 0) + 1

            if not dry_run:
                BankBalanceSnapshot.objects.create(
                    bank_account = account,
                    taken_at     = taken_at,
                    as_of_date   = as_of,
                    opening      = opening,
                    closing      = closing,
                    outcome      = outcome,
                    error_text   = error_text,
                    source       = source,
                )

            self._report(account, outcome, closing, error_text)

        self.stdout.write(self.style.SUCCESS(
            f"\n{counts['ok']} read, "
            f"{counts['no_balance_returned']} with no balance returned, "
            f"{counts['failed']} failed."))

    def _report(self, account, outcome, closing, error_text):
        if outcome == BankBalanceSnapshot.Outcome.OK:
            self.stdout.write(self.style.SUCCESS(
                f'  ✓ {account.account_number} ({account.account_name}): '
                f'{closing} {account.currency_code_id}'))
        elif outcome == BankBalanceSnapshot.Outcome.NO_BALANCE_RETURNED:
            self.stdout.write(self.style.WARNING(
                f'  ? {account.account_number} ({account.account_name}): '
                f'the bank answered but sent no balance'))
        else:
            self.stdout.write(self.style.ERROR(
                f'  ! {account.account_number} ({account.account_name}): '
                f'{error_text[:200]}'))
