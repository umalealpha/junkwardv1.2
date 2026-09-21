"""
Escalate a flagged recovery that has been sitting for 48 hours with no demand
letter to Kago Tshutlhedi (Finance Manager).

    python manage.py subrogation_escalation_check [--dry-run]

Pako Kago raised this himself, as gap 1 of his own request: "A blocking pop-up
that only she can clear stalls the recovery if she's on leave. Suggest an
auto-escalation (e.g. to Kago or yourself) after 48 hours unclear."

TIME. Everything below is timezone-aware `timezone.now()`. Django's TIME_ZONE is
Africa/Gaborone (UTC+2), so every date and hour a human reads in the email is
Gaborone time, while the arithmetic is done in UTC underneath and therefore
cannot drift. The cron that runs this fires on the host, whose clock is UTC —
see the comment in infra/cron/subrogation-escalation.cron.

WHAT IT MEASURES, and why it is not the obvious thing. The clock runs from
`recovery_flagged_at` — the moment the flag arrived — and NOT from `updated_at`.
A rule written against the current state of the row, or against when the row was
last touched, is defeated by ordinary work: open the case, save it, and an
`updated_at` clock starts again from zero, for ever. This one cannot be reset by
touching the record. Only the demand letter actually going out stops it.

It fires ONCE per case: `escalated_at` is stamped, and `due_for_escalation`
excludes anything already stamped. Run it hourly and a case is escalated exactly
one time.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from claims.models import Subrogation
from claims.subrogation_alert import (ESCALATION_HOURS, due_for_escalation,
                                      escalate)

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (f'Escalate recovery-possible claims with no demand letter after '
            f'{ESCALATION_HOURS} hours.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would escalate; send nothing, stamp nothing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        now = timezone.now()
        due = list(due_for_escalation(Subrogation.objects.all(), now=now))

        if not due:
            self.stdout.write(
                f'Nothing overdue at {timezone.localtime(now):%Y-%m-%d %H:%M} '
                f'(Gaborone).')
            return

        sent = 0
        for sub in due:
            flagged = timezone.localtime(sub.recovery_flagged_at)
            line = (f'{sub.claim_reference} — flagged {flagged:%d %b %Y %H:%M} '
                    f'(Gaborone), no letter')
            if dry:
                self.stdout.write(f'  would escalate: {line}')
                continue
            try:
                escalate(sub, now=now)
                sent += 1
                self.stdout.write(f'  escalated: {line}')
            except Exception as exc:                            # noqa: BLE001
                # Do NOT stamp escalated_at on a failure — an unsent escalation
                # that marks itself done is worse than no escalation at all.
                log.warning('Subrogation escalation failed for %s: %s',
                            sub.claim_reference, exc)
                self.stderr.write(f'  FAILED: {line}: {exc}')

        self.stdout.write(
            f'{"DRY RUN — " if dry else ""}{len(due)} overdue, {sent} escalated '
            f'to the Finance Manager.')
