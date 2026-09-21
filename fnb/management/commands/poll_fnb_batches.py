"""
Management command: poll_fnb_batches

Refresh the FNB status of every batch that is still in flight.

Why this exists (2026-08-20): `refresh_batch_status()` was only ever reachable
from the operator's "Refresh" button in the FNB Batches screen. Nothing polled
automatically, so the 2026-08-19 penny test sat at `submitted` while FNB had
already rejected it (RR10 INVALID CHARACTER SET). The reject was readable from
FNB the whole time — we simply never asked. A silent RJCT is indistinguishable
from a successful payment on the screen, which is how a one-character bug
survived as a "the bank has not given us production credentials" problem.

Access (H54 — this command is a second door onto the same action as the
FNBRefreshBatchView): that view's only gate is IsAuthenticated — no company
scope and no CanManageFNB. It is waived here because there is no operator at
all: this is the system sweeping every company's in-flight batches, the same
way pull_fnb_statements and poll_fnb_notifications already do. The call is
read-only against FNB (retrieveReport) and writes only the status FNB reports,
so it cannot move money or widen what any user can see. `refresh_batch_status`
is passed no user, so nothing is attributed to a person.

Run every 5 minutes from cron. Install with `infra/install-crons.sh` — editing
infra/cron/fnb-sync.cron alone does NOT schedule anything on the host.

"HTTP 425 Too Early. Retry-After 120 seconds" is NOT a rate limit (2026-09-04).
It is FNB's answer for an instruction it has not processed yet — typically a
batch still awaiting authorisation on the bank — so there is no status report
to return. Proof from FNBSyncLog: every batch that later settled shows
hundreds of 425s and exactly ONE 200 (the poll that found it processed); a
settled batch answers 200 and a waiting one answers 425 seconds apart. So a
425 is recorded as "not processed yet", not as an error, and the sweep simply
moves on. (Treating it as throttling and pausing/stopping the sweep — tried
and reverted the same day — only meant fewer batches got read.)

Usage:
    python manage.py poll_fnb_batches
    python manage.py poll_fnb_batches --max-age-hours 168   # widen the sweep
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from fnb.client import FNBAPIError
from fnb.models import FNBBatchSubmission
from fnb.payments import refresh_batch_status

# Statuses where FNB may still have news for us. PENDING has no reference yet;
# FAILED / SETTLED / CANCELLED are terminal. UNKNOWN is listed for the day a
# reference exists for one, but in practice it never matches: UNKNOWN is only
# set from the FNBAPIError branch, which runs BEFORE fnb_reference is written,
# so every UNKNOWN batch is excluded below by the empty-reference filter. Those
# still need a human, as the status says.
IN_FLIGHT = (
    FNBBatchSubmission.Status.SUBMITTED,
    FNBBatchSubmission.Status.ACKNOWLEDGED,
    FNBBatchSubmission.Status.UNKNOWN,
)


class Command(BaseCommand):
    help = 'Poll FNB for the status of every in-flight EFT batch.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age-hours', type=int, default=720,
            help='Only poll batches created within this many hours '
                 '(default 720 = 30 days).',
        )

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(hours=opts['max_age_hours'])
        batches = (FNBBatchSubmission.objects
                   .filter(status__in=IN_FLIGHT, created_at__gte=cutoff)
                   .exclude(fnb_reference='')
                   .order_by('created_at'))

        checked = changed = errored = not_ready = 0
        for batch in batches:
            was = batch.status
            checked += 1
            try:
                refresh_batch_status(batch)
            except FNBAPIError as exc:
                if exc.status_code == 425:
                    # Not processed by the bank yet (see docstring) — normal
                    # for a batch awaiting authorisation. Nothing to record.
                    not_ready += 1
                    continue
                errored += 1
                self.stderr.write(
                    f'{batch.idempotency_key}: poll failed — '
                    f'{type(exc).__name__}: {exc}'
                )
                continue
            except Exception as exc:                       # noqa: BLE001
                # One unreachable batch must not stop the sweep — the next
                # batch may be the one carrying a reject nobody has seen.
                errored += 1
                self.stderr.write(
                    f'{batch.idempotency_key}: poll failed — '
                    f'{type(exc).__name__}: {exc}'
                )
                continue
            batch.refresh_from_db()
            if batch.status != was:
                changed += 1
                line = (f'{batch.idempotency_key}: {was} -> {batch.status}'
                        f'{" | " + batch.failure_reason if batch.failure_reason else ""}')
                if batch.status == FNBBatchSubmission.Status.FAILED:
                    self.stdout.write(self.style.ERROR(line))
                else:
                    self.stdout.write(self.style.SUCCESS(line))

        self.stdout.write(self.style.SUCCESS(
            f'FNB batch poll: {checked} checked, {changed} changed, '
            f'{errored} errored, {not_ready} not yet processed by FNB'
        ))
