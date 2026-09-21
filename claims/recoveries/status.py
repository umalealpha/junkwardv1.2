from __future__ import annotations

from enum import Enum


class RecoveryStatus(Enum):
    """Picklist of recovery statuses for subrogation claims.

    Each value represents a distinct stage in the recovery lifecycle:
    - IN_PROGRESS: Active recovery efforts underway
    - SETTLED: Money has been recovered and received
    - AWAITING_RELEASE: Awaiting form of release from the third party
    - WRITTEN_OFF: Recovery has been written off as uncollectible
    - CLOSED: The recovery case is closed (but no money received)
    - UNKNOWN: Status is missing or unrecognizable
    """
    IN_PROGRESS = 'in_progress'
    SETTLED = 'settled'
    AWAITING_RELEASE = 'awaiting_release'
    WRITTEN_OFF = 'written_off'
    CLOSED = 'closed'
    UNKNOWN = 'unknown'


def normalise_status(raw) -> RecoveryStatus:
    """Convert a raw recovery status value to a canonical RecoveryStatus enum member.

    The function normalises case, whitespace, and common variations found in the
    live register. It never raises an exception — if the input is None, empty,
    or unrecognizable, it returns UNKNOWN instead.

    A hybrid status like "settled Closed" or "Settled_Closed" is recognized as
    SETTLED (meaning money came in) rather than a bare CLOSED, because the presence
    of "settled" indicates successful recovery. To ensure this precedence, the
    alias table checks for "settled" hybrids before checking for a bare "closed".

    Args:
        raw: A raw value from the recovery register. May be None, a string of any case,
             whitespace, or punctuation, or other non-string types.

    Returns:
        A RecoveryStatus enum member, never None. Always one of the six allowed values.
    """

    # Handle None
    if raw is None:
        return RecoveryStatus.UNKNOWN

    # Convert to string and normalize: strip whitespace and convert to lowercase
    if not isinstance(raw, str):
        raw_str = str(raw).strip().lower()
    else:
        raw_str = raw.strip().lower()

    # Handle empty strings after stripping
    if not raw_str:
        return RecoveryStatus.UNKNOWN

    # Alias table: maps normalized patterns to RecoveryStatus.
    # Order matters: longer/more specific patterns must be checked before shorter ones.
    # Critical: "settled closed" and "settled_closed" must be checked before plain "closed"
    # so that hybrid statuses are correctly identified as SETTLED, not CLOSED.
    ALIAS_TABLE = [
        # IN_PROGRESS variants (including when a lawyer is involved)
        (['in progress', 'lawyer'], RecoveryStatus.IN_PROGRESS),

        # SETTLED variants, including hybrids with "closed"
        # This must come before CLOSED to capture "settled closed" and "settled_closed"
        (['settled', 'settled closed', 'settled_closed'], RecoveryStatus.SETTLED),

        # AWAITING_RELEASE
        (['awaiting form of release'], RecoveryStatus.AWAITING_RELEASE),

        # WRITTEN_OFF (multiple valid spellings and punctuation)
        (['bad debt_subrogation write off', 'written off', 'write off', 'write-off'], RecoveryStatus.WRITTEN_OFF),

        # CLOSED (only bare "closed", not the "settled closed" hybrids)
        (['closed'], RecoveryStatus.CLOSED),
    ]

    # Check each alias group
    for patterns, status in ALIAS_TABLE:
        if raw_str in patterns:
            return status

    # No match found
    return RecoveryStatus.UNKNOWN
