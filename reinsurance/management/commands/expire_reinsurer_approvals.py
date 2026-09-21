"""Expire reinsurer approvals that are past their expiry date.

WHY THIS COMMAND EXISTS (reinsurance control QC, 16-Sep-2026)
------------------------------------------------------------
``reinsurance.onboarding.expire_due()`` was written, reviewed and unit-tested
(test_reinsurer_controls.py::test_expire_due_moves_only_approved_rows_past_
their_date) — and then nothing ever called it. No management command, no cron,
no signal. So an approved counterparty whose approval ran out last month still
answered ``may_be_placed() == True`` for ever, and the control centre counted it
under "approved". A rule that only runs when somebody remembers to run it is not
a control.

That is the same failure class as the Monday failed-debits job: merged, deployed
and never scheduled. The fix is in three parts and ALL THREE are needed —
this command, ``infra/cron/reinsurer-approval-expiry.cron``, and the entry in
``infra/install-crons.sh``'s ENABLED list. Editing the .cron file alone
schedules nothing on the host.

Safe to re-run: expire_due() is date-driven and idempotent, takes a row lock,
and re-checks the status inside the transaction, so two overlapping runs cannot
double-record. It deliberately leaves SUSPENDED and REJECTED rows alone —
a person put them there.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from reinsurance import onboarding


class Command(BaseCommand):
    help = ('Move approved reinsurers past their expiry date to EXPIRED, '
            'writing an approval-transition row for each. Idempotent.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would expire and change nothing.',
        )

    def handle(self, *args, **options):
        # Africa/Gaborone, never server UTC: an approval that runs out "today"
        # must mean today in Gaborone. timezone.localdate() honours the
        # project's TIME_ZONE, which is why it is used here and not
        # datetime.date.today().
        today = timezone.localdate()

        if options['dry_run']:
            from reinsurance.models import Reinsurer
            due = Reinsurer.objects.filter(
                approval_status=Reinsurer.ApprovalStatus.APPROVED,
                expiry_date__lt=today,
            ).order_by('name')
            self.stdout.write(f'{today.isoformat()} — {due.count()} approval(s) due to expire:')
            for r in due:
                self.stdout.write(f'  {r.short_code or r.name}  expired {r.expiry_date}')
            return

        moved = onboarding.expire_due(on=today)
        self.stdout.write(self.style.SUCCESS(
            f'{today.isoformat()} — {moved} reinsurer approval(s) moved to EXPIRED.'
        ))
