"""run_watchdog — the nightly Omni audit (CFO 2026-08-21, 20:00 CAT).

Runs the day's rotation + the always-on checks (business rules, cross-module
integrity, bug board, MACHINE-TALK), records a WatchdogRun + WatchdogFindings,
raises ONE rolling Exception per DANGER finding (on the Exceptions page, NOT the
CFO's task list — CFO 2026-08-24), and emails the CFO a report.

It is REPORT-ONLY. It never edits data, never touches permissions, and never
deploys code. "Safe auto-fix" is gated behind settings.WATCHDOG_AUTOFIX_ENABLED
(default off) and no auto-fixers are registered yet, so today it is pure
detect-and-report. Dangerous findings are ALWAYS report-only.

  python manage.py run_watchdog                 # tonight, emails once
  python manage.py run_watchdog --no-email      # run + record, no email
  python manage.py run_watchdog --date 2026-08-24 --force-email
"""
from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from watchdog import checks as check_mod
from watchdog import report as report_mod
from watchdog.machine_talk import hot_modules
from watchdog.models import Severity, WatchdogFinding, WatchdogRun
from watchdog.rotation import focus_for
from watchdog.severity import classify

_INFO = "info"


def _cfo_user():
    return (User.objects.filter(email__iexact="pganesharajah@alphadirect.co.bw").first()
            or User.objects.filter(is_superuser=True).order_by("date_joined").first())


def _raise_danger_exception(finding, cfo, today):
    """One rolling Exception per danger check, on the Exceptions page — NOT a task
    on the CFO's personal list (CFO 2026-08-24: "I don't want Omni Watchdog matters
    in my task, create this in the Exceptions page"). A watchdog finding is a system
    anomaly for finance to clear, which is exactly what the Exceptions queue is for.

    create_exception dedupes on (source_app, source_model, source_id, type) while
    the row is OPEN/ACKNOWLEDGED/IN_PROGRESS, so a repeat finding reuses one row
    instead of piling up. Always raised — flagging a danger is the whole point of
    the audit; it is NOT gated by dry_run (that only governs safe auto-fixes)."""
    from exceptions.models import Exception as ExceptionModel
    from exceptions.services import create_exception

    return create_exception(
        exception_type=ExceptionModel.Type.OTHER,
        title=f"Omni Watchdog: {finding.title}",
        description=f"{finding.detail}\n\nRaised by the nightly Omni Watchdog audit.",
        severity=ExceptionModel.Severity.HIGH,
        requires_role=ExceptionModel.RequiresRole.FINANCE_MANAGER,
        source_app="watchdog",
        source_model="WatchdogFinding",
        source_id=finding.check_key,
        source_label=finding.check_key,
        metadata={"check_key": finding.check_key, "category": finding.category,
                  "module": finding.module, "run_date": str(today)},
        created_by=cfo,
    )


def _retire_watchdog_tasks():
    """Cancel EVERY open OmniTask an earlier build raised for the watchdog (source
    'watchdog:*'), once per run. Doing it per-finding would only clear checks that
    flagged tonight and leave a task for a danger that has since healed sitting in
    the CFO's popup forever — the exact thing he asked to remove. Only ever touches
    watchdog-sourced tasks. Returns the number retired."""
    from core.models import OmniTask
    return (OmniTask.objects
            .filter(source__startswith="watchdog:",
                    status__in=(OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS))
            .update(status=OmniTask.Status.CANCELLED,
                    completed_at=timezone.now(), updated_at=timezone.now()))


class Command(BaseCommand):
    help = "Run the nightly Omni Watchdog audit and email the report."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: today)")
        parser.add_argument("--focus", help="Override the rotation label")
        parser.add_argument("--no-email", action="store_true")
        parser.add_argument("--force-email", action="store_true",
                            help="Send even if a report was already sent today")
        parser.add_argument("--apply-safe-fixes", action="store_true",
                            help="Apply safe auto-fixes (requires WATCHDOG_AUTOFIX_ENABLED)")

    def handle(self, *args, **opts):
        if opts.get("date"):
            run_date = datetime.strptime(opts["date"], "%Y-%m-%d").date()
        else:
            run_date = timezone.localdate()

        label, _ = focus_for(run_date.weekday())
        focus = opts.get("focus") or label
        hot = hot_modules(run_date)
        selected = check_mod.select(run_date.weekday(), hot)

        autofix_on = bool(opts.get("apply_safe_fixes")) and \
            getattr(settings, "WATCHDOG_AUTOFIX_ENABLED", False)
        dry_run = not autofix_on

        run = WatchdogRun.objects.create(
            run_date=run_date, focus=focus, dry_run=dry_run,
            status=WatchdogRun.Status.RUNNING, started_at=timezone.now())

        cfo = _cfo_user()
        ran = passed = safe = danger = 0

        for check, res in check_mod.run_checks(selected):
            ran += 1
            if res.ok:
                passed += 1
                continue

            if res.severity == _INFO:
                WatchdogFinding.objects.create(
                    run=run, check_key=check.key, category=check.category,
                    module=check.module, severity=Severity.INFO,
                    title=res.title, detail=res.detail)
                continue

            flagged, reason = classify(res.domains)
            flagged = flagged or res.severity == "danger"
            severity = Severity.DANGER if flagged else Severity.WARN
            can_fix = bool(res.safe_to_autofix) and not flagged

            if flagged:
                auto_action = "flagged — needs a human" + (f" ({reason})" if reason else "")
            elif can_fix:
                note = res.autofix_note or "auto-fixable"
                auto_action = ("would " + note) if dry_run else ("auto-fixed: " + note)
            else:
                auto_action = ""

            finding = WatchdogFinding.objects.create(
                run=run, check_key=check.key, category=check.category,
                module=check.module, severity=severity, title=res.title,
                detail=res.detail, flagged=flagged, safe_to_autofix=can_fix,
                auto_action=auto_action[:200])

            if flagged:
                danger += 1
                _raise_danger_exception(finding, cfo, run_date)
            else:
                safe += 1

        # Watchdog findings live on the Exceptions page now — retire ALL old
        # watchdog tasks (incl. checks that have since healed) so none linger in
        # the CFO's task popup (CFO 2026-08-24).
        _retire_watchdog_tasks()

        run.checks_run = ran
        run.checks_passed = passed
        run.findings_safe = safe
        run.findings_danger = danger
        run.status = WatchdogRun.Status.COMPLETE
        run.finished_at = timezone.now()
        run.summary = (f"{danger} flagged, {safe} safe, {passed}/{ran} checks passed "
                       f"— focus {focus}")
        run.save()

        sent = False
        if not opts.get("no_email"):
            findings = list(run.findings.all())
            sent = report_mod.send_report(run, findings,
                                          force=bool(opts.get("force_email")))

        self.stdout.write(self.style.SUCCESS(
            f"Watchdog {run_date} ({focus}): {run.summary}. "
            f"email={'sent' if sent else 'skipped'} dry_run={dry_run} "
            f"hot={sorted(hot) or '—'}"))
