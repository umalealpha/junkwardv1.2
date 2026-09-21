"""watchdog/signal_checks.py — the two always-on "read" checks: the bug board and
MACHINE-TALK. Both surface state as INFO/WARN context in the nightly report; the
actual bug triage + fixing stays with the hourly triage runner and a human.
"""
from __future__ import annotations

from watchdog.checks import CheckResult, register


@register(key="bug_board", label="Bug board", module="", category="bug")
def bug_board():
    """Surface the bug board: new (awaiting the hourly triage) + those triage
    flagged for a human (data/financial, i.e. cannot be auto-actioned)."""
    from core.models import BugReport

    new = BugReport.objects.filter(status="new").count()
    needs = (BugReport.objects
             .filter(triage_requested=True)
             .exclude(status__in=["resolved", "wont_fix"]).count())

    out = []
    if needs:
        out.append(CheckResult(
            ok=False, severity="warn",
            title=f"{needs} bug report(s) need a human decision",
            detail="Triage flagged these as data/financial or out-of-policy — not "
                   "auto-actionable.",
            autofix_note=""))
    if new:
        out.append(CheckResult(
            ok=False, severity="info",
            title=f"{new} new bug report(s) awaiting triage",
            detail="The hourly triage runner will classify these; listed for visibility."))
    if not out:
        out.append(CheckResult(ok=True, title="Bug board clear"))
    return out


@register(key="machine_talk", label="Today's changes (MACHINE-TALK)",
          module="", category="machine_talk")
def machine_talk():
    """What changed today, so those modules were tested first tonight."""
    from watchdog.machine_talk import hot_modules

    hot = hot_modules()
    if not hot:
        return [CheckResult(ok=True, title="No module changes noted in MACHINE-TALK today")]
    return [CheckResult(
        ok=False, severity="info",
        title=f"{len(hot)} module(s) changed today — tested first",
        detail="MACHINE-TALK mentions: " + ", ".join(sorted(hot)))]
