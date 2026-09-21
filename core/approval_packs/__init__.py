"""Per-stream detail-pack builders. Keys match core/approvals_views.py's
`BULK_STREAM_KEYS`, so the email, the confirm page and the phone app all name an
approval the same way.

Each module exposes `build(pk) -> dict | None` built with
`core.approval_pack.make_pack`. Imports are lazy and per-stream: one app failing
to import must not take the other eight packs down with it.
"""
from __future__ import annotations

import importlib

_MODULES = {
    "commissions": "commissions",
    "payments": "payments",
    "staff_loans": "staff_loans",
    "incentives": "incentives",
    "leave_encash": "leave_encash",
    "petty_cash": "petty_cash",
    "po": "po",
    "journal_entries": "journal_entries",
    "authority_to_recruit": "authority_to_recruit",
}


def _load(name):
    def _call(pk):
        mod = importlib.import_module(f"{__name__}.{name}")
        return mod.build(pk)
    return _call


BUILDERS = {key: _load(mod) for key, mod in _MODULES.items()}
