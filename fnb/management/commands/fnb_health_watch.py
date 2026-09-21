"""
Management command: fnb_health_watch

Evaluate the FNB link and email the CFO + Kago + Pako if it just went DOWN
(or just RECOVERED), AND alert on any EFT batch FNB has left unconfirmed for
too long. Run hourly by cron on prod (/etc/cron.d/fnb-health-watch, installed
from infra/cron/fnb-health-watch.cron by infra/install-crons.sh — editing the
.cron file alone schedules nothing).

    python manage.py fnb_health_watch
    python manage.py fnb_health_watch --dry-run        # evaluate + show, never email
    python manage.py fnb_health_watch --stuck-hours 6  # widen the stuck threshold
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from fnb.health_watch import run_watch


class Command(BaseCommand):
    help = 'Alert the CFO/Finance when the FNB bank connection goes down or recovers.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Evaluate and print, but never send email or save state.')
        parser.add_argument('--stuck-hours', type=float, default=None,
                            help='Alert on batches unconfirmed longer than this '
                                 '(default: settings.FNB_STUCK_BATCH_ALERT_HOURS, '
                                 'else 2).')

    def handle(self, *args, **opts):
        res = run_watch(dry_run=opts.get('dry_run', False),
                        stuck_hours=opts.get('stuck_hours'))
        self.stdout.write(self.style.SUCCESS(
            f'FNB health watch: verdict={res["verdict"]} (was {res["prev"]}), '
            f'action={res["action"]}, emailed={res["emailed"]}, '
            f'signals={res["meta"]}'
        ))
        stuck = res.get('stuck') or {}
        line = (f'FNB stuck batches: {stuck.get("stuck_count", 0)} over '
                f'{stuck.get("threshold_hours", "?")}h, '
                f'action={stuck.get("action", "")}, '
                f'emailed={stuck.get("emailed", False)}')
        self.stdout.write(self.style.ERROR(line) if stuck.get('stuck_count')
                          else self.style.SUCCESS(line))

        if opts.get('dry_run') and res.get('body'):
            self.stdout.write('\n--- would send (link) ---')
            self.stdout.write(f'Subject: {res.get("subject", "")}')
            self.stdout.write(res['body'])
        if opts.get('dry_run') and stuck.get('body'):
            self.stdout.write('\n--- would send (stuck batches) ---')
            self.stdout.write(f'Subject: {stuck.get("subject", "")}')
            self.stdout.write(stuck['body'])
