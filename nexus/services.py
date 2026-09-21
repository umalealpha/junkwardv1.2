"""Nexus services — ingest a trip, score it, accrue rewards with an audit trail.

This is the seam the customer app + any telematics feed call. record_trip() is
the single entry point: it scores the trip (P1), writes the trip, accrues reward
points and posts an audit-ledger entry (P2), and rolls up the driver total — all
in one transaction.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from . import scoring
from .models import NexusDriver, NexusRewardLedger, NexusTrip


@transaction.atomic
def record_trip(driver: NexusDriver, *, started_at, distance_km, duration_minutes,
                idle_minutes=0, harsh_brakes=0, speeding_events=0, label="",
                score=None) -> NexusTrip:
    # `score` may be supplied directly (e.g. the WebFleet OptiDrive driving-quality
    # indicator ×100 — the real telematics score); else compute it from the metrics.
    if score is None:
        score = scoring.compute_trip_score(
            distance_km=distance_km, idle_minutes=idle_minutes,
            duration_minutes=duration_minutes, harsh_brakes=harsh_brakes,
            speeding_events=speeding_events,
        )
    score = int(max(0, min(100, round(score))))
    points = scoring.compute_points(score, distance_km)

    trip = NexusTrip.objects.create(
        driver=driver, started_at=started_at, label=label,
        distance_km=Decimal(str(distance_km)), duration_minutes=int(duration_minutes),
        idle_minutes=int(idle_minutes), harsh_brakes=int(harsh_brakes),
        speeding_events=int(speeding_events), score=score, points=points,
    )

    # accrue + audit (P2) — plain-English ledger lines (no jargon)
    when = label or "trip"
    if points > 0:
        NexusRewardLedger.objects.create(
            driver=driver, trip=trip, points=points,
            description=f"{when} — earned {points} points ({scoring.grade_label(score).lower()} drive)",
        )
    elif speeding_events or harsh_brakes:
        # a small visible deduction for unsafe events, fully audited
        deduction = -(int(harsh_brakes) * 5 + int(speeding_events) * 10)
        if deduction:
            why = "speeding" if speeding_events else "hard braking"
            NexusRewardLedger.objects.create(
                driver=driver, trip=trip, points=deduction,
                description=f"{when} — {deduction} points ({why})",
            )

    # roll up driver total from the ledger (source of truth = the audit trail)
    total = sum(e.points for e in driver.ledger.all())
    driver.total_points = total
    driver.save(update_fields=["total_points"])
    return trip


def driver_summary(driver: NexusDriver) -> dict:
    """Everything the customer app needs, computed from real data."""
    trips = list(driver.trips.all()[:30])
    total_km = sum(float(t.distance_km) for t in trips)
    total_dur = sum(t.duration_minutes for t in trips)
    total_idle = sum(t.idle_minutes for t in trips)
    scored = [t.score for t in trips]
    avg_score = round(sum(scored) / len(scored)) if scored else 0
    idle_pct = round((total_idle / total_dur) * 100) if total_dur else 0
    nxt, to_next = scoring.next_tier(driver.total_points)
    return {
        "driver": {"id": str(driver.id), "name": driver.full_name},
        "score": avg_score,
        "km_total": round(total_km, 1),
        "trips_count": len(trips),
        "idle_pct": idle_pct,
        "points": driver.total_points,
        "tier": scoring.tier_for(driver.total_points),
        "next_tier": nxt,
        "points_to_next": to_next,
        "recent_trips": [
            {"label": t.label, "distance_km": float(t.distance_km),
             "duration_minutes": t.duration_minutes, "score": t.score,
             "points": t.points,
             "grade": scoring.grade_label(t.score),
             "feedback": scoring.trip_feedback(
                 t.score, speeding_events=t.speeding_events, harsh_brakes=t.harsh_brakes,
                 idle_minutes=t.idle_minutes, duration_minutes=t.duration_minutes),
             "harsh_brakes": t.harsh_brakes, "speeding_events": t.speeding_events,
             "idle_minutes": t.idle_minutes}
            for t in trips[:6]
        ],
        "ledger": [
            {"description": e.description, "points": e.points,
             "created_at": e.created_at.isoformat()}
            for e in driver.ledger.all()[:8]
        ],
    }
