"""
Management command: simulate_graphite_events

Sends 5 realistic test events through the EventProcessor and shows
how they land in the Finance system as invoices.
"""

from django.core.management.base import BaseCommand

from integrations.models import IntegrationEvent
from integrations.services import EventProcessor


EVENTS = [
    {
        'source_system': 'graphite',
        'event_type':    'policy_issued',
        'event_data': {
            'policy_number':       'POL-2026-004521',
            'policy_type':         'Motor Vehicle',
            'insured_name':        'Kabo Molefe',
            'insured_graphite_id': 'GFT-CUST-004521',
            'premium_amount':      '4500.00',
            'vat_amount':          '630.00',
            'policy_start':        '2026-04-01',
            'issue_date':          '2026-03-24',
            'due_days':            30,
        },
    },
    {
        'source_system': 'graphite',
        'event_type':    'policy_issued',
        'event_data': {
            'policy_number':       'POL-2026-004522',
            'policy_type':         'Property — Commercial',
            'insured_name':        'Botswana Building Materials (Pty) Ltd',
            'insured_graphite_id': 'GFT-CORP-001234',
            'premium_amount':      '18500.00',
            'vat_amount':          '2590.00',
            'policy_start':        '2026-04-01',
            'issue_date':          '2026-03-24',
            'due_days':            30,
        },
    },
    {
        'source_system': 'graphite',
        'event_type':    'claim_approved',
        'event_data': {
            'claim_number':          'CLM-2026-001837',
            'claim_type':            'Motor — Third Party',
            'claimant_name':         'Thabo Seretse',
            'claimant_graphite_id':  'GFT-CLMT-001837',
            'claim_amount':          '12750.00',
            'policy_number':         'POL-2025-003998',
            'issue_date':            '2026-03-24',
        },
    },
    {
        'source_system': 'graphite',
        'event_type':    'commission_calculated',
        'event_data': {
            'broker_name':        'Pinnacle Insurance Brokers',
            'broker_graphite_id': 'GFT-BRK-00088',
            'commission_amount':  '6300.00',
            'wht_applicable':     True,
            'period_start':       '2026-03-01',
            'period_end':         '2026-03-31',
        },
    },
    {
        'source_system': 'graphite',
        'event_type':    'policy_cancelled',
        'event_data': {
            'policy_number':           'POL-2026-004180',
            'insured_name':            'Neo Tshimologo',
            'insured_graphite_id':     'GFT-CUST-004180',
            'refund_amount':           '2100.00',
            'original_invoice_number': 'INV-2026-000089',
        },
    },
]


class Command(BaseCommand):
    help = 'Send 5 simulated Graphite events and show how they process into Finance records.'

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                '\nAlpha Direct <-> Graphite -- Integration Event Simulation\n'
            )
        )

        processor = EventProcessor()
        results   = []

        for i, payload in enumerate(EVENTS, start=1):
            self.stdout.write(f"\n[{i}/5] Sending: {payload['event_type']} ...")

            event = IntegrationEvent.objects.create(**payload)
            event = processor.run(event)

            results.append(event)

            if event.status == IntegrationEvent.Status.PROCESSED:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  OK  {event.status.upper()}"
                        f"  ->  {event.result_type} {event.result_id}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"  FAIL  {event.status.upper()}: {event.error_message}"
                    )
                )

        # Summary table
        self.stdout.write(self.style.MIGRATE_HEADING('\n--- Summary -------------------------------------------'))
        self.stdout.write(
            f"  {'#':<3} {'Event Type':<28} {'Status':<12} {'Result':<10} {'Result ID'}"
        )
        self.stdout.write('  ' + '-' * 80)

        processed = failed = 0
        for i, ev in enumerate(results, start=1):
            status_str = ev.status
            result_str = ev.result_type or ''
            id_str     = str(ev.result_id)[:8] + '…' if ev.result_id else ev.error_message or ''

            line = f"  {i:<3} {ev.event_type:<28} {status_str:<12} {result_str:<10} {id_str}"
            if ev.status == IntegrationEvent.Status.PROCESSED:
                self.stdout.write(self.style.SUCCESS(line))
                processed += 1
            else:
                self.stdout.write(self.style.ERROR(line))
                failed += 1

        self.stdout.write('  ' + '-' * 80)
        self.stdout.write(
            f"\n  Processed: {processed}   Failed: {failed}   Total: {len(results)}\n"
        )

        if processed:
            self.stdout.write(
                self.style.SUCCESS(
                    '  Finance records created. Run `python manage.py run_all_reports` '
                    'to see updated balances.\n'
                )
            )
