"""
fnb_pop_capture — file FNB payment confirmations as proofs of payment (CFO 2026-08-26).

Reads the CFO mailbox (the only place these land — accountsdept@ is a distribution
group with no mailbox behind it), stores each FNB result email as a PDF proof, and
files it against the claim, or the payment request, or leaves it in the queue.

FORWARD ONLY. CFO 2026-08-26: "old payments ignore, we do this properly from
today." Nothing received before settings.FNB_POP_CAPTURE_FROM is ever stored, so
a widened window or a re-run cannot drag history in. There is deliberately NO
backfill flag.

  python manage.py fnb_pop_capture --dry-run          # show, write nothing
  python manage.py fnb_pop_capture                    # last 72 hours
  python manage.py fnb_pop_capture --hours 336        # catch up after an outage
  python manage.py fnb_pop_capture --propose          # also ask the AI about the queue
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from fnb.models import FNBProofOfPayment as POP
from fnb.pop_capture import DEFAULT_MAILBOX, capture


class Command(BaseCommand):
    help = 'Capture FNB proof-of-payment emails and file them.'

    def add_arguments(self, parser):
        parser.add_argument('--mailbox', default=DEFAULT_MAILBOX)
        parser.add_argument('--hours', type=int, default=72)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--propose', action='store_true',
                            help='Ask the AI to propose matches for the unfiled queue. '
                                 'Proposals only — nothing is filed without a person.')
        parser.add_argument('--propose-limit', type=int, default=25)

    def handle(self, *args, **o):
        from fnb.pop_capture import capture_from
        hours = o['hours']
        floor = capture_from()
        self.stdout.write(f'Reading {o["mailbox"]} (last {hours}h'
                          + (f', nothing before {floor}' if floor else '') + ')…')

        out = capture(mailbox=o['mailbox'], hours=hours, dry_run=o['dry_run'])
        c = out['counts']
        self.stdout.write(
            f"  emails seen        : {c['seen']}\n"
            f"  newly captured     : {c['captured']}\n"
            f"  already on file    : {c['already']}\n"
            f"  before the cut-off : {c['before_cutoff']} (ignored on purpose)\n"
            f"  unparseable        : {c['unparseable']}\n"
            f"  → filed to a claim : {c['filed_claim']}\n"
            f"  → filed to a request: {c['filed_request']}\n"
            f"  → left in the queue : {c['unfiled']}")

        for r in out['rows'][:15]:
            if r['action'] in ('captured', 'would-capture'):
                self.stdout.write(f"     {r['state']:14s} {r.get('amount', '')!s:>14s}  "
                                  f"{r['ref'][:52]}")

        if o['propose'] and not o['dry_run']:
            self._propose(o['propose_limit'])

        if o['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN — nothing was written.'))

    def _propose(self, limit: int):
        from fnb.pop_ai import propose_for
        queue = POP.objects.filter(state=POP.State.UNFILED, paid=True)\
                           .order_by('-amount')[:limit]
        self.stdout.write(f'\nAsking for proposals on {len(queue)} unfiled proof(s)…')
        made = 0
        for pop in queue:
            res = propose_for(pop)
            if res.get('proposed'):
                made += 1
                self.stdout.write(f"     proposed {res['proposed']['kind']} "
                                  f"{res['proposed']['ref']} for {pop.reference[:40]}")
        self.stdout.write(f'  proposals awaiting a human: {made}')
