"""watchdog/models.py — the nightly Omni audit's run history + findings.

CFO 2026-08-21 ("Omni Watchdog — Nightly Audit System"): every evening Omni is
audited on a 7-day rotation; each night validates the business rules, checks the
modules still talk to each other, reads the bug board and MACHINE-TALK, then
emails a report.

DESIGN LINE (inherited from core/management/commands/triage_bugs.py, CFO-agreed):
the watchdog DETECTS, CLASSIFIES, FLAGS and FILES tasks. It never edits financial
data / journals / mappings, never touches permissions, and — by design — never
auto-pushes code to the live ERP. "Safe auto-fix" is a switched-off capability
(settings.WATCHDOG_AUTOFIX_ENABLED, default False); until it is armed every night
is report-only. Dangerous findings (finance / permissions / data / frozen numbers)
are ALWAYS report-only, whatever the switch says.
"""
from __future__ import annotations

from django.db import models

from core.models import BaseModel, OmniTask


class Severity(models.TextChoices):
    OK     = "ok",     "OK"
    INFO   = "info",   "Info"
    WARN   = "warn",   "Warning"       # a safe issue — eligible for auto-fix
    DANGER = "danger", "Danger"        # finance / permissions / data — flag only


class Category(models.TextChoices):
    INTEGRITY     = "integrity",     "Cross-module integrity"
    BUSINESS_RULE = "business_rule", "Business rule"
    MODULE_HEALTH = "module_health", "Module health"
    BUG           = "bug",           "Bug board"
    MACHINE_TALK  = "machine_talk",  "Machine-talk change"


class WatchdogRun(BaseModel):
    """One nightly (or ad-hoc) audit pass."""

    class Status(models.TextChoices):
        RUNNING  = "running",  "Running"
        COMPLETE = "complete", "Complete"
        FAILED   = "failed",   "Failed"

    run_date     = models.DateField(db_index=True)
    focus        = models.CharField(max_length=60, blank=True, default="")
    status       = models.CharField(max_length=10, choices=Status.choices,
                                    default=Status.RUNNING, db_index=True)
    dry_run      = models.BooleanField(default=True)
    started_at   = models.DateTimeField(null=True, blank=True)
    finished_at  = models.DateTimeField(null=True, blank=True)

    checks_run      = models.PositiveIntegerField(default=0)
    checks_passed   = models.PositiveIntegerField(default=0)
    findings_safe   = models.PositiveIntegerField(default=0)   # WARN
    findings_danger = models.PositiveIntegerField(default=0)   # DANGER (flagged)
    summary         = models.TextField(blank=True, default="")

    class Meta(BaseModel.Meta):
        verbose_name = "Watchdog run"

    def __str__(self):
        return f"Watchdog {self.run_date} ({self.focus or 'nightly'})"


class WatchdogFinding(BaseModel):
    """One thing a check flagged in a run."""

    run       = models.ForeignKey(WatchdogRun, on_delete=models.CASCADE,
                                  related_name="findings")
    check_key = models.CharField(max_length=60, db_index=True)
    category  = models.CharField(max_length=20, choices=Category.choices)
    module    = models.CharField(max_length=40, blank=True, default="")
    severity  = models.CharField(max_length=10, choices=Severity.choices,
                                 default=Severity.WARN, db_index=True)

    title  = models.CharField(max_length=200)
    detail = models.TextField(blank=True, default="")

    # DANGER findings are flagged=True: report-only, never auto-fixed.
    flagged         = models.BooleanField(default=False, db_index=True)
    safe_to_autofix = models.BooleanField(default=False)
    # What a safe auto-fix would do (dry-run) or did do (armed). Human-readable.
    auto_action     = models.CharField(max_length=200, blank=True, default="")

    # A rolling task raised for a flagged finding (reuses the stuck-work pattern).
    omnitask   = models.ForeignKey(OmniTask, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    resolved    = models.BooleanField(default=False, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name = "Watchdog finding"
        indexes = [models.Index(fields=["check_key", "resolved"])]

    def __str__(self):
        return f"[{self.severity}] {self.title}"


class WatchdogReportLog(BaseModel):
    """One row per day a report email was sent — makes the nightly email
    idempotent against a duplicate cron entry or a manual re-run. Same claim-row
    trick as core.StuckWorkEscalation, and for the same reason (the locmem test
    backend means OutboundEmailLog cannot be the guard).
    """
    report_date = models.DateField(db_index=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=["report_date"],
                                    name="uniq_watchdog_report_per_day"),
        ]
        verbose_name = "Watchdog report log"

    def __str__(self):
        return f"Watchdog report {self.report_date}"
