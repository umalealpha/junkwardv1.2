"""Tests for `expire_reinsurer_approvals` and its cron scheduling.

The command moves APPROVED reinsurers whose `expiry_date` is in the past to
EXPIRED, writes an approval transition, and is idempotent. If it is not run,
or if the cron wiring is missing, expired approved reinsurers would remain
placeable and continue to appear in risk placements. These tests protect the
command behaviour and the scheduling that fires it.
"""

import io
import re
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from reinsurance.models import Reinsurer, ReinsurerApprovalTransition


class ExpireReinsurerApprovalsCommandTests(APITestCase):
    def _create_reinsurer(self, name, short_code, approval_status, expiry_date):
        return Reinsurer.objects.create(
            name=name,
            short_code=short_code,
            approval_status=approval_status,
            expiry_date=expiry_date,
        )

    def test_approved_reinsurer_expiring_yesterday_is_moved_to_expired_and_transition_written(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        reinsurer = self._create_reinsurer(
            name="Expired Approved Reinsurer",
            short_code="EXPIRED1",
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            expiry_date=yesterday,
        )

        out = io.StringIO()
        call_command("expire_reinsurer_approvals", stdout=out)

        reinsurer.refresh_from_db()
        self.assertEqual(reinsurer.approval_status, Reinsurer.ApprovalStatus.EXPIRED)
        self.assertEqual(
            ReinsurerApprovalTransition.objects.filter(
                reinsurer=reinsurer,
                from_status=Reinsurer.ApprovalStatus.APPROVED,
                to_status=Reinsurer.ApprovalStatus.EXPIRED,
            ).count(),
            1,
        )
        self.assertIn("1 reinsurer approval(s) moved to EXPIRED", out.getvalue())

    def test_approved_reinsurer_expiring_tomorrow_and_none_expiry_are_untouched(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        future_reinsurer = self._create_reinsurer(
            name="Future Expiry Reinsurer",
            short_code="FUTURE1",
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            expiry_date=tomorrow,
        )
        no_expiry_reinsurer = self._create_reinsurer(
            name="No Expiry Reinsurer",
            short_code="NOEXPIRY1",
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            expiry_date=None,
        )

        call_command("expire_reinsurer_approvals", stdout=io.StringIO())

        future_reinsurer.refresh_from_db()
        no_expiry_reinsurer.refresh_from_db()
        self.assertEqual(future_reinsurer.approval_status, Reinsurer.ApprovalStatus.APPROVED)
        self.assertEqual(no_expiry_reinsurer.approval_status, Reinsurer.ApprovalStatus.APPROVED)
        self.assertFalse(
            ReinsurerApprovalTransition.objects.filter(
                reinsurer__in=[future_reinsurer, no_expiry_reinsurer]
            ).exists()
        )

    def test_suspended_reinsurer_past_expiry_is_not_touched(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        suspended = self._create_reinsurer(
            name="Suspended Past Expiry",
            short_code="SUSPEND1",
            approval_status=Reinsurer.ApprovalStatus.SUSPENDED,
            expiry_date=yesterday,
        )

        call_command("expire_reinsurer_approvals", stdout=io.StringIO())

        suspended.refresh_from_db()
        self.assertEqual(suspended.approval_status, Reinsurer.ApprovalStatus.SUSPENDED)
        self.assertFalse(
            ReinsurerApprovalTransition.objects.filter(reinsurer=suspended).exists()
        )

    def test_running_command_twice_moves_nothing_second_time_and_writes_no_second_transition(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        reinsurer = self._create_reinsurer(
            name="Idempotent Reinsurer",
            short_code="IDEMPOT1",
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            expiry_date=yesterday,
        )

        call_command("expire_reinsurer_approvals", stdout=io.StringIO())
        self.assertEqual(
            ReinsurerApprovalTransition.objects.filter(reinsurer=reinsurer).count(),
            1,
        )

        out2 = io.StringIO()
        call_command("expire_reinsurer_approvals", stdout=out2)

        reinsurer.refresh_from_db()
        self.assertEqual(reinsurer.approval_status, Reinsurer.ApprovalStatus.EXPIRED)
        self.assertEqual(
            ReinsurerApprovalTransition.objects.filter(reinsurer=reinsurer).count(),
            1,
        )
        self.assertIn("0 reinsurer approval(s) moved to EXPIRED", out2.getvalue())

    def test_dry_run_changes_nothing_at_all(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        reinsurer = self._create_reinsurer(
            name="Dry Run Reinsurer",
            short_code="DRYRUN1",
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            expiry_date=yesterday,
        )

        call_command("expire_reinsurer_approvals", "--dry-run", stdout=io.StringIO())

        reinsurer.refresh_from_db()
        self.assertEqual(reinsurer.approval_status, Reinsurer.ApprovalStatus.APPROVED)
        self.assertFalse(
            ReinsurerApprovalTransition.objects.filter(reinsurer=reinsurer).exists()
        )

    def test_after_expiry_may_be_placed_is_false(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        reinsurer = self._create_reinsurer(
            name="May Be Placed Reinsurer",
            short_code="MAYBEPL1",
            approval_status=Reinsurer.ApprovalStatus.APPROVED,
            expiry_date=yesterday,
        )

        call_command("expire_reinsurer_approvals", stdout=io.StringIO())

        reinsurer.refresh_from_db()
        self.assertFalse(reinsurer.may_be_placed())

    def test_cron_file_exists_and_job_name_in_install_crons_enabled(self):
        base = Path(settings.BASE_DIR)
        cron_file = base / "infra" / "cron" / "reinsurer-approval-expiry.cron"
        install_script = base / "infra" / "install-crons.sh"

        self.assertTrue(cron_file.exists(), msg=f"Missing cron file: {cron_file}")
        self.assertTrue(install_script.exists(), msg=f"Missing install script: {install_script}")

        cron_text = cron_file.read_text()
        self.assertIn("expire_reinsurer_approvals", cron_text)

        script_text = install_script.read_text()
        enabled_match = re.search(r"ENABLED\s*=\s*\(([^)]*)\)", script_text, re.S)
        self.assertIsNotNone(enabled_match, "ENABLED list not found in install-crons.sh")
        self.assertIn("reinsurer-approval-expiry", enabled_match.group(1))