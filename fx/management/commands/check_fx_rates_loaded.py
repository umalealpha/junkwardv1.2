"""
check_fx_rates_loaded — daily FX-rate loading chase (CFO/Oprah bug 5257d640).

Oprah's risk: the Finance Manager must click 'Load BoB rate' manually each day;
if they forget, FX revaluations run on stale rates and reporting is wrong.

CFO brief 2026-08-26: stop the standalone "BoB FX rates not loaded" do-not-reply
email. Instead raise an Omni task to Kago Tshutlhedi + Pako to load & approve the
day's Bank-of-Botswana rates, and auto-close it when the rates are approved.

This command now just drives the FX stuck_work watcher (fx/stuck.py) on the same
business-day schedule: it creates / refreshes the rolling task while rates are
missing and closes it the moment they are loaded + approved. It NEVER posts or
approves a rate. No email is sent from here (the 06:30 task digest reminds the
assignees; CFO/EXCO are deliberately not escalated — FX is a Finance-only chain).

Schedule (prod root cron, business days):
    30 8  * * 1-5  …manage.py check_fx_rates_loaded
    0  12 * * 1-5  …manage.py check_fx_rates_loaded      # refresh if still missing

Flags: --dry-run (evaluate + print, create/close nothing), --date YYYY-MM-DD
(only affects the weekend-skip + task deadline; the rate check reads today).
"""
from __future__ import annotations

import datetime as _dt

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Raise/auto-close the Kago+Pako task to load today's BoB FX rates."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--date', default='')

    def handle(self, *args, **opts):
        from core.stuck_work import sweep
        from fx.stuck import FX_WATCHER

        today = timezone.localdate()
        if opts.get('date'):
            try:
                today = _dt.date.fromisoformat(opts['date'])
            except ValueError:
                self.stderr.write('bad --date'); return

        # Skip weekends — BoB doesn't publish; no task.
        if today.weekday() >= 5:
            self.stdout.write(f'{today} is a weekend — BoB does not publish; skipping.')
            return

        dry = bool(opts.get('dry_run'))
        res = sweep(watchers=[FX_WATCHER], today=today, dry_run=dry)
        self.stdout.write(
            f'{today}: FX watcher — items={res["items"]} '
            f'created={res["tasks_created"]} refreshed={res["tasks_refreshed"]} '
            f'closed={res["tasks_closed"]}' + (' (dry-run)' if dry else ''))
