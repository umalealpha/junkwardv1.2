"""watchdog/checks.py — the check framework and registry.

A Check is a small, READ-ONLY probe. It returns CheckResult(s): ok=True means the
invariant holds; ok=False is a finding. Checks tag themselves with a `module` (app
label, used by the rotation) and `domains` (used by watchdog.severity to decide
DANGER-flag vs safe).

Every check runs behind a guard: one broken check yields a single WARN finding
("check errored") and never kills the rest of the run — the same discipline as
core.stuck_work.sweep().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

log = logging.getLogger("watchdog")


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    title: str
    detail: str = ""
    severity: str = "warn"          # advisory when not ok; may be raised to danger
    domains: tuple = ()             # e.g. ("finance", "gl") -> DANGER via severity
    safe_to_autofix: bool = False
    autofix_note: str = ""          # what a safe auto-fix WOULD do (dry-run text)


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    module: str                     # app label; "*" runs on the Sunday sweep only
    category: str                   # integrity|business_rule|module_health|bug|machine_talk
    run: Callable[[], Iterable[CheckResult]]


REGISTRY: dict[str, Check] = {}


def register(*, key: str, label: str, module: str, category: str):
    """Decorator: register a check's run() function."""
    def _wrap(fn: Callable[[], Iterable[CheckResult]]):
        if key in REGISTRY:
            raise ValueError(f"duplicate watchdog check key: {key}")
        REGISTRY[key] = Check(key=key, label=label, module=module,
                              category=category, run=fn)
        return fn
    return _wrap


def select(weekday: int, extra_modules: Iterable[str] = ()) -> list[Check]:
    """The checks to run tonight: the always-on categories + the day's rotation
    modules + any `extra_modules` (what MACHINE-TALK says changed today, so those
    areas are tested even off their rota day). On the Sunday full sweep every
    registered check runs.
    """
    from watchdog.rotation import ALWAYS_ON_CATEGORIES, focus_for, is_full_sweep

    _, modules = focus_for(weekday)
    full = is_full_sweep(modules)
    mods = set(modules) | set(extra_modules or ())

    out: list[Check] = []
    for c in REGISTRY.values():
        if full:
            out.append(c)
        elif c.category in ALWAYS_ON_CATEGORIES or c.module in mods:
            out.append(c)
    return sorted(out, key=lambda c: (c.category, c.key))


def run_one(check: Check) -> list[CheckResult]:
    """Run a single check behind a guard."""
    try:
        results = list(check.run() or [])
    except Exception:   # noqa: BLE001 — a broken check must not kill the run
        log.exception("watchdog check %s errored", check.key)
        return [CheckResult(
            ok=False,
            title=f"{check.label}: check could not run",
            detail="The check raised an error and was skipped — investigate the "
                   "check itself, this is not a confirmed data problem.",
            severity="warn",
        )]
    return results


def run_checks(checks: Sequence[Check]):
    """Yield (check, result) for every result of every check."""
    for c in checks:
        for r in run_one(c):
            yield c, r


# Import the concrete check modules so their @register calls populate REGISTRY.
# Imported here (not at app import) and each guarded, so a missing sibling model
# in some environment can never stop the whole registry from loading.
def _load_concrete():
    for mod in ("watchdog.integrity_checks",
                "watchdog.business_rule_checks",
                "watchdog.signal_checks"):
        try:
            __import__(mod)
        except Exception:   # noqa: BLE001
            log.exception("watchdog: failed to import %s", mod)


_load_concrete()
