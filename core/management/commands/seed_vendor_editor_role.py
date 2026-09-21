"""
core/management/commands/seed_vendor_editor_role.py

CFO directive 2026-07-10: give Kao (Claims) and the copied recipients access to
the supplier-address editor (/vendors/addresses). Membership of the
`vendor_editor` group is the gate checked by billing.vendor_address.

  python manage.py seed_vendor_editor_role                       # dry-run, default people
  python manage.py seed_vendor_editor_role --commit
  python manage.py seed_vendor_editor_role --emails a@x,b@y --commit

Only touches this one group's membership — no other permissions, no company
access, no superuser flags.
"""
from __future__ import annotations

from django.contrib.auth.models import User, Group
from django.core.management.base import BaseCommand

GROUP = 'vendor_editor'
DEFAULT_EMAILS = [
    'ogalotshoge@alphadirect.co.bw',   # Kao Galotshoge (Claims)
    'lmachola@insurance.co.bw',         # Lemogang Machola
]


class Command(BaseCommand):
    help = 'Create the vendor_editor group and grant supplier-address access to users.'

    def add_arguments(self, parser):
        parser.add_argument('--emails', default='',
                            help='Comma-separated emails (defaults to Kao + Lemogang).')
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        commit = opts['commit']
        emails = [e.strip().lower() for e in
                  (opts['emails'].split(',') if opts['emails'] else DEFAULT_EMAILS)
                  if e.strip()]
        w = self.stdout.write

        if commit:
            group, created = Group.objects.get_or_create(name=GROUP)
            w(f'group {GROUP}: {"CREATED" if created else "exists"}')
        else:
            group = Group.objects.filter(name=GROUP).first()
            w(f'group {GROUP}: {"exists" if group else "WOULD CREATE"}')

        granted = 0
        for em in emails:
            users = list(User.objects.filter(email__iexact=em, is_active=True))
            if not users:
                w(f'  NO ACTIVE USER for {em} — skipped')
                continue
            for u in users:
                if commit and group:
                    u.groups.add(group)
                    granted += 1
                    w(f'  granted {em} -> {u.username}')
                else:
                    w(f'  WOULD grant {em} -> {u.username}')

        if commit:
            w(self.style.SUCCESS(f'COMMITTED: {granted} membership(s) in {GROUP}.'))
        else:
            w(self.style.WARNING('DRY RUN — re-run with --commit.'))
