"""Load the weekly ADH health claims EFT settlement file into Omni (B4).

Keetile Mokhendo. Every Saturday AFA send four near-identical emails; exactly
one carries the file that is ours. This reads the settlement mailbox, picks that
one, opens its attachment, and raises ONE Omni payment request per claim line
through the ordinary gated create path.

🔴 IT CREATES PAYMENT REQUESTS AND STOPS. Finance sign-off in Omni and CFO
authorisation in FNB stay manual. No money moves from here, ever.

🔴 IT NEEDS PERMISSION TO READ THE MAILBOX, WHICH OMNI DOES NOT HAVE YET.
Omni's Microsoft registration is Mail.Send only. Until an administrator grants
Mail.Read, every run FAILS LOUDLY: a FAILED run record on the screen plus an
email to Keetile naming the permission to ask IT for. Never a silent "no file
this week" — that is the one outcome that would let a whole settlement go
unraised with nobody the wiser.

    manage.py load_adh_settlements                # load it
    manage.py load_adh_settlements --dry-run      # say what it would raise
    manage.py load_adh_settlements --attachment X # a named attachment in the mailbox

NEVER FAILS SILENTLY. A Saturday with no email, an unreadable zip, a listing
whose columns we do not recognise, a mailbox we may not read — each one emails
Keetile saying so, in plain English, with no claim detail in the subject or the
body.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from healthcare import claims_settlement as cs
from healthcare.afa_mailbox import MailboxNotReadable
from healthcare.models import AdhSettlementRun

# "We could not even reach the source" is a DIFFERENT exception class from "the
# file was wrong" — the mailbox raises its own MailboxNotReadable. Catching only
# SettlementFileError let a source we cannot reach — the single most likely
# Saturday outcome — kill the command with a traceback: no run record, no email,
# nothing. NEVER FAILS SILENTLY means catching both. (Fable, 13-Sep-2026 —
# learned on the SFTP drop, and it holds just the same for the mailbox.)
SETTLEMENT_FAILURES = (cs.SettlementFileError, MailboxNotReadable)

log = logging.getLogger('adh-settlement')


class Command(BaseCommand):
    help = ('Turn the weekly AFA ADH claims EFT settlement email into Omni '
            'payment requests. Creates requests only — it never releases money.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would be raised; write nothing.')
        parser.add_argument('--attachment', default=None,
                            help='Process this named attachment instead of '
                                 'selecting one (it still has to pass the filter).')
        parser.add_argument('--no-email', action='store_true',
                            help='Do not send the exception email (tests, reruns).')

    def handle(self, *args, **opts):
        # Botswana time. The cron on the box is UTC and the files land 01:00-01:20
        # Gaborone, so the date a run is stamped with comes from the local
        # calendar, never from date.today().
        today = timezone.localdate()
        dry = bool(opts['dry_run'])

        try:
            chosen, rejected = self._pick(opts)
        # TWO classes, both caught. "We could not reach the source at all" is a
        # different failure from "the file was wrong", and catching only
        # SettlementFileError let the most likely Saturday outcome kill the
        # command with a traceback: no run record, no email, nothing. NEVER
        # FAILS SILENTLY means catching both. (Fable, 13-Sep-2026 — learned on
        # the SFTP drop, and it holds just the same for the mailbox.)
        except MailboxNotReadable as exc:
            # Its own branch, and its own subject line: "we are not allowed to
            # read the mailbox" is a thing IT must fix, not a quiet week.
            self._fail(today, str(exc), notify=not opts['no_email'], dry=dry,
                       subject='ADH settlement — Omni cannot read the mailbox')
            return
        except cs.SettlementFileError as exc:
            # getattr, not exc.rejected: a failure that never got as far as
            # listing the mailbox carries no rejected list.
            self._fail(today, str(exc), notify=not opts['no_email'], dry=dry,
                       rejected=getattr(exc, 'rejected', []))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Settlement attachment chosen: {chosen.name}'))
        for r in rejected:
            self.stdout.write(f'   not processed: {r["name"]} — {r["reason"]}')

        # Keyed on the ATTACHMENT BYTES. A forwarded or re-sent copy of the same
        # file is a different message with a different id and often a "FW:"
        # subject; both of those would look new, and the bytes do not.
        sha = cs.file_fingerprint(chosen.body)
        existing = AdhSettlementRun.objects.filter(file_sha256=sha).first()
        if existing is not None and not dry:
            self.stdout.write(self.style.WARNING(
                f'This exact file was already loaded on {existing.loaded_on} '
                f'({existing.created_count} request(s) raised). Nothing to do.'))
            return

        try:
            # ASKED BEFORE THE RUN ROW EXISTS, and inside this `try`. Asked
            # afterwards — which is where create_payment_requests asks it — the
            # raise escapes every except: traceback, no email, and a row already
            # written carrying this file's UNIQUE fingerprint and the model's
            # default status of LOADED. Every later run of the same file would
            # then say "already loaded ... Nothing to do" and stop, so one
            # missing login would block that settlement for good, in silence.
            cs.require_maker()
            member, blob = cs.unzip_listing(chosen.body, chosen.name)
            lines = cs.parse_eft_xls(blob, filename=member)
        except SETTLEMENT_FAILURES as exc:
            self._fail(today, str(exc), notify=not opts['no_email'], dry=dry,
                       file_name=chosen.name, rejected=rejected)
            return

        self.stdout.write(f'   claim lines in the listing: {len(lines)}')
        if dry:
            run = AdhSettlementRun(loaded_on=today, file_name=chosen.name)
            result = cs.create_payment_requests(run, lines, dry_run=True)
            self._report(result)
            self.stdout.write(self.style.WARNING(
                '\nDRY RUN — nothing was written.'))
            return

        run = AdhSettlementRun.objects.create(
            loaded_on=today, file_name=chosen.name, file_sha256=sha,
            rejected=rejected, line_count=len(lines))
        result = cs.create_payment_requests(run, lines)

        run.created_count = len(result.created)
        run.skipped_count = len(result.skipped)
        run.failed_count = len(result.failed)
        run.problems = result.failed
        run.status = (AdhSettlementRun.Status.PARTIAL if result.failed
                      else AdhSettlementRun.Status.LOADED if result.created
                      else AdhSettlementRun.Status.NOTHING)
        run.save(update_fields=['created_count', 'skipped_count', 'failed_count',
                                'problems', 'status'])

        self._report(result)
        # Counts only — a claim number in /var/log is exactly what the data
        # protection rule forbids.
        log.info('adh-settlement: %s lines, %s raised, %s already loaded, %s problems',
                 len(lines), len(result.created), len(result.skipped),
                 len(result.failed))
        if result.failed and not opts['no_email']:
            self._notify(
                'ADH settlement — some claim lines could not be raised',
                f'{len(result.failed)} of {len(lines)} claim line(s) in this week\'s '
                f'AFA settlement listing could not be turned into a payment request. '
                f'They are listed on the ADH settlement runs screen in Omni, with the '
                f'reason for each. Nothing was paid and nothing was released — these '
                f'are payment requests only.')

    # ------------------------------------------------------------------ helpers
    def _pick(self, opts):
        if opts['attachment']:
            candidates = [c for c in cs.fetch_candidates()
                          if c.name == opts['attachment']]
            if not candidates:
                raise cs.SettlementFileError(
                    f'{opts["attachment"]} is not in the settlement mailbox.')
            chosen, rejected = cs.select_settlement_file(candidates)
            if chosen is None:
                raise cs.SettlementFileError(
                    f'{opts["attachment"]} does not pass the filter: '
                    f'{rejected[0]["reason"]}',
                    rejected=rejected)
            return cs.load_body(chosen, rejected), rejected
        return cs.fetch_file()

    def _report(self, result: cs.LoadResult):
        for ref in result.created:
            self.stdout.write(self.style.SUCCESS(f'   raised {ref}'))
        for s in result.skipped:
            self.stdout.write(f'   skipped {s["claim_number"]} — {s["reason"]}')
        for f in result.failed:
            self.stdout.write(self.style.ERROR(
                f'   PROBLEM {f["claim_number"]} — {f["reason"]}'))
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{len(result.created)} payment request(s) raised, '
            f'{len(result.skipped)} already loaded, {len(result.failed)} problem(s). '
            f'They are awaiting finance sign-off — nothing has been paid.'))

    def _fail(self, today, message: str, *, notify: bool, dry: bool,
              file_name: str = '', rejected: list | None = None,
              subject: str = 'ADH settlement file — nothing loaded this week'):
        # `rejected` matters most HERE. A failed run is the Saturday nobody can
        # explain from the outside, so the row keeps the structured list of what
        # else arrived and why each was passed over — not only the prose message.
        self.stderr.write(self.style.ERROR(message))
        if not dry:
            AdhSettlementRun.objects.create(
                loaded_on=today, file_name=file_name[:255],
                status=AdhSettlementRun.Status.FAILED, error=message[:4000],
                rejected=rejected or [])
        if notify:
            self._notify(subject, message)

    def _notify(self, subject: str, message: str):
        """Tell the person who owns this feed. Never silence."""
        try:
            from core.notifications import send_html_with_cfo_cc, wrap_plain_as_html
            send_html_with_cfo_cc(
                subject=subject,
                html=wrap_plain_as_html(
                    f'{message}\n\nThis is the automatic weekly ADH claims '
                    f'settlement load. It raises payment requests in Omni only — '
                    f'it never releases money.'),
                to=[cs.ENTERED_BY_EMAIL])
        except Exception:                                          # noqa: BLE001
            # An email that could not be sent must not hide the failure it was
            # reporting — it is already on stderr and in the run record.
            log.exception('adh-settlement: could not send the exception email')
