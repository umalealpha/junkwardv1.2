"""
Collect invoices the firms emailed in.

CFO recommendation 1: the best upload screen is the one nobody opens. Firms already email
their bills, so this reads the attachments and leaves each one in the waiting room with its
duplicate warnings already worked out.

    python manage.py bonu_fetch_mail --mailbox bonu@alphadirect.co.bw
    python manage.py bonu_fetch_mail --mailbox … --all       # not just unread

Nothing is paid, posted or confirmed. A firm cannot create a payable by sending an email.
If we do not yet have permission to read that mailbox, it says so in plain words rather
than looking like an empty inbox.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Read invoice attachments out of a mailbox into the BONU waiting room.'

    def add_arguments(self, parser):
        parser.add_argument('--mailbox', required=True,
                            help='The mailbox the firms send to, e.g. bonu@alphadirect.co.bw')
        parser.add_argument('--limit', type=int, default=25)
        parser.add_argument('--folder', default='Inbox')
        parser.add_argument('--all', action='store_true',
                            help='Include mail already marked read.')

    def handle(self, *args, **opts):
        from bonu.mailbox import MailboxUnavailable, fetch
        try:
            out = fetch(opts['mailbox'], limit=opts['limit'], folder=opts['folder'],
                        unread_only=not opts['all'])
        except MailboxUnavailable as exc:
            # Not a crash — a fact somebody has to act on.
            self.stderr.write(self.style.WARNING(str(exc)))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{out['mailbox']}: looked at {out['messages_looked_at']} message(s), "
            f"read {out['documents_created']} bill(s)."))
        for d in out['documents']:
            self.stdout.write(f"  {d['filename']} from {d['from']} → {d['status']}")
        if out['attachments_ignored']:
            self.stdout.write(f"  {out['attachments_ignored']} attachment(s) ignored "
                              f"(not a bill format).")
        for e in out['errors']:
            self.stderr.write(f'  could not read: {e}')
        self.stdout.write(self.style.SUCCESS(f"\n{out['note']}"))
