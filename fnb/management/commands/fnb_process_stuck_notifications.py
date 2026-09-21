"""
Management command: fnb_process_stuck_notifications

One-off backlog clean-up (CFO's FNB & Payments improvement plan, 2026-09-14).

`fnb/notifications.py:_persist_items` used to create an FNBWebhookEvent row
for every polled bank notification and never advance it — a comment claimed
"downstream consumers... are dispatched by signals on FNBWebhookEvent
post_save", but no such signal exists anywhere in this codebase. So every
notification since polling went live sat at status=received forever
(840 rows on production 14-Sep-2026, oldest from 27-May-2026).

That is now fixed going forward (notifications.py calls
process_notification_event() the moment each one is persisted). This command
applies the SAME classification+dispatch step to the existing backlog so it
does not sit stuck until FNB happens to resend it (FNB won't — these are
historical bank postings, not redeliverable webhooks).

Safe to re-run: process_notification_event() only touches rows still at
status=received, and dispatch_webhook() is idempotent per its own docstring.

Be plain with yourself about what "processed" means here (Fable 5.1 review,
2026-09-14): dispatch_webhook() only has handlers for payment.*/batch.*
event types. Every one of these bank-posting notifications is
'camt054_<CdtDbtInd>_<family code>' — none of them match, so each one hits
the "Unhandled event_type" branch and is marked PROCESSED having had nothing
applied. PROCESSED means "logged and classified", not "acted on" — there is
no consumer of these bank postings yet. That is the honest state of this
backlog clear-up, not a defect to fix here.

    python manage.py fnb_process_stuck_notifications            # apply
    python manage.py fnb_process_stuck_notifications --dry-run  # count only, no writes
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from fnb.models import FNBWebhookEvent
from fnb.notifications import _classify, process_notification_event


class Command(BaseCommand):
    help = 'Advance backlog FNB notification events stuck at status=received.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Count what would happen; write nothing.')

    def handle(self, *args, **opts):
        stuck = FNBWebhookEvent.objects.filter(
            status=FNBWebhookEvent.Status.RECEIVED)
        total = stuck.count()
        if not total:
            self.stdout.write('Nothing stuck at received.')
            return

        if opts['dry_run']:
            would_clear = would_flag = 0
            for e in stuck.iterator():
                cdt_dbt, bk_tx = _classify(e.raw_payload or {})
                if cdt_dbt and bk_tx:
                    would_clear += 1
                else:
                    would_flag += 1
            self.stdout.write(
                f'{total} stuck at received: would classify+process '
                f'{would_clear}, would flag for review {would_flag} '
                f'(dry-run — nothing written).')
            return

        cleared = flagged = 0
        for e in stuck.iterator():
            process_notification_event(e)
            e.refresh_from_db(fields=['status'])
            if e.status == FNBWebhookEvent.Status.FAILED:
                flagged += 1
            else:
                cleared += 1
        self.stdout.write(self.style.SUCCESS(
            f'processed {total}: {cleared} moved on, {flagged} flagged for '
            f'review (status=failed — check the FNB Webhook Events admin). '
            f'"moved on" means logged and classified, not acted on — nothing '
            f'in Omni yet consumes these bank postings.'))
