"""
Management command: bank_integrity_watch

Check that Omni's copy of the bank still agrees with the bank, and email
Kago / Pako / Keetile (CFO cc'd) when it does not. CFO directive 2026-09-20.

    python manage.py bank_integrity_watch                 # run + email on failure
    python manage.py bank_integrity_watch --dry-run       # show the email, send nothing
    python manage.py bank_integrity_watch --no-bank-tie   # skip the FNB round-trip

The bank tie asks FNB for one day at a time, per account, for the last seven
days. FNB rate-limits to 10 calls a minute per endpoint (fnb/client.py), so a
full run takes several minutes — schedule it, do not run it in a request.

Run once a day, AFTER the 06:00 statement pull (infra/cron/fnb-sync.cron).
Installing the cron file does NOT schedule it; infra/install-crons.sh must run
on the box with bank-integrity-watch in its ENABLED list.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from banking.integrity_watch import COULD_NOT_RUN, run


class Command(BaseCommand):
    help = "Check Omni's bank data against FNB and alert the finance team."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print the findings and the email, send nothing.')
        parser.add_argument(
            '--no-bank-tie', action='store_true',
            help='Skip the per-day FNB comparison (the slow check).')

    def handle(self, *args, **opts):
        result = run(dry_run=opts['dry_run'],
                     skip_bank_tie=opts['no_bank_tie'])

        for name, findings in result['results']:
            if findings:
                self.stdout.write(self.style.ERROR(f'{name}: {len(findings)}'))
                for f in findings:
                    self.stdout.write(f'    - {f}')
            else:
                self.stdout.write(self.style.SUCCESS(f'{name}: ok'))

        n = result['findings']
        if n == 0:
            self.stdout.write(self.style.SUCCESS(
                '\nOmni agrees with the bank. No email sent.'))
            return

        self.stdout.write(self.style.WARNING(f'\n{n} finding(s) — {result["action"]}'))
        if opts['dry_run']:
            self.stdout.write('\n--- subject ---')
            self.stdout.write(result.get('subject', ''))
            self.stdout.write('\n--- body ---')
            self.stdout.write(result.get('body', ''))
            return

        # Exit non-zero when the ALARM ITSELF failed — the email did not go, or
        # a check could not run. Cron mails root on a non-zero exit and the
        # monitoring picks it up; a watcher whose own failure is one line in a
        # log nobody reads is a watcher that has quietly stopped watching.
        broken = []
        if not result.get('emailed'):
            broken.append(result.get('action', 'the email did not go'))
        broken += [f for _, fs in result['results'] for f in fs
                   if f.startswith(COULD_NOT_RUN)]
        if broken:
            raise CommandError(
                'the bank check did not complete: ' + '; '.join(broken))
