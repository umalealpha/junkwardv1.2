"""
seed_expense_approvers — the senior-accountant pool that can process refunds.

CFO 2026-07-13: refunds route to one of Pako, Kago, Tlamelo Chimidza,
Keetile Mokhendo, or Legakwa Ntabeni. Membership of the `expense_approver`
group is the gate (see hris.expense_api). Idempotent + name-guarded.

  python manage.py seed_expense_approvers            # dry-run
  python manage.py seed_expense_approvers --commit
"""
from __future__ import annotations

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db.models import Q, Value
from django.db.models.functions import Concat

GROUP = 'expense_approver'
# name fragments (first+last), matched uniquely among active users
ROSTER = ['pako kago', 'kago tshutlhedi', 'tlamelo chimidza',
          'keetile mokhendo', 'legakwa ntabeni']


class Command(BaseCommand):
    help = 'Seed the expense_approver (senior-accountant) pool. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        commit = opts['commit']
        w = self.stdout.write
        group = (Group.objects.get_or_create(name=GROUP)[0] if commit
                 else Group.objects.filter(name=GROUP).first())
        w(f'group {GROUP}: {"ready" if group else "WOULD CREATE"}')
        qs = User.objects.filter(is_active=True).annotate(
            full=Concat('first_name', Value(' '), 'last_name'))
        added = 0
        for frag in ROSTER:
            matches = list(qs.filter(Q(full__icontains=frag)
                                     | Q(username__icontains=frag.replace(' ', '.')))[:3])
            if len(matches) != 1:
                w(f'  {frag:22} -> {"NONE" if not matches else f"AMBIGUOUS ({len(matches)})"} — skipped')
                continue
            u = matches[0]
            if commit and group:
                u.groups.add(group); added += 1
                w(f'  {frag:22} -> granted {u.username}')
            else:
                w(f'  {frag:22} -> would grant {u.username}')
        w(self.style.SUCCESS(f'COMMITTED: {added} in {GROUP}.') if commit
          else self.style.WARNING('DRY RUN — re-run with --commit.'))
