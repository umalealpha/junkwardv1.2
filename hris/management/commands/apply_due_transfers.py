"""Apply SCHEDULED inter-entity employee transfers whose effective date has
arrived. Run daily from cron (alongside expire_stale_pos).

    python manage.py apply_due_transfers
"""
from django.core.management.base import BaseCommand

from hris.transfer_service import apply_due_transfers


class Command(BaseCommand):
    help = 'Apply scheduled employee transfers whose effective date has arrived.'

    def handle(self, *args, **opts):
        n = apply_due_transfers()
        self.stdout.write(self.style.SUCCESS(
            f'apply_due_transfers: applied {n} scheduled transfer(s).'))
