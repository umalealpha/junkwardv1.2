"""
Management command: fnb_three_way_check

The daily read-only check that Omni's payment record, the FNB batch and the
batch's age all agree. It changes nothing.

    python manage.py fnb_three_way_check                 # print
    python manage.py fnb_three_way_check --email         # print + send
    python manage.py fnb_three_way_check --stale-days 3

Scheduling note, learned the hard way on 12-Sep-2026: a deploy does NOT install
cron schedules. Adding infra/cron/fnb-three-way-check.cron to the repo schedules
nothing until `sudo bash /opt/alpha-finance/infra/install-crons.sh` runs on the
box and /etc/cron.d is read back. A merged cron file is not a live schedule.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from fnb.three_way_check import FINDINGS, run


class Command(BaseCommand):
    help = 'Report where Omni\'s payment status and the FNB batch disagree.'

    def add_arguments(self, parser):
        parser.add_argument('--email', action='store_true',
                            help='Also send the report to Finance and the CFO.')
        parser.add_argument('--stale-days', type=int, default=7,
                            help='A batch older than this is called out (default 7).')
        # CFO, 21-Sep-2026: "this email should not talk about things 7 days
        # old, going forward". 0 turns the window off and reports everything,
        # which is what the exception cockpit screen already does.
        parser.add_argument('--max-age-days', type=int, default=7,
                            help='Ignore anything older than this (default 7; '
                                 '0 reports everything).')

    def handle(self, *args, **opts):
        cap = opts['max_age_days'] or None
        res = run(stale_days=opts['stale_days'], max_age_days=cap)

        if cap:
            self.stdout.write(f'Window: the last {cap} days.')

        if res['clean']:
            self.stdout.write(self.style.SUCCESS(
                'Three-way check: Omni and the bank agree on every payment with a batch.'))
        else:
            self.stdout.write(self.style.ERROR(
                f"Three-way check: {res['contradiction_count']} payment(s) where Omni "
                f"and the bank disagree."))

        for key, rows in res['contradictions'].items():
            if not rows:
                continue
            headline, meaning = FINDINGS[key]
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(f'{headline} — {len(rows)}'))
            self.stdout.write(f'  {meaning}')
            for r in rows:
                self.stdout.write(
                    f"    {r['ref']}  BWP {float(r['total'] or 0):,.2f}  "
                    f"{r['payee'] or ''}  ({r['age_days']}d old)")

        # Printed even though the email deliberately never mentions it: the log
        # is the one place that has to prove an old problem was left out on
        # purpose rather than quietly lost.
        if res.get('excluded_older'):
            self.stdout.write(self.style.WARNING(
                f"{res['excluded_older']} older disagreement(s) are outside the "
                f"{cap}-day window and are NOT in this email. They are still on "
                f"the payments exception screen."))

        self.stdout.write('')
        self.stdout.write('Batches the bank has not settled:')
        for status, g in res['batches']['groups'].items():
            self.stdout.write(
                f"  {status}: {g['count']} batches, BWP {float(g['total']):,.2f}, "
                f"{g['over_threshold']} over {res['batches']['stale_days']} days")

        n = res['notifications']
        self.stdout.write('')
        if res['notifications_clean']:
            self.stdout.write(self.style.SUCCESS(
                'Bank notifications: none stuck, none flagged.'))
        else:
            self.stdout.write(self.style.WARNING(
                f"Bank notifications: {n['stuck']} stuck past received, "
                f"{n['failed']} could not be classified — check the FNB "
                f"Webhook Events admin."))

        if opts['email']:
            from fnb.three_way_check_email import send_report
            out = send_report(res)
            self.stdout.write(self.style.SUCCESS(
                f"emailed={out.get('sent')} to={out.get('to')}")
                if out.get('sent') else self.style.WARNING(
                f"not sent: {out.get('reason')}"))
