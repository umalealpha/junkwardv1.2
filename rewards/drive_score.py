"""rewards/drive_score.py — Click & Drive trip scoring.

Pure function. The customer app tracks a trip on the phone and sends only
AGGREGATE numbers (no GPS track). The server scores the trip here so the client
cannot fake a perfect score. Transparent + additive, like alpha_score.py.

Score starts at 100 and loses points for harsh braking/acceleration events,
time spent idling, and excessive top speed — normalised by trip length so a
long safe trip isn't punished. Wellness/safety only, not a legal record.
"""
from __future__ import annotations

HARSH_PENALTY = 6.0       # per harsh event per 10 km
IDLE_PENALTY_MAX = 18.0   # max points lost to idling
SPEED_FREE_KMH = 120.0    # over this starts costing
SPEED_PENALTY_MAX = 22.0
# Fairness for short trips (tester finding 2026-07: "worst rates on the
# shortest trips"). Two fixes:
#  - HARSH_RATE_FLOOR_KM: the distance used to turn harsh events into a
#    per-10km RATE is floored, so ONE event on a 1-2 km hop is not amplified
#    into a huge penalty the way dividing by a tiny distance did.
#  - NEUTRAL_SCORE + FULL_CONFIDENCE_KM: a short trip carries little signal, so
#    the raw score is blended toward a neutral baseline; a 1 km trip barely
#    moves from neutral, a 10 km+ trip is graded in full.
HARSH_RATE_FLOOR_KM = 5.0
NEUTRAL_SCORE = 80.0
FULL_CONFIDENCE_KM = 10.0


# --- Data quality: a trip must be physically possible ---------------------
# A phone's GPS spikes when it first locks on: it can report a high speed and
# a couple of "harsh events" while the car has not moved at all. Those readings
# are not driving and must never reach the profile — one 12-second, 0 km row
# once branded a member DANGEROUS off a 132 km/h reading (CFO, 2026-09-08).
# This is the ONE parser: the ingest gate, the stored flags and every read path
# all go through trip_quality() so a trip cannot be judged one way on the way
# in and another way on the way out.
MIN_TRIP_KM = 0.3          # below this there is no drive to grade
MIN_TRIP_MIN = 1.0         # below this there is no drive to grade
PEAK_SUSTAIN_SEC = 10.0    # a genuine peak is held for at least this long
ABSURD_KMH = 250.0         # no insured car on a Botswana road reaches this
MIN_RATE_KM = 1.0          # rate denominator floor — never print a count as a rate
MIN_HARSH_FOR_RATE = 3     # fewer events than this is noise, not a habit
MIN_KM_FOR_RATE = 5.0      # ...and it needs real distance behind it too

REASON_TEXT = {
    '': '',
    # Say what is TRUE of the row: 0.18 km is not "never moved".
    'no_distance': 'Under 300 m covered — too little ground to grade a drive.',
    'too_short': 'Under 1 minute — too short to grade a drive.',
}


def trip_quality(*, distance_km, duration_min, max_speed) -> dict:
    """Judge whether a recorded trip is physically possible.

    Returns {valid, reason, speed_reliable, max_speed}:
      - valid=False        → no drive happened; exclude the row from every
                             aggregate (it is kept for audit, never graded).
      - speed_reliable=False → the trip is real but its peak-speed reading is
                             not supported by the ground actually covered, so
                             the speed is UNKNOWN: 0.0 is returned and no speed
                             penalty or speeding flag may be raised from it.
    """
    dist = max(0.0, float(distance_km or 0))
    dur = max(0.0, float(duration_min or 0))
    top = max(0.0, float(max_speed or 0))

    if dist < MIN_TRIP_KM:
        return {'valid': False, 'reason': 'no_distance', 'speed_reliable': False, 'max_speed': 0.0}
    if dur < MIN_TRIP_MIN:
        return {'valid': False, 'reason': 'too_short', 'speed_reliable': False, 'max_speed': 0.0}

    # Holding `top` for PEAK_SUSTAIN_SEC covers this much ground. A peak the
    # trip's own distance cannot account for is a GPS spike, not a speed.
    needed_km = top * (PEAK_SUSTAIN_SEC / 3600.0)
    reliable = top <= ABSURD_KMH and dist >= needed_km
    return {'valid': True, 'reason': '', 'speed_reliable': reliable,
            'max_speed': top if reliable else 0.0}


def apply_quality(trip) -> bool:
    """Stamp trip_quality()'s verdict onto a CustomerDriveTrip row.

    Duck-typed so the data migration (which holds a HISTORICAL model) and the
    `reflag_drive_trips` command share ONE implementation — the thing that
    actually fixes the stored records must not exist twice.
    Returns True when the row changed (nothing is written otherwise).
    🔴 Changing MIN_TRIP_KM / MIN_TRIP_MIN / PEAK_SUSTAIN_SEC / ABSURD_KMH means
    the stored flags are stale: re-run `manage.py reflag_drive_trips`.
    """
    q = trip_quality(distance_km=trip.distance_km, duration_min=trip.duration_min,
                     max_speed=trip.max_speed)
    if (trip.is_valid, trip.invalid_reason, trip.speed_reliable) == (
            q['valid'], q['reason'], q['speed_reliable']):
        return False
    trip.is_valid = q['valid']
    trip.invalid_reason = q['reason']
    trip.speed_reliable = q['speed_reliable']
    trip.save(update_fields=['is_valid', 'invalid_reason', 'speed_reliable'])
    return True


def _clamp(v: float, lo=0.0, hi=100.0) -> float:
    return max(lo, min(hi, v))


def trip_score(*, distance_km, duration_min, idle_minutes, harsh_events, max_speed) -> dict:
    """Return {score 0-100, band, factors[], points}."""
    dist = max(0.0, float(distance_km or 0))
    dur = max(0.0, float(duration_min or 0))
    idle = max(0.0, float(idle_minutes or 0))
    harsh = max(0, int(harsh_events or 0))
    top = max(0.0, float(max_speed or 0))

    # Harsh events as a per-10km RATE, but the distance is floored so a single
    # event on a short hop isn't amplified into a max penalty.
    denom_km = max(dist, HARSH_RATE_FLOOR_KM)
    per10 = harsh / (denom_km / 10.0)
    harsh_pen = min(45.0, per10 * HARSH_PENALTY)

    idle_ratio = min(1.0, idle / dur) if dur > 0 else 0.0
    idle_pen = idle_ratio * IDLE_PENALTY_MAX

    speed_pen = min(SPEED_PENALTY_MAX, max(0.0, top - SPEED_FREE_KMH) * 0.6)

    raw = _clamp(100.0 - harsh_pen - idle_pen - speed_pen)
    # Confidence blend: short trips regress toward neutral so one glitchy event
    # on a 1 km trip can't brand someone a bad driver; long trips graded in full.
    conf = max(0.2, min(1.0, dist / FULL_CONFIDENCE_KM))
    provisional = dist < FULL_CONFIDENCE_KM
    score = int(round(NEUTRAL_SCORE * (1.0 - conf) + raw * conf))
    band = ('Excellent' if score >= 85 else 'Good' if score >= 70
            else 'Fair' if score >= 50 else 'Needs work')

    # Provisional points: safe trips earn a little per km, scaled by score.
    # (Final economics are a CFO sign-off, like the tier discount.)
    points = int(round(min(20.0, dist * 0.6) * (score / 100.0))) if score >= 50 else 0

    factors = [
        {'key': 'smoothness', 'label': 'Smooth driving', 'note': f'{harsh} harsh event(s)', 'penalty': round(harsh_pen, 1)},
        {'key': 'idle',       'label': 'Idling',         'note': f'{idle:.0f} min idling',   'penalty': round(idle_pen, 1)},
        {'key': 'speed',      'label': 'Speed',
         # top == 0 here means the caller handed us an UNBELIEVABLE reading, not
         # a stationary car — never print it as "max 0 km/h".
         'note': (f'max {top:.0f} km/h' if top > 0 else 'peak speed not reliable — reading discarded'),
         'penalty': round(speed_pen, 1)},
    ]
    if provisional:
        factors.append({'key': 'confidence', 'label': 'Short trip',
                        'note': f'{dist:.1f} km — provisional score, drive further for a full grade',
                        'penalty': 0.0})
    return {'score': score, 'band': band, 'factors': factors, 'points': points, 'provisional': provisional}


# --- Driving profile (blunt, no sugar-coating) ----------------------------
# Each trip dict: score, distance_km, duration_min, idle_minutes, harsh_events, max_speed.

# {habits} is filled from what the numbers ACTUALLY show — a member with no
# speeding flag must never be told speeding is "routine" for them (CFO,
# 2026-09-08: "put guard rails not to report wrong information").
_VERDICTS = {
    'Dangerous':    'Bluntly: your driving is dangerous. {habits} — high crash risk and an '
                    'expensive driver to insure. Change how you drive.',
    'Risky':        'Your driving is risky. {habits}. You are more likely to crash and to be '
                    'rated up on premiums. Smooth it out.',
    'Inconsistent': 'Inconsistent. Some trips are fine, others careless. The bad habits are what get '
                    'you hurt and pushed into a higher risk band.',
    'Solid':        'Solid, sensible driving. A few rough edges, but broadly safe.',
    'Exemplary':    'Exemplary. Smooth, controlled and low-risk — exactly the driver insurers want.',
}


def driving_profile(trips: list[dict]) -> dict:
    # GUARDRAIL (CFO, 2026-09-08): grade only trips that physically happened,
    # and take a peak speed only from a trip whose distance can account for it.
    # Junk rows stay in the database for audit but can never brand a driver —
    # a single 0 km, 12-second GPS spike previously supplied 2 of 3 harsh
    # events and a phantom 132 km/h peak, reading out as "DANGEROUS".
    graded = []
    for t in trips:
        q = trip_quality(distance_km=t.get('distance_km'), duration_min=t.get('duration_min'),
                         max_speed=t.get('max_speed'))
        if not q['valid']:
            continue
        graded.append({**t, 'max_speed': q['max_speed']})
    excluded = len(trips) - len(graded)
    trips = graded
    n = len(trips)
    if n == 0:
        verdict = ('No trips recorded yet. Tap Start drive to build your profile.' if excluded == 0
                   else f'No gradeable trips yet — {excluded} recording(s) were too short to grade. '
                        'Tap Start drive and go for a proper drive.')
        return {'band': 'No data', 'verdict': verdict,
                'trips': 0, 'totalKm': 0, 'avgScore': None, 'harshPer100km': None,
                'harshEvents': 0, 'idlePct': 0, 'topSpeed': 0, 'flags': [],
                'excludedTrips': excluded}

    total_km = sum(float(t.get('distance_km') or 0) for t in trips)
    # Distance-WEIGHTED average so risk reflects km driven, not trip count — a
    # long dangerous trip must dominate, and it can't be diluted by spamming
    # many short (confidence-floored ~high-scoring) trips. MIN_W keeps a short
    # trip from counting as zero exposure.
    _MIN_W = 0.5
    wsum = sum(max(float(t.get('distance_km') or 0), _MIN_W) for t in trips)
    avg_score = round(sum(int(t.get('score') or 0) * max(float(t.get('distance_km') or 0), _MIN_W)
                          for t in trips) / wsum)
    harsh = sum(int(t.get('harsh_events') or 0) for t in trips)
    # A rate needs a denominator. Floor it at MIN_RATE_KM so a sub-kilometre
    # total is never printed as if the raw event COUNT were a per-100 km rate.
    harsh_per_100 = round(harsh / (max(total_km, MIN_RATE_KM) / 100.0), 1)
    total_dur = sum(float(t.get('duration_min') or 0) for t in trips)
    total_idle = sum(float(t.get('idle_minutes') or 0) for t in trips)
    idle_pct = round(total_idle / total_dur * 100) if total_dur > 0 else 0
    top_speed = round(max((float(t.get('max_speed') or 0) for t in trips), default=0))

    # Danger override FIRST — the band must agree with the flags below (both
    # derive from harsh_per_100 / top_speed). Aggressive/speeding drivers cannot
    # hide behind a diluted average, even if all their bad trips are short.
    # ...but a rate off one or two events is noise, not a pattern: 1 hard brake
    # in 10 km read out as "9.3 per 100 km" and branded a safe driver Risky.
    # The override needs a real sample (events AND distance) before it fires.
    rate_trustworthy = harsh >= MIN_HARSH_FOR_RATE and total_km >= MIN_KM_FOR_RATE
    if (rate_trustworthy and harsh_per_100 >= 10) or top_speed > 160:
        band = 'Dangerous'
    elif (rate_trustworthy and harsh_per_100 >= 5) or top_speed > 140:
        band = 'Risky'
    elif avg_score >= 85 and harsh_per_100 < 1:
        band = 'Exemplary'
    elif avg_score >= 70:
        band = 'Solid'
    elif avg_score >= 55:
        band = 'Inconsistent'
    elif avg_score >= 40:
        band = 'Risky'
    else:
        band = 'Dangerous'

    flags = []
    # Same trustworthiness gate as the band above — one parser, both places.
    # Otherwise a driver lands "Solid" while being told they drive aggressively
    # off 2 events in 4 km (guards must agree in both directions).
    if rate_trustworthy and harsh_per_100 >= 3:
        flags.append(f'{harsh_per_100} hard brakes/accelerations per 100 km — you drive aggressively.')
    elif rate_trustworthy and harsh_per_100 >= 1.5:
        flags.append(f'{harsh_per_100} hard events per 100 km — ease off the brakes and throttle.')
    elif harsh > 0 and not rate_trustworthy:
        flags.append(f'{harsh} hard event(s) so far — too little driving to call it a habit yet.')
    if top_speed > 140:
        flags.append(f'Top speed {top_speed} km/h — you speed, plainly.')
    elif top_speed > 120:
        flags.append(f'You went over 120 km/h (peak {top_speed}).')
    if idle_pct >= 25:
        flags.append(f'{idle_pct}% of your time is spent idling — wasteful and pollutes.')

    habits = []
    if rate_trustworthy and harsh_per_100 >= 3:
        habits.append('hard braking')
    if top_speed > 120:
        habits.append('speeding')
    if habits:
        habit_text = (' and '.join(habits).capitalize()
                      + (' are routine for you' if len(habits) > 1 else ' is routine for you'))
    else:
        habit_text = 'Your trip scores are poor'
    verdict = _VERDICTS[band].format(habits=habit_text)
    if n < 3:
        verdict = f'Early read ({n} trip{"s" if n != 1 else ""}): ' + verdict

    return {'band': band, 'verdict': verdict, 'trips': n,
            'totalKm': round(total_km, 1), 'avgScore': avg_score,
            # A rate off one or two events is not a fact about the driver, so it
            # is withheld rather than printed as one. The raw count is honest
            # and goes out instead, for the screen to show as "1 hard event".
            'harshPer100km': harsh_per_100 if rate_trustworthy else None,
            'harshEvents': harsh,
            'idlePct': idle_pct,
            'topSpeed': top_speed, 'flags': flags, 'excludedTrips': excluded}
