"""Alert if today's ADH → AFA load file never got built.

    python manage.py afa_watchdog [--hour 9]

A silently missed morning is invisible until AFA complain — and the blue/green
cutover is already known to leave crons stopped. This runs a few hours after
the builder and emails if there is nothing for today, or if what is there
aborted or failed.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from healthcare.models import AfaLoadFileRun

log = logging.getLogger('afa-loadfile')

_RECIPIENTS_SETTING = 'AFA_WATCHDOG_RECIPIENTS'
_DEFAULT_RECIPIENTS = ['rtonkope@alphadirect.co.bw', 'mtlagae@alphadirect.co.bw']


class Command(BaseCommand):
    help = "Alert if today's AFA load file is missing, aborted or failed."

    def add_arguments(self, parser):
        parser.add_argument('--hour', type=int, default=9,
                            help='Only alert from this hour onward (local time).')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        now = timezone.localtime()
        if now.hour < opts['hour']:
            self.stdout.write(f'Too early ({now:%H:%M}) — the builder may not have run yet.')
            return

        today = timezone.localdate()
        run = AfaLoadFileRun.objects.filter(run_date=today).first()

        if run is None:
            subject = f'AFA load file MISSING for {today}'
            message = (f'No AFA load file was built for {today}. The morning job has '
                       f'not run, or it failed before it could record anything.')
        elif run.status == AfaLoadFileRun.Status.ABORTED:
            subject = f'AFA load file ABORTED for {today}'
            message = (f'The {today} run was stopped by the safety fence and no file '
                       f'was produced.\n\nReason: {run.abort_reason}')
        elif run.status == AfaLoadFileRun.Status.FAILED:
            subject = f'AFA load file FAILED to send for {today}'
            message = (f'The {today} file was built ({run.row_count} rows) but did not '
                       f'reach AFA.\n\n{run.send_error}')
        else:
            self.stdout.write(f'{today}: {run.status}, {run.row_count} rows — nothing to alert.')
            return

        self.stderr.write(self.style.ERROR(subject))
        if opts.get('dry_run'):
            self.stdout.write(message)
            return

        from django.conf import settings as dj_settings
        recipients = getattr(dj_settings, _RECIPIENTS_SETTING, None) or _DEFAULT_RECIPIENTS
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(',') if r.strip()]

        try:
            from core.notifications import send_with_cfo_cc
            send_with_cfo_cc(subject=subject, body=message, to=list(recipients))
        except Exception as exc:                       # noqa: BLE001
            # The alert failing must not hide the thing it was alerting about.
            log.error('afa watchdog could not send its alert: %s', exc.__class__.__name__)
            self.stderr.write(self.style.ERROR(
                f'Could not email the alert ({exc.__class__.__name__}) — '
                f'the underlying problem still stands: {subject}'))
