"""
expire_stale_pos — auto-expire APPROVED POs past valid_until with no
activity in the last 90 days.

Run from cron daily after the nightly backup:
    0 5 * * * /opt/alpha-finance/.../manage.py expire_stale_pos --commit

Manus PO Audit #8 (CFO directive 2026-05-20).
"""

from django.core.management.base import BaseCommand
from procurement.phase_b_services import expire_stale_pos


class Command(BaseCommand):
    help = 'Auto-expire stale APPROVED POs past their valid_until with no activity in 90 days.'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true',
                            help='Actually write. Default = dry-run.')

    def handle(self, *args, **opts):
        # Dry-run: count what would expire without writing
        if not opts['commit']:
            from datetime import timedelta
            from django.utils import timezone
            from procurement.models import PurchaseOrder
            today = timezone.localdate()
            cutoff = today - timedelta(days=90)
            n = PurchaseOrder.objects.filter(
                status=PurchaseOrder.Status.APPROVED,
                valid_until__isnull=False,
                valid_until__lt=today,
                updated_at__date__lt=cutoff,
            ).count()
            self.stdout.write(f'[dry-run] would expire {n} PO(s).')
            return
        ids = expire_stale_pos()
        self.stdout.write(self.style.SUCCESS(f'Expired {len(ids)} PO(s).'))
