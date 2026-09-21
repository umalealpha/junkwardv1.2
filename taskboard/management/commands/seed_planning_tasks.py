"""
seed_planning_tasks — create this week's planning-meeting tasks as OmniTasks.

CFO directive 2026-07-13 (Planning-meeting WhatsApp). Assigner = the CFO. Tasks
with a clear owner go to that person; team/unowned items are parked on the CFO to
reassign. Idempotent: a task with the same title + week is not duplicated.

  python manage.py seed_planning_tasks            # dry-run
  python manage.py seed_planning_tasks --commit
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import OmniTask
from taskboard import services
from taskboard.cfo_views import _match_user, _monday

CFO_USERNAME = 'pganesharajah'

# (owner token, title, body)  — token '' / 'team' parks on the CFO to reassign.
TASKS = [
    ('team',      'Premium board report',
     'Confirm we have the top-100 customers’ premium info; build our own for audit.'),
    ('medu',      'Project Nexus',
     'Follow the instructions posted by 9am on the Google Play Store.'),
    ('tshephang', 'Veritas YE-2026 stock sheet', 'Prepare the stock sheet for Veritas year-end 2026.'),
    ('oprah',     'Veritas internal audit procedures', 'Run the internal audit procedures for Veritas.'),
    ('bharath',   'Claims fraud checks in Graphite', 'Work offsite and conclude the claims fraud checks in Graphite.'),
    ('keetile',   'Instant-insurance matters', 'Action the instant-insurance matters communicated on Saturday.'),
    ('legakwa',   'Unpaid payments + group budgets',
     'Why were market SA, Liberty and Sefalana voucher payments not paid / not sent to the CFO to approve? Also the group budgets.'),
    ('team',      'Load Alpha budgets to Omni', 'Load the Alpha budgets into Omni. (Owner to confirm.)'),
    ('team',      'PST debit order', 'PST debit order. (Owner to confirm.)'),
    ('tlamelo',   'Tlamelo health payment', 'Tlamelo health payment — confirm and process.'),
]


class Command(BaseCommand):
    help = 'Seed this week’s planning-meeting tasks as OmniTasks. Dry-run unless --commit.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true')

    def handle(self, *args, **opts):
        commit = opts['commit']
        w = self.stdout.write
        cfo = User.objects.filter(username=CFO_USERNAME).first()
        if not cfo:
            raise CommandError(f'CFO account {CFO_USERNAME!r} not found.')
        week = _monday()
        # No auto-deadline (CFO 2026-07-13 "be fair"): reminders fire only on the
        # deadline day, so a task carries NO due date until the CFO/manager sets one.
        created = existing = 0
        for token, title, body in TASKS:
            owner = _match_user(token) or cfo
            parked = owner == cfo and token in ('team', '')
            if OmniTask.objects.filter(title=title, week_of=week).exists():
                existing += 1
                w(f'  exists  {title}')
                continue
            w(f'  {"NEW " if commit else "WOULD "}{title:38} -> {owner.get_full_name() or owner.username}'
              f'{"  [parked on CFO]" if parked else ""}')
            if commit:
                t = OmniTask.objects.create(
                    assigner=cfo, assignee=owner, title=title, body=body,
                    week_of=week, source='planning_meeting', due_at=None,
                    priority=OmniTask.Priority.HIGH if 'payment' in title.lower() else OmniTask.Priority.NORMAL)
                services.notify_on_assign(t)
                created += 1
        if commit:
            w(self.style.SUCCESS(f'COMMITTED: created={created} existing={existing} week_of={week} (no auto-deadline)'))
        else:
            w(self.style.WARNING(f'DRY RUN — would create up to {len(TASKS)-existing}. Re-run with --commit.'))
