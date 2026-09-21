"""Nexus driving-points engine (P1) — pure, testable, no Django imports.

Computes a 0-100 driving score per trip from telematics inputs (distance, idle,
events) using configurable weights, then converts a score into reward points.
This is the core the customer app + Rewards Engine (P2) build on.
"""
from __future__ import annotations

# Default scoring weights (CFO-tunable later via config). Each event/condition
# deducts from a perfect 100.
DEFAULT_WEIGHTS = {
    "harsh_brake": 6,        # per harsh-braking event
    "speeding": 9,           # per speeding event
    "idle_ratio_penalty": 25,  # max penalty for fully-idle trip
    "idle_free_grace": 0.10,   # idle up to 10% of trip time is free
}

TIERS = [
    (0,    "Bronze"),
    (300,  "Silver"),
    (600,  "Gold"),
    (1000, "Platinum"),
]


def compute_trip_score(distance_km, idle_minutes, duration_minutes,
                       harsh_brakes=0, speeding_events=0, weights=None) -> int:
    """Return a 0-100 driving score for one trip. Higher = safer/smoother."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    distance_km = float(distance_km or 0)
    duration_minutes = float(duration_minutes or 0)
    idle_minutes = float(idle_minutes or 0)

    score = 100.0
    score -= int(harsh_brakes or 0) * w["harsh_brake"]
    score -= int(speeding_events or 0) * w["speeding"]

    # idle penalty: only the idle fraction ABOVE the grace allowance is charged
    if duration_minutes > 0:
        idle_ratio = min(max(idle_minutes / duration_minutes, 0.0), 1.0)
        charged = max(idle_ratio - w["idle_free_grace"], 0.0)
        # scale charged (0..1-grace) up to the full penalty
        denom = max(1.0 - w["idle_free_grace"], 1e-6)
        score -= (charged / denom) * w["idle_ratio_penalty"]

    return int(max(0, min(100, round(score))))


def compute_points(score: int, distance_km) -> int:
    """Reward points for a trip. Only good driving earns; distance scales it.
    A score below 50 earns nothing; 50-100 scales 0->1.5 points per km."""
    distance_km = float(distance_km or 0)
    if score < 50:
        return 0
    rate = (score - 50) / 50.0 * 1.5      # 0 at score 50, 1.5/km at 100
    return int(round(distance_km * rate))


def tier_for(total_points: int) -> str:
    name = TIERS[0][1]
    for threshold, label in TIERS:
        if total_points >= threshold:
            name = label
    return name


def next_tier(total_points: int):
    """Return (next_tier_name, points_to_reach) or (None, 0) if at top."""
    for threshold, label in TIERS:
        if total_points < threshold:
            return label, threshold - total_points
    return None, 0


def grade_label(score: int) -> str:
    """Plain-English grade for one trip — what a normal driver reads, not a
    raw metric. (Bands modelled on UBI/eco-driving apps, Charmaine 2026-06-25.)"""
    s = int(score or 0)
    if s >= 90:
        return "Excellent"
    if s >= 80:
        return "Good"
    if s >= 70:
        return "Fair"
    if s >= 50:
        return "Needs care"
    return "Risky"


def trip_feedback(score: int, *, speeding_events=0, harsh_brakes=0,
                  idle_minutes=0, duration_minutes=0) -> str:
    """One plain sentence a normal person understands — no jargon, no metrics."""
    if int(speeding_events or 0) > 0:
        return "Watch your speed — you went over the limit."
    if int(harsh_brakes or 0) > 0:
        return "Ease off the brakes — a few hard stops."
    dur = int(duration_minutes or 0)
    if dur and int(idle_minutes or 0) > 0 and (idle_minutes / dur) > 0.35:
        return "A lot of idling — try to switch off when parked."
    if int(score or 0) >= 90:
        return "Smooth, safe drive. Nice one."
    if int(score or 0) >= 80:
        return "Good drive overall."
    return "An okay drive — room to improve."
