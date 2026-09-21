"""Pure computation helpers for Alpha Direct 'Omni' Nexus family/friend teams.

This module has zero Django or database dependencies by design. It computes
team totals and membership in one place so that views side and this side use
the single, consistent rule about who belongs to a team. It never inspects,
stores or returns commercial terms (premiums, currencies, credits etc.).
Any value that is missing or unusable is treated as absent so a team can never
be accidentally enlarged or inflated by bad data.
"""

import math
from typing import Any, Dict, List

#: Maximum number of distinct members a team may hold.
MAX_TEAM_SIZE = 6

#: Keys that are summed to produce the team totals.
_TOTAL_KEYS = ("km", "points", "trips")


def _clean_number(value: Any) -> float:
    """Return a non-negative float/int for *value*, or 0.0 if unusable."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if math.isnan(number) or math.isinf(number):
            return 0.0
        return max(0.0, number)
    # Non-numeric (str, list, dict…) — treat as absent.
    return 0.0


def _dedupe_members(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a copy of *members* with duplicate ids removed, first seen wins.

    Rows that have no usable id are dropped entirely.
    """
    seen_ids = set()
    result = []
    for row in members or []:
        raw_id = row.get("id") if isinstance(row, dict) else None
        # A blank whitespace id is the same as no id.
        if raw_id is None:
            continue
        if isinstance(raw_id, str) and not raw_id.strip():
            continue
        # Convert non-string ids (e.g. int) to strings for dedupe.
        # Keep the original value for output.
        dedupe_key = str(raw_id).strip() if isinstance(raw_id, str) else raw_id
        if dedupe_key in seen_ids:
            continue
        seen_ids.add(dedupe_key)
        result.append(row)
    return result


def _sum_member_fields(member: Dict[str, Any]) -> Dict[str, float]:
    """Return a dict of the cleaned numeric fields for one member."""
    return {
        key: _clean_number(member.get(key))
        for key in _TOTAL_KEYS
    }


def team_summary(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Produce a plain summary of a Nexus team.

    *members* is a list of plain dicts each expected to have 'id', plus the
    optional numeric keys 'km', 'points', 'trips'. Duplicate ids are counted
    once; rows without an id are ignored; bad numeric values count as zero.

    The returned dict is pure data (no money values, no premium logic) and is
    suitable for JSON serialisation.
    """
    # Normalise the input: keep only rows with a usable, unique id.
    unique_rows = _dedupe_members(members)

    total_km = 0.0
    total_points = 0
    total_trips = 0
    member_list = []

    for row in unique_rows:
        totals = _sum_member_fields(row)
        total_km += totals["km"]
        total_points += int(totals["points"])  # points are integers by spec
        total_trips += int(totals["trips"])    # trips are integers by spec

        member_list.append(
            {
                "id": row.get("id"),
                "name": row.get("name") if isinstance(row.get("name"), str) else "",
                "km": totals["km"],
                "points": int(totals["points"]),
                "trips": int(totals["trips"]),
            }
        )

    # Rank by points descending; tie-break by id for determinism.
    member_list.sort(key=lambda m: (-m["points"], str(m["id"])))

    size = len(member_list)
    full = size >= MAX_TEAM_SIZE
    spaces_left = max(0, MAX_TEAM_SIZE - size)

    return {
        "size": size,
        "full": full,
        "spacesLeft": spaces_left,
        "totalKm": total_km,
        "totalPoints": total_points,
        "totalTrips": total_trips,
        "members": member_list,
    }


def is_team_member(member_id: Any, members: List[Dict[str, Any]]) -> bool:
    """Return True if *member_id* appears in the team's distinct id list.

    A blank, whitespace or None member id is never a member. Team rows without
    an id are ignored; duplicate ids are harmless.
    """
    if member_id is None:
        return False
    if isinstance(member_id, str) and not member_id.strip():
        return False

    for row in _dedupe_members(members):
        row_id = row.get("id")
        if row_id is None:
            continue
        if isinstance(row_id, str) and not row_id.strip():
            continue
        if str(row_id) == str(member_id):
            return True
    return False