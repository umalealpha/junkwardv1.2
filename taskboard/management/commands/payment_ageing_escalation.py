"""Escalate overdue payment requests to the CFO the SAME DAY.

CFO 2026-09-14: "payment requests that sit too long must escalate to the CFO
the same day, not wait for a next-day digest." Meant to run several times a
day (e.g. hourly, or on the hour during business hours) alongside the
existing 09:30 payment_daily_digest — it never replaces that digest, it
closes the gap between "went stale mid-morning" and "the CFO hears about it
tomorrow".

    python manage.py payment_ageing_escalation
    python manage.py payment_ageing_escalation --dry-run
    python manage.py payment_ageing_escalation --stale-days 2
    python manage.py payment_ageing_escalation --to me@x.com

Scheduling note (learned the hard way on fnb_three_way_check, 12-Sep-2026): a
deploy does not install a cron schedule. Adding a file under infra/cron/
schedules nothing until infra/install-crons.sh runs on the box.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from taskboard import escalation


class Command(BaseCommand):
    help = ('Email the CFO about any payment request that has crossed the '
            'ageing threshold today, same-day (does not wait for the digest).')

    def add_arguments(self, parser):
        parser.add_argument('--stale-days', type=int, default=None,
                            help='Override the Finance-editable threshold '
                                 '(Taskboard settings > payment_escalation.stale_days).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be sent; send nothing, mark nothing.')
        parser.add_argument('--to', dest='to', default='',
                            help='Override recipients, comma-separated (testing).')

    def handle(self, *args, **opts):
        to_override = ([a.strip() for a in opts['to'].split(',') if a.strip()]
                       if opts.get('to') else None)
        res = escalation.escalate(threshold_days=opts['stale_days'],
                                  to_override=to_override, dry_run=opts['dry_run'])

        if res['count'] == 0:
            self.stdout.write(f"Nothing over {res['threshold']} day(s) waiting on the "
                              f"CFO that has not already been escalated.")
            return

        if opts['dry_run']:
            self.stdout.write(f"DRY RUN — \"{res['subject']}\" to "
                              f"{', '.join(res['to']) or '(nobody configured)'}")
            return

        if not res['to']:
            self.stderr.write(self.style.ERROR(
                f"{res['count']} payment request(s) are over {res['threshold']} day(s) "
                f"waiting on the CFO but nobody is on the escalation list — nothing sent. "
                f"Add recipients in Omni under Reports > Report recipients, report "
                f"\"{escalation.REPORT_SLUG}\"."))
            raise SystemExit(3)

        if not res['sent']:
            self.stderr.write(self.style.ERROR(
                f"{res['count']} payment request(s) are over {res['threshold']} day(s) "
                f"waiting on the CFO but the send delivered NOTHING — they are left "
                f"un-escalated and will be retried on the next run."))
            raise SystemExit(2)

        self.stdout.write(self.style.SUCCESS(
            f"Escalated {res['count']} request(s) over {res['threshold']} day(s) to "
            f"{', '.join(res['to'])} (send={res['sent']})."))
