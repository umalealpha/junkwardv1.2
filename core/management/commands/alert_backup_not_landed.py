"""
core/management/commands/alert_backup_not_landed.py

Send the CFO one plain-English email when the off-site database copy has not
landed. DETECTION lives in ops/backup_watch.sh, not here — the host has the AWS
CLI the backup itself uses, and boto3 is deliberately NOT a dependency of this
image. This half owns only the thing Django is needed for: the house-template
email path.

WHY THIS EXISTS (2026-08-25)
────────────────────────────
The off-site copy of the omni database stopped landing on 7 JULY 2026 and nobody
knew for SEVEN WEEKS. The job was not missing — it ran every night, dumped the
database, encrypted it, and then had its push refused:

    File dumps/omni-2026-08-25.sql.gz.gpg is 139.05 MB;
    this exceeds GitHub's file size limit of 100.00 MB

The database had grown past 100MB compressed. Because the dump and the GPG step
still worked, the artifact looked current and a dry-run passed — which is how a
readiness review came away reporting that leg as healthy. Nothing ever looked at
the push result.

A backup nobody checks is not a backup. It is a cron job.

Run:  python manage.py alert_backup_not_landed --detail "<what is wrong>"
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

CFO_EMAIL = 'pganesharajah@alphadirect.co.bw'

SUBJECT = 'Off-site database copy has not landed'


class Command(BaseCommand):
    help = ('Email the CFO that the nightly off-site database copy has not '
            'landed. Called by ops/backup_watch.sh, which does the detecting.')

    def add_arguments(self, parser):
        parser.add_argument('--detail', required=True,
                            help='Plain-English description of what is wrong.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the email body; send nothing.')

    def handle(self, *args, **opts):
        detail = (opts['detail'] or '').strip()
        if not detail:
            # Fail loudly rather than send a blank alert: an empty alert reads
            # as "something is wrong but we cannot say what", which is how a
            # real warning gets ignored.
            raise CommandError('--detail is required and must not be empty.')

        body = (
            'The off-site copy of the Omni database has not landed.\n\n'
            f'{detail}\n\n'
            'What this means in practice: Omni itself is running normally. The '
            'problem is the spare copy kept outside the server — the one we '
            'would restore from if we ever had to.\n\n'
            'This alert exists because exactly this went unnoticed for seven '
            'weeks in July and August 2026. The nightly job ran every single '
            'night; only its last step failed, and nothing was watching it.'
        )

        if opts['dry_run']:
            self.stdout.write(body)
            return

        from core.notifications import send_html_with_cfo_cc, wrap_plain_as_html
        try:
            sent = send_html_with_cfo_cc(
                subject=SUBJECT,
                html=wrap_plain_as_html(body),
                to=[CFO_EMAIL],
                text_fallback=body,
            )
        except Exception as exc:  # noqa: BLE001
            # Never let a mail failure hide the finding — the caller's cron log
            # keeps stderr, and ops/backup_watch.sh has already printed it.
            raise CommandError(f'could not send the backup alert: {exc}') from exc
        self.stdout.write(self.style.SUCCESS(f'alert sent (result={sent})'))
