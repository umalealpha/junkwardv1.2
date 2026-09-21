from __future__ import annotations

import datetime
import logging

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from hris.ceo_pulse import send_pulse

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send or preview the Sunday CEO Workforce Pulse (L-PULSE).'

    def add_arguments(self, parser):
        parser.add_argument('--preview', action='store_true',
                            help='Send to the CFO preview address only.')
        parser.add_argument('--send', action='store_true',
                            help='Send to the CEO with the CFO/COO/Unami CC.')
        parser.add_argument('--dry-run', action='store_true', dest='dry_run',
                            help='Build everything but send nothing.')
        parser.add_argument('--today', dest='today',
                            help='Local date in YYYY-MM-DD format for the pulse window.')

    def _build_td_client(self):
        """Copy the Time Doctor client construction from send_exceptions_report.

        Wrapped in try/except -> None: a stale/missing Time Doctor client must
        HELD, never crash the command."""
        try:
            from integrations.timedoctor import TimeDoctorClient
            client = TimeDoctorClient.from_settings()
            if not getattr(client, 'configured', True):
                self.stdout.write(self.style.WARNING(
                    'SKIPPED: TIMEDOCTOR_TOKEN not set / client not configured.'))
                return None
            return client
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(
                f'Time Doctor client could not be constructed: {exc}'))
            return None

    def handle(self, *args, **opts):
        preview = bool(opts.get('preview'))
        send = bool(opts.get('send'))
        dry_run = bool(opts.get('dry_run'))

        # Default when neither --send nor --preview is provided.
        if not (preview or send):
            dry_run = True

        if send:
            preview = False

        if opts.get('today'):
            try:
                today = datetime.date.fromisoformat(opts['today'])
            except ValueError:
                raise CommandError('--today must be YYYY-MM-DD')
        else:
            today = timezone.localdate()

        td_client = self._build_td_client()
        result = send_pulse(
            preview=preview,
            dry_run=dry_run,
            today=today,
            td_client=td_client,
        )

        if result.get('held'):
            missing = ', '.join(result.get('missing', ['unknown missing data']))
            self.stdout.write(self.style.WARNING(f'HELD: {missing}'))
            return

        if result.get('sent'):
            dest = ', '.join(result.get('to', []))
            cc = ', '.join(result.get('cc', [])) or '—'
            self.stdout.write(self.style.SUCCESS(f'Sent to {dest} (cc {cc}).'))
            return

        if result.get('prepared'):
            kind = 'preview' if result.get('preview') else 'dry'
            dest = ', '.join(result.get('to', []))
            complete = result.get('complete', True)
            self.stdout.write(
                f'[{kind}] html_len={result.get("html_len")} '
                f'complete={complete} to={dest}')
            return

        self.stdout.write(self.style.WARNING('No action taken.'))
