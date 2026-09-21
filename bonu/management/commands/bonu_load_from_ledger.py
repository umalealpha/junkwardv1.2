"""
Load the BONU legal spend that is already in the books.

    python manage.py bonu_load_from_ledger              # show what WOULD load, change nothing
    python manage.py bonu_load_from_ledger --commit     # actually create the firms and invoices

Dry run by default, because this writes 32 firms and hundreds of invoices into a live
system and nobody should be able to do that by pressing return in the wrong window.

What it takes from the ledger: the firm's name, the invoice reference, the date, the amount.
What it deliberately leaves behind: the member's name, which is in the same field and has no
business in a dashboard.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create BONU firms and invoices from the BONU Claims account in the ledger.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Write to the database. Without this, nothing changes.')
        parser.add_argument('--account', default='103014')

    def handle(self, *args, **o):
        from bonu.ledger_import import load

        r = load(dry_run=not o['commit'], account_code=o['account'])
        if not r.get('ok'):
            self.stderr.write(self.style.ERROR(r.get('error', 'failed')))
            return

        s = r['stats']
        head = ('WOULD LOAD' if r['dry_run'] else 'LOADED')
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{head}: {r['invoices_seen']} invoice reference(s) worth "
            f"P{r['total_amount']:,.2f} across {len(r['by_firm'])} firm(s)."))
        self.stdout.write(f"  ledger lines read      : {s['lines']}")
        self.stdout.write(f"  no firm on the entry   : {s['no_firm']}")
        self.stdout.write(f"  no invoice reference   : {s['no_reference']}")
        self.stdout.write(f"  net zero (reversed)    : {s['net_zero']}")
        self.stdout.write(f"  same reference, 2 dates: {r['multi_date_references']}  "
                          f"(worth asking the firm about)")
        self.stdout.write(f"  member identified      : {r.get('with_member_token', 0)} "
                          f"(one-way token, no names stored)")
        if not r['dry_run']:
            self.stdout.write(f"  firms created          : {r['firms_created']}")
            self.stdout.write(f"  firms already on file  : {r['firms_matched']}")
            self.stdout.write(f"  invoices created       : {r['invoices_created']}")
            self.stdout.write(f"  invoices already there : {r['invoices_skipped_existing']}")

        self.stdout.write('\n  Top firms by spend:')
        for name, row in sorted(r['by_firm'].items(), key=lambda kv: -kv[1]['amount'])[:12]:
            self.stdout.write(f"    P{row['amount']:>12,.0f}  {row['invoices']:>4} invoice(s)  {name[:44]}")

        self.stdout.write('\n  ' + r['limits'])
        if r['dry_run']:
            self.stdout.write(self.style.WARNING(
                '\nNothing was written. Re-run with --commit to load it.'))
        else:
            self.stdout.write(self.style.SUCCESS('\nDone. No journal was posted and no figure moved.'))
