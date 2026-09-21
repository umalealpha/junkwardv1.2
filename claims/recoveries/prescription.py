"""Prescription risk tracking for subrogation recovery claims.

The Botswana Prescription Act sets the ordinary period for a contractual or
delictual debt at THREE YEARS. The clock here runs from the date of loss where
we have one, otherwise from the date appointed — never from nothing.

This module only computes and warns. It never decides to abandon a claim: an
expired case stays visible so the decision is a person's, on the record.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from claims.recoveries.dates import parse_register_date


PRESCRIPTION_YEARS = 3


class PrescriptionRisk(Enum):
    """Risk level of a case approaching or past prescription limit.

    Each level indicates how urgently the case needs attention before the
    Botswana Prescription Act's 3-year ordinary period expires.

    SAFE: 366+ days remaining before prescription expires. No immediate action.
    WATCH: 91-365 days remaining. Mark for periodic review.
    URGENT: 30-90 days remaining. Escalate to collections team.
    CRITICAL: 0-29 days remaining (including expired today). Stop-work alert.
    EXPIRED: prescription period has already elapsed (negative days). Case is
             aged but still visible for decision-making, never auto-deleted.
    UNKNOWN: no date_of_loss or date_appointed provided. Highest control risk:
             542 open cases worth P21.8m have no date. Treat as critical.
    """
    SAFE = "safe"
    WATCH = "watch"
    URGENT = "urgent"
    CRITICAL = "critical"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Prescription:
    """Immutable snapshot of a claim's prescription status at a point in time.

    All fields are read-only (frozen=True). This ensures the snapshot remains
    a point-in-time fact and cannot be accidentally mutated.

    Attributes:
        expires_on: The date prescription expires (3 years from clock start).
                    None if no date_of_loss or date_appointed is available.
        days_remaining: Days until prescription expires, as of as_at.
                        Negative if prescription has already passed.
                        None if expires_on is None (unknown case).
        expired: True if the prescription period has elapsed (days_remaining < 0).
        risk: PrescriptionRisk enum indicating which action level applies.
        basis: Which date was used to start the clock: 'date_of_loss' or
               'date_appointed'. None if neither date was available.
    """
    expires_on: date | None
    days_remaining: int | None
    expired: bool
    risk: PrescriptionRisk
    basis: str | None

    @property
    def needs_attention(self) -> bool:
        """True for everything except SAFE risk level.

        A CFO reading the register should see this flag for every case that
        requires action: expired, approaching expiry, or unclockedcases with
        no date. Only fresh cases with 1+ years left (SAFE) are operationally
        quiet.

        Returns:
            bool: True if risk is not SAFE, False if SAFE.
        """
        return self.risk is not PrescriptionRisk.SAFE


# Shared with ageing.py. This module previously had its own narrower parser,
# which returned datetimes unchanged (crashing the subtraction below) and
# rejected the Excel serials and "YYYY-MM-DD HH:MM:SS" strings that ageing
# accepted for the SAME column. See dates.py.
_parse_date = parse_register_date


def _add_years_safe(start_date: date, years: int) -> date:
    """Add years to a date, handling 29 February in non-leap target years.

    The Botswana Prescription Act does not special-case leap days. Adding
    3 years to 2024-02-29 should land on 2027-02-28 (not 03-01), because
    2027 is not a leap year. This preserves the *intent* of "3 years from
    today" across all months.

    Args:
        start_date: The date to add years to.
        years: Number of years to add (typically 3 for prescription).

    Returns:
        The resulting date, with day falling back to 28 if Feb 29 is the
        start date and the target year is not a leap year.
    """
    try:
        # Straightforward case: target year has the same day (e.g., Jun 1 -> Jun 1)
        return start_date.replace(year=start_date.year + years)
    except ValueError:
        # Feb 29 in a non-leap year; fall back to Feb 28 of target year
        return start_date.replace(year=start_date.year + years, day=28)


def prescription_status(
    date_of_loss: date | str | None,
    date_appointed: date | str | None,
    as_at: date,
) -> Prescription:
    """Compute prescription status for a recovery claim.

    The Botswana Prescription Act (ordinary period) sets a 3-year clock on
    debts. For a claim, the clock starts from date_of_loss (when the debt
    arose / property was damaged) if available. If not, it starts from
    date_appointed (when we took action to recover). If neither is present,
    the case is marked UNKNOWN and flagged for urgent attention (542 open
    cases worth P21.8m fall into this category).

    Args:
        date_of_loss: When the property damage occurred (if known).
                      Accepts date objects or ISO strings ('YYYY-MM-DD').
        date_appointed: When we appointed someone to recover (fallback).
                        Accepts date objects or ISO strings ('YYYY-MM-DD').
        as_at: The reference date to evaluate prescription at (typically today).
               Accepts date objects only.

    Returns:
        A Prescription object with expires_on, days_remaining, expired status,
        risk level, and the basis for the calculation.
    """
    # Parse both dates, gracefully
    loss_date = _parse_date(date_of_loss)
    appointed_date = _parse_date(date_appointed)

    # Determine the date to run the clock from
    if loss_date is not None:
        start_date = loss_date
        basis = 'date_of_loss'
    elif appointed_date is not None:
        start_date = appointed_date
        basis = 'date_appointed'
    else:
        # No date at all: highest control risk. Must be surfaced to CFO.
        return Prescription(
            expires_on=None,
            days_remaining=None,
            expired=False,
            risk=PrescriptionRisk.UNKNOWN,
            basis=None,
        )

    # Calculate expiry date: 3 years from clock start
    expires_on = _add_years_safe(start_date, PRESCRIPTION_YEARS)

    # Calculate days remaining as of as_at
    days_remaining = (expires_on - as_at).days

    # Determine if already expired
    expired = days_remaining < 0

    # Map days_remaining to risk level using the ladder
    if days_remaining < 0:
        risk = PrescriptionRisk.EXPIRED
    elif days_remaining <= 29:
        risk = PrescriptionRisk.CRITICAL
    elif days_remaining <= 90:
        risk = PrescriptionRisk.URGENT
    elif days_remaining <= 365:
        risk = PrescriptionRisk.WATCH
    else:
        # days_remaining >= 366
        risk = PrescriptionRisk.SAFE

    return Prescription(
        expires_on=expires_on,
        days_remaining=days_remaining,
        expired=expired,
        risk=risk,
        basis=basis,
    )
