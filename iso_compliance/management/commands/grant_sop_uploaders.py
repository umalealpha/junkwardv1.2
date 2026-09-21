"""grant_sop_uploaders — ensure the SOP Bank uploader group + membership.

CFO directive 2026-07-27 (Unami's Corporate Governance request): empower a small
set of HR + Finance staff to upload SOPs and Policies themselves, so nobody has
to ask the CFO. Idempotent — safe to re-run. Adds ONLY to the group; never
removes anyone and never touches any other permission.

    python manage.py grant_sop_uploaders
    python manage.py grant_sop_uploaders --list      # show current members only
"""
from __future__ import annotations

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

GROUP = 'sop_uploader'

# The empowered uploaders, by username (verified against prod 2026-07-27).
# Pako = Pako Kago the Financial Controller (pkago) — confirmed by the CFO,
# NOT Ditso Pako Motlhabane and NOT the insurance.co.bw Pako.
UPLOADERS = [
    'ubutale',              # Unami Butale — Senior Manager Human Capital
    'dikgopoleng',          # Dorothy Ikgopoleng
    'omogomotsi',           # Oprah Mogomotsi
    'ktshutlhedi',          # Kago Tshutlhedi — Finance Manager
    'meduduetso.tlagae',    # Meduduetso Tlagae
    'pkago',                # Pako Kago — Financial Controller
]


class Command(BaseCommand):
    help = 'Create the sop_uploader group and add the empowered HR + Finance staff.'

    def add_arguments(self, parser):
        parser.add_argument('--list', action='store_true',
                            help='List current members and exit without changes.')

    def handle(self, *args, **opts):
        group, made = Group.objects.get_or_create(name=GROUP)
        if made:
            self.stdout.write(self.style.SUCCESS(f'Created group "{GROUP}".'))

        if opts['list']:
            for u in group.user_set.order_by('username'):
                self.stdout.write(f'  {u.username:24s} {u.first_name} {u.last_name}')
            self.stdout.write(f'Total members: {group.user_set.count()}')
            return

        added, missing, already = [], [], []
        for username in UPLOADERS:
            u = User.objects.filter(username=username).first()
            if u is None:
                missing.append(username)
                continue
            if group.user_set.filter(pk=u.pk).exists():
                already.append(username)
                continue
            u.groups.add(group)
            added.append(username)

        for a in added:
            self.stdout.write(self.style.SUCCESS(f'  + added {a}'))
        for a in already:
            self.stdout.write(f'  = already a member: {a}')
        for m in missing:
            self.stdout.write(self.style.WARNING(f'  ! no such user (skipped): {m}'))
        self.stdout.write(self.style.SUCCESS(
            f'Done. Group "{GROUP}" now has {group.user_set.count()} member(s).'))
