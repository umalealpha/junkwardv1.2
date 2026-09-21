"""The one list of departments (CFO approved 18-Sep-2026).

Onboarding accepts only these names. The same list will back the single
Employee.department field (register row L-DEPT), so every screen agrees.

LEGACY_ALIASES records the spellings found live on 18-Sep and the canonical
name each folds into. It is used for REPORTING and the later backfill only —
onboarding does not accept an alias, because an alias typed today is exactly
the drift this list exists to stop.
"""
from __future__ import annotations

DEPARTMENTS: tuple[str, ...] = (
    'Finance & Planning',
    'Claims',
    'Underwriting',
    'Software Development',
    'Admin & IT',
    'Business Development',
    'Sales & Marketing',
    'Operations',
    'Compliance',
    'Human Capital',
    'Health Insurance',
    'UniCoin',
    'Veritas',
    'Executive',
    'Special Projects',
)

LEGACY_ALIASES: dict[str, str] = {
    'finance': 'Finance & Planning',
    'claims — parts ordering': 'Claims',
    'system and software development': 'Software Development',
    'administration': 'Admin & IT',
    'information technology': 'Admin & IT',
    'it': 'Admin & IT',
    'sales & operations': 'Sales & Marketing',
    'hr': 'Human Capital',
    'health care': 'Health Insurance',
    'uni coin': 'UniCoin',
    'unicoin': 'UniCoin',
    'c-suite': 'Executive',
    'exco': 'Executive',
    'senior management': 'Executive',
    'quantum': 'Special Projects',
    'data analytics': 'Special Projects',
}

_BY_FOLD = {name.casefold(): name for name in DEPARTMENTS}


def canonical_department(value) -> str | None:
    """Exact (case-insensitive) match to the approved list, else None.

    Positive match only — never a substring, never a default (checklist L6:
    a control keyed on something with a fallback answer satisfies itself).
    """
    return _BY_FOLD.get(str(value or '').strip().casefold())


def fold_legacy(value) -> str | None:
    """Canonical name for a legacy spelling, else None. Reporting/backfill only."""
    text = str(value or '').strip()
    return canonical_department(text) or LEGACY_ALIASES.get(text.casefold())
