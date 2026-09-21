"""Panel member classification and normalization for subrogation recovery.

The register's "Appointed To" column holds law firms, debt collectors, third-party
insurers, and payment methods — separate concerns. This module merges spelling
variants and flags non-panel entries so recovery scorecards can be built.
"""
from __future__ import annotations

from enum import Enum
from dataclasses import dataclass


class PanelKind(Enum):
    """Classification of values in the "Appointed To" field."""
    LAW_FIRM = 'LAW_FIRM'
    DEBT_COLLECTOR = 'DEBT_COLLECTOR'
    INSURER = 'INSURER'
    PAYMENT_METHOD = 'PAYMENT_METHOD'
    NOTE = 'NOTE'
    NONE = 'NONE'
    UNKNOWN = 'UNKNOWN'


@dataclass(frozen=True)
class PanelName:
    """Canonical panel member name and classification.

    name: the canonical or title-cased name
    kind: the classification (PanelKind enum member)
    is_panel_member: True only if this is a Law Firm or Debt Collector
    """
    name: str
    kind: PanelKind

    @property
    def is_panel_member(self) -> bool:
        """Panel members are Law Firms and Debt Collectors only.

        Insurers, payment methods, notes, and unknown/blank entries are not
        panel members and should not appear on the approved panel list.
        """
        return self.kind in (PanelKind.LAW_FIRM, PanelKind.DEBT_COLLECTOR)


def _normalize_for_lookup(s: str) -> str:
    """Normalize a string for alias table lookup.

    Lowercases, converts 'and' to '&', normalizes slashes, removes trailing
    periods, and collapses multiple spaces. This allows matching despite
    spelling variants and case differences.
    """
    s = s.lower()
    s = s.replace(' and ', ' & ')
    s = s.replace('/', ' / ')
    s = s.rstrip('.')
    s = ' '.join(s.split())  # Collapse multiple spaces
    return s


# Alias table: known spelling variants mapped to (canonical_name, PanelKind).
# canonical_name is None for INSURER and PAYMENT_METHOD (use title-cased input instead).
# This table is the source of truth for merging variants into one counterparty.
ALIAS_TABLE = {
    # Law firms
    'salbany & torto': ('Salbany & Torto', PanelKind.LAW_FIRM),
    'jeremiah tladi & co': ('Jeremiah Tladi & Co', PanelKind.LAW_FIRM),
    'jereiah tladi & co': ('Jeremiah Tladi & Co', PanelKind.LAW_FIRM),  # Typo variant
    'kelobang godisang attorneys': ('Kelobang Godisang Attorneys', PanelKind.LAW_FIRM),
    'kelobang godisang attoneys': ('Kelobang Godisang Attorneys', PanelKind.LAW_FIRM),  # Typo
    'minchin & kelly': ('Minchin & Kelly', PanelKind.LAW_FIRM),
    'akheel / desai': ('Akheel / Desai', PanelKind.LAW_FIRM),
    'akheel': ('Akheel / Desai', PanelKind.LAW_FIRM),  # Abbreviation maps to full name
    'woodward legal services': ('Woodward Legal Services', PanelKind.LAW_FIRM),
    'mbikiwa legal practice': ('Mbikiwa Legal Practice', PanelKind.LAW_FIRM),
    # Debt collectors
    'collection africa': ('Collection Africa', PanelKind.DEBT_COLLECTOR),
    '5t debt collectors': ('5T Debt Collectors', PanelKind.DEBT_COLLECTOR),
    # Third-party insurers (not panel members)
    'hollard': (None, PanelKind.INSURER),
    'bryte': (None, PanelKind.INSURER),
    'old mutual': (None, PanelKind.INSURER),
    # Payment methods (not panel members, field misuse)
    'direct deposit': (None, PanelKind.PAYMENT_METHOD),
    'debit order': (None, PanelKind.PAYMENT_METHOD),
    'orange money': (None, PanelKind.PAYMENT_METHOD),
}


def normalise_panel_name(raw) -> PanelName:
    """Normalize and classify a panel member name from the register.

    Handles spelling variants, typos, payment method misuse, and free-text notes.
    Never raises on bad input — non-string input and blanks are tolerated.

    Args:
        raw: Value from the "Appointed To" column (may be None, empty, float, etc.)

    Returns:
        PanelName with canonical name and classification.

    Behavior:
    - Known law firms and debt collectors are merged (e.g., "SALBANY & TORTO"
      and "Salbany and Torto" map to the same canonical name).
    - Typos are corrected ("Attoneys" → "Attorneys").
    - Third-party insurers are flagged INSURER, not panel members.
    - Payment methods are flagged PAYMENT_METHOD, not panel members.
    - Free-text notes (sentence-like, >6 words) are flagged NOTE.
    - Unknown short names are kept but title-cased and flagged UNKNOWN.
    - Blank, None, N/A, or empty are flagged NONE.
    """

    # Non-string input is tolerated
    if not isinstance(raw, str):
        return PanelName('', PanelKind.NONE)

    # Strip whitespace
    cleaned = raw.strip()

    # Blank string
    if not cleaned:
        return PanelName('', PanelKind.NONE)

    # Explicit blank markers
    if cleaned.upper() in ('N/A', 'NONE'):
        return PanelName('', PanelKind.NONE)

    # Normalize for lookup (lowercase, standardize separators, etc.)
    lookup_key = _normalize_for_lookup(cleaned)

    # Check alias table first
    if lookup_key in ALIAS_TABLE:
        canonical_name, kind = ALIAS_TABLE[lookup_key]
        if canonical_name is None:
            # For INSURER and PAYMENT_METHOD, use title-cased input
            return PanelName(cleaned.title(), kind)
        return PanelName(canonical_name, kind)

    # Check if it looks like a free-text note (sentence-like, >6 words)
    words = lookup_key.split()
    if len(words) > 6:
        return PanelName(cleaned, PanelKind.NOTE)

    # Unknown short name — keep it, flag for review, title-case it
    return PanelName(cleaned.title(), PanelKind.UNKNOWN)
