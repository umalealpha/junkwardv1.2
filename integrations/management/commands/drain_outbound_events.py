"""drain_outbound_events — deliver due OutboundEvents (WS1 outbound state bus).

Run on a short interval (scheduler) to push out anything that failed a live
send and is now past its backoff. Safe to run repeatedly: delivery is idempotent
per event and gated by OUTBOUND_BUS_ENABLED.
"""
from django.core.management.base import BaseCommand

from integrations.outbound import drain


class Command(BaseCommand):
    help = 'Deliver any due OutboundEvents (pending/failed, backoff elapsed).'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50,
                            help='Max events to attempt this pass (default 50).')

    def handle(self, *args, **opts):
        result = drain(limit=opts['limit'])
        self.stdout.write(self.style.SUCCESS(f'outbound drain: {result}'))
