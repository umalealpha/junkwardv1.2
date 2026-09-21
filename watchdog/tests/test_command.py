from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase

from watchdog import checks as check_mod
from watchdog.checks import Check, CheckResult
from watchdog.models import Severity, WatchdogFinding, WatchdogReportLog, WatchdogRun

_KEY = "wd_test"
_DATE = "2020-01-01"        # a quiet, deterministic date (empty test DB, no MT)


def _fake_run():
    return [
        # danger, and it dishonestly claims to be auto-fixable — the command MUST
        # override that: a danger finding can never be auto-fixed.
        CheckResult(ok=False, title="FAKE danger", detail="d",
                    domains=("finance",), severity="danger", safe_to_autofix=True),
        CheckResult(ok=False, title="FAKE safe", detail="s", severity="warn",
                    safe_to_autofix=True, autofix_note="re-run the snapshot"),
        CheckResult(ok=False, title="FAKE info", detail="i", severity="info"),
        CheckResult(ok=True, title="FAKE ok"),
    ]


class RunWatchdogCommandTests(TestCase):
    def setUp(self):
        # Use the real CFO email so _cfo_user() picks this account deterministically
        # (migrations may seed other superusers).
        self.cfo = User.objects.create_superuser(
            "pganesharajah", "pganesharajah@alphadirect.co.bw", "pw")
        check_mod.REGISTRY[_KEY] = Check(
            key=_KEY, label="Fake", module="", category="business_rule",
            run=_fake_run)
        self.addCleanup(lambda: check_mod.REGISTRY.pop(_KEY, None))

    def test_run_records_findings_and_is_report_only(self):
        call_command("run_watchdog", "--date", _DATE, "--no-email")
        run = WatchdogRun.objects.get()
        self.assertEqual(run.status, WatchdogRun.Status.COMPLETE)
        self.assertTrue(run.dry_run)                       # report-only
        self.assertEqual(run.findings_danger, 1)
        self.assertEqual(run.findings_safe, 1)
        self.assertGreaterEqual(run.checks_passed, 1)

    def test_danger_finding_can_never_be_autofixed(self):
        call_command("run_watchdog", "--date", _DATE, "--no-email")
        danger = WatchdogFinding.objects.get(check_key=_KEY, severity=Severity.DANGER)
        self.assertTrue(danger.flagged)
        self.assertFalse(danger.safe_to_autofix)           # forced off despite claim
        self.assertIn("flagged", danger.auto_action)

    def test_safe_finding_is_dry_run_would_fix(self):
        call_command("run_watchdog", "--date", _DATE, "--no-email")
        safe = WatchdogFinding.objects.get(check_key=_KEY, severity=Severity.WARN)
        self.assertTrue(safe.safe_to_autofix)
        self.assertFalse(safe.flagged)
        self.assertTrue(safe.auto_action.startswith("would"), safe.auto_action)

    def test_info_finding_recorded(self):
        call_command("run_watchdog", "--date", _DATE, "--no-email")
        self.assertTrue(
            WatchdogFinding.objects.filter(check_key=_KEY, severity=Severity.INFO).exists())

    def test_danger_raises_one_exception_not_a_cfo_task(self):
        # CFO 2026-08-24: watchdog dangers belong on the Exceptions page, not the
        # CFO's task list — and any task an earlier build raised must be retired.
        from core.models import OmniTask
        from django.utils import timezone
        from exceptions.models import Exception as ExceptionModel

        stale = OmniTask.objects.create(
            assigner=self.cfo, assignee=self.cfo, title="Omni Watchdog: FAKE danger",
            body="old", source=f"watchdog:{_KEY}", due_at=timezone.now(),
            status=OmniTask.Status.PENDING)

        call_command("run_watchdog", "--date", _DATE, "--no-email")
        call_command("run_watchdog", "--date", _DATE, "--no-email")   # second night

        # Exactly one Exception on the Exceptions page (deduped, not piled up).
        excs = ExceptionModel.objects.filter(source_app="watchdog", source_id=_KEY)
        self.assertEqual(excs.count(), 1)
        self.assertEqual(excs.first().status, ExceptionModel.Status.OPEN)
        self.assertEqual(excs.first().requires_role,
                         ExceptionModel.RequiresRole.FINANCE_MANAGER)

        # No open watchdog task clutters the CFO's list; the stale one was cancelled.
        open_tasks = OmniTask.objects.filter(
            source=f"watchdog:{_KEY}",
            status__in=(OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS))
        self.assertEqual(open_tasks.count(), 0)
        stale.refresh_from_db()
        self.assertEqual(stale.status, OmniTask.Status.CANCELLED)

    def test_healed_check_task_is_retired(self):
        # A watchdog task for a danger that has since healed (its check does NOT
        # flag this run) must still be cancelled by the once-per-run sweep — else
        # it haunts the CFO popup forever.
        from core.models import OmniTask
        from django.utils import timezone
        stale = OmniTask.objects.create(
            assigner=self.cfo, assignee=self.cfo,
            title="Omni Watchdog: an old danger that has since healed", body="old",
            source="watchdog:healed_check", due_at=timezone.now(),
            status=OmniTask.Status.PENDING)
        call_command("run_watchdog", "--date", _DATE, "--no-email")
        stale.refresh_from_db()
        self.assertEqual(stale.status, OmniTask.Status.CANCELLED)

    def test_email_sent_once_per_day(self):
        call_command("run_watchdog", "--date", _DATE)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(WatchdogReportLog.objects.filter(report_date=_DATE).count(), 1)
        call_command("run_watchdog", "--date", _DATE)      # re-run same day
        self.assertEqual(len(mail.outbox), 1)              # not emailed again


class ReportIdempotencyTests(TestCase):
    def test_send_report_skips_when_already_logged(self):
        from watchdog import report as report_mod
        run = WatchdogRun.objects.create(run_date="2020-02-02", focus="Test")
        WatchdogReportLog.objects.create(report_date=run.run_date)  # pretend sent
        self.assertFalse(report_mod.send_report(run, [], force=False))
        self.assertEqual(len(mail.outbox), 0)
