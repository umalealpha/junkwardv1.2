"""watchdog/machine_talk.py — read the shared machine log to learn what changed
today, so the nightly run tests those areas first.

MACHINE-TALK.md ships inside the repo (top level), so the copy deployed in the
container is the log as of the running SHA. We read TODAY's lines and match module
keywords. Best-effort: a missing/unreadable file is not an error, it just means
"nothing known changed today".
"""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from django.conf import settings

log = logging.getLogger("watchdog")

# module key -> words that, in a MACHINE-TALK line, imply that module changed.
_MODULE_WORDS: dict[str, tuple[str, ...]] = {
    "ledger": ("ledger", "general ledger", "gl ", "journal", "trial balance"),
    "claims": ("claim", "claims", "salvage"),
    "payments": ("payment", "payments", "payout"),
    "commissions": ("commission", "commissions"),
    "leases": ("lease", "leases", "ifrs 16", "ifrs16"),
    "payroll": ("payroll", "payslip", "paye"),
    "hris": ("hris", "leave", "employee", "staff loan"),
    "supplier_recon": ("supplier recon", "supplier-recon", "vendor bill"),
    "procurement": ("procurement", "purchase order", " po ", "vendor"),
    "billing": ("billing", "invoice"),
    "reinsurance": ("reinsurance", "treaty", "cession", "fac "),
    "underwriting": ("underwriting", "quote", "policy"),
    "healthcare": ("healthcare", "health broker", "bonu"),
    "fnb": ("fnb", "first national"),
    "realpay": ("realpay",),
    "bank_feeds": ("bank feed", "bank statement", "bank sync"),
    "customer_refunds": ("refund", "refunds"),
    "taskboard": ("taskboard", "omnitask", "task reminder"),
    "exceptions": ("exception", "exceptions"),
    "reconciliation_hub": ("reconciliation", "recon "),
    "petty_cash": ("petty cash",),
    "assets": ("fixed asset", "asset register", "depreciation"),
}


def _machine_talk_path() -> Path:
    return Path(settings.BASE_DIR) / "MACHINE-TALK.md"


def hot_modules(today: date | None = None, *, path: Path | None = None) -> set[str]:
    """App labels that TODAY's MACHINE-TALK lines mention."""
    from django.utils import timezone
    today = today or timezone.localdate()
    p = path or _machine_talk_path()
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:   # noqa: BLE001 — no log is not a failure
        return set()

    stamp = today.strftime("%Y-%m-%d")
    todays = "\n".join(ln for ln in text.splitlines() if stamp in ln).lower()
    if not todays:
        return set()

    hot: set[str] = set()
    for module, words in _MODULE_WORDS.items():
        if any(re.search(re.escape(w), todays) for w in words):
            hot.add(module)
    return hot
