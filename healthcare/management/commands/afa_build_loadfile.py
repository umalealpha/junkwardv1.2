"""Build the day's ADH → AFA load file. The morning cron entry point.

    python manage.py afa_build_loadfile [--date YYYY-MM-DD] [--send]

Building is safe and repeatable — one run per date, and a run that has not been
released is replaced, so running twice in a morning changes nothing.

--send only does anything when AFA_LOADFILE_AUTOSEND is on. Until it is, the
file waits on the screen for a person to release it.
"""
from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from healthcare import afa_service, afa_sftp
from healthcare.afa_members import GraphiteReplicaNotConfigured
from healthcare.models import AfaLoadFileRun


class Command(BaseCommand):
    help = "Build (and optionally send) the ADH → AFA member load file."

    def add_arguments(self, parser):
        parser.add_argument('--date', help='YYYY-MM-DD (defaults to today)')
        parser.add_argument('--send', action='store_true',
                            help='Deliver to AFA if automatic sending is switched on.')

    def handle(self, *args, **opts):
        run_date = None
        if opts.get('date'):
            try:
                run_date = datetime.strptime(opts['date'], '%Y-%m-%d').date()
            except ValueError:
                raise CommandError('--date must be YYYY-MM-DD')

        try:
            run = afa_service.build_and_store(run_date)
        except GraphiteReplicaNotConfigured as exc:
            # Not a crash — the cron must not go red because an env var is
            # unset. Say so and stop.
            self.stdout.write(self.style.WARNING(f'SKIPPED: {exc}'))
            return
        except ValueError as exc:
            self.stdout.write(self.style.WARNING(f'SKIPPED: {exc}'))
            return

        if run.status == AfaLoadFileRun.Status.ABORTED:
            # Loud on purpose. The fence firing means the source read looked
            # wrong, and silence here is how a scheme gets cancelled.
            self.stderr.write(self.style.ERROR(
                f'ABORTED for {run.run_date}: {run.abort_reason}'))
            raise CommandError('AFA load file aborted by the safety fence.')

        self.stdout.write(
            f'{run.run_date}: {run.row_count} rows '
            f'({run.new_count} new, {run.changed_count} changed, '
            f'{run.departure_count} departures), {run.held_count} held'
        )
        for reason, count in (run.held_reasons or {}).items():
            self.stdout.write(f'   held {count}: {reason}')

        if not opts.get('send'):
            return
        if not afa_sftp.autosend_enabled():
            self.stdout.write(self.style.WARNING(
                'Automatic sending is OFF — the file is waiting to be released.'))
            return
        if not run.row_count:
            self.stdout.write('Nothing to send.')
            return

        try:
            afa_sftp.send(run.file_name, run.file_body)
        except afa_sftp.AfaSendUnknown as exc:
            run.status = AfaLoadFileRun.Status.FAILED
            run.send_error = str(exc)
            run.save(update_fields=['status', 'send_error'])
            raise CommandError(str(exc))
        except (afa_sftp.AfaSendFailed, afa_sftp.AfaSendNotConfigured,
                afa_sftp.AfaSendDisabled) as exc:
            run.status = AfaLoadFileRun.Status.FAILED
            run.send_error = str(exc)
            run.save(update_fields=['status', 'send_error'])
            raise CommandError(str(exc))

        from django.utils import timezone
        run.status = AfaLoadFileRun.Status.SENT
        run.sent_at = timezone.now()
        run.save(update_fields=['status', 'sent_at'])
        try:
            afa_service.commit_snapshot(run)
        except afa_service.SnapshotNotRecorded as exc:
            raise CommandError(f'The file reached AFA, but {exc}')
        self.stdout.write(self.style.SUCCESS(f'Delivered {run.file_name} to AFA.'))
