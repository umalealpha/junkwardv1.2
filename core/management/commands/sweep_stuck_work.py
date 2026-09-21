"""Raise / refresh / close the rolling tasks for work that is sitting with
somebody, and alarm the CFO on anything past its escalation limit.

CFO 2026-08-20 — "can we create auto reminders and tasks if someone is sitting on
something". Runs at 06:15 CAT, just BEFORE sweep_task_reminders (06:30), so a task
raised this morning is in that morning's digest rather than tomorrow's.

    python manage.py sweep_stuck_work [--dry-run]
"""
from django.core.management.base import BaseCommand

from core import stuck_work


class Command(BaseCommand):
    help = 'Chase work sitting unactioned: rolling OmniTasks + CFO escalation.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would happen; write nothing.')

    def handle(self, *args, **opts):
        res = stuck_work.sweep(dry_run=opts['dry_run'])
        self.stdout.write(
            f"queues={res['queues']} items={res['items']} "
            f"created={res['tasks_created']} refreshed={res['tasks_refreshed']} "
            f"closed={res['tasks_closed']} escalated={res['escalated']}"
            + ('  [DRY RUN]' if opts['dry_run'] else ''))
