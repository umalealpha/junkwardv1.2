"""Tests for the Alpha Nexus streak-reward engine (rewards/health_points.py).

Pure-function logic (no DB) → SimpleTestCase. Covers the streak multiplier
tiers and the freeze-token ("forgiveness") streak bridging edge cases that the
feature blueprint calls out as the architecturally treacherous part.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from rewards import health_points as hp
from rewards import customer_auth, drive_score
from rewards.models import CustomerFeedback, HealthConsent, HealthMetric, RewardMember


class StreakMultiplierTests(SimpleTestCase):
    def test_tier_boundaries(self):
        self.assertEqual(hp.streak_multiplier(0),   Decimal('1.00'))
        self.assertEqual(hp.streak_multiplier(6),   Decimal('1.00'))
        self.assertEqual(hp.streak_multiplier(7),   Decimal('1.25'))
        self.assertEqual(hp.streak_multiplier(29),  Decimal('1.25'))
        self.assertEqual(hp.streak_multiplier(30),  Decimal('1.50'))
        self.assertEqual(hp.streak_multiplier(99),  Decimal('1.50'))
        self.assertEqual(hp.streak_multiplier(100), Decimal('2.00'))
        self.assertEqual(hp.streak_multiplier(10_000), Decimal('2.00'))

    def test_negative_and_none_are_safe(self):
        self.assertEqual(hp.streak_multiplier(-5),   Decimal('1.00'))
        self.assertEqual(hp.streak_multiplier(None), Decimal('1.00'))


class EffectiveStreakTests(SimpleTestCase):
    TODAY = date(2026, 6, 28)

    def days(self, *offsets):
        """Set of dates at the given day-offsets back from TODAY (0 == today)."""
        return {self.TODAY - timedelta(days=o) for o in offsets}

    def test_empty_history_is_inactive(self):
        r = hp.effective_streak(set(), self.TODAY)
        self.assertEqual(r, {'streak': 0, 'freezes_used': 0, 'active': False})

    def test_consecutive_from_today(self):
        r = hp.effective_streak(self.days(0, 1, 2), self.TODAY)
        self.assertEqual(r['streak'], 3)
        self.assertEqual(r['freezes_used'], 0)
        self.assertTrue(r['active'])

    def test_yesterday_grace_when_today_not_yet_synced(self):
        # today missing but yesterday present → streak still active, no token spent
        r = hp.effective_streak(self.days(1, 2, 3), self.TODAY)
        self.assertEqual(r['streak'], 3)
        self.assertEqual(r['freezes_used'], 0)
        self.assertTrue(r['active'])

    def test_broken_when_today_and_yesterday_both_missing(self):
        r = hp.effective_streak(self.days(2, 3, 4), self.TODAY)
        self.assertFalse(r['active'])
        self.assertEqual(r['streak'], 0)

    def test_freeze_bridges_single_day_gap(self):
        # active 0,1, gap at 2, active 3,4 ; one token bridges the gap
        r = hp.effective_streak(self.days(0, 1, 3, 4), self.TODAY, freeze_tokens=1)
        self.assertEqual(r['streak'], 4)
        self.assertEqual(r['freezes_used'], 1)

    def test_freeze_insufficient_for_two_day_gap(self):
        # gap at 2 AND 3 (two missing days), only one token → chain breaks at the 2nd
        r = hp.effective_streak(self.days(0, 1, 4, 5), self.TODAY, freeze_tokens=1)
        self.assertEqual(r['streak'], 2)
        self.assertEqual(r['freezes_used'], 1)

    def test_tokens_not_wasted_past_end_of_history(self):
        # plenty of tokens but history ends — must not consume tokens into the void
        r = hp.effective_streak(self.days(0, 1), self.TODAY, freeze_tokens=5)
        self.assertEqual(r['streak'], 2)
        self.assertEqual(r['freezes_used'], 0)

    def test_zero_tokens_breaks_on_first_gap(self):
        r = hp.effective_streak(self.days(0, 1, 3), self.TODAY, freeze_tokens=0)
        self.assertEqual(r['streak'], 2)
        self.assertEqual(r['freezes_used'], 0)

    def test_freeze_bridges_gap_under_yesterday_grace(self):
        # today missing (grace → start yesterday=1), gap at 2, active 3
        r = hp.effective_streak(self.days(1, 3), self.TODAY, freeze_tokens=1)
        self.assertEqual(r['streak'], 2)
        self.assertEqual(r['freezes_used'], 1)
        self.assertTrue(r['active'])


# ---------------------------------------------------------------------------
# Click & Drive trip scoring (rewards/drive_score.py trip_score) — pure fn.
# ---------------------------------------------------------------------------

def _factor(res, key):
    """The single factor dict with the given key, or None."""
    return next((f for f in res['factors'] if f['key'] == key), None)


class TripScoreTests(SimpleTestCase):
    def test_harsh_rate_floor_no_max_penalty_on_short_trip(self):
        # 1 harsh event on a 2 km trip. denom is floored at HARSH_RATE_FLOOR_KM=5,
        # so per10 = 1 / (5/10) = 2 → penalty = 2 * 6 = 12, NOT the 30 you'd get
        # dividing by the real 2 km, and well under the 45 hard cap.
        res = drive_score.trip_score(distance_km=2, duration_min=6, idle_minutes=0,
                                     harsh_events=1, max_speed=60)
        pen = _factor(res, 'smoothness')['penalty']
        self.assertEqual(pen, 12.0)
        self.assertLess(pen, 45.0)  # nowhere near max harsh penalty

    def test_short_trip_is_provisional_and_blends_toward_neutral(self):
        # 2 km, 2 harsh: raw is pulled toward NEUTRAL_SCORE=80 (conf floor 0.2),
        # flagged provisional with a 'confidence' factor.
        res = drive_score.trip_score(distance_km=2, duration_min=6, idle_minutes=0,
                                     harsh_events=2, max_speed=60)
        self.assertTrue(res['provisional'])
        self.assertIsNotNone(_factor(res, 'confidence'))
        # raw would be 100-24=76; blended: 80*0.8 + 76*0.2 = 79.2 → 79 (≈ neutral)
        self.assertEqual(res['score'], 79)
        self.assertGreaterEqual(res['score'], 76)  # regressed up toward 80

    def test_long_trip_is_not_provisional_and_fully_graded(self):
        # 12 km (>= FULL_CONFIDENCE_KM=10): conf=1.0, no blend, no 'confidence' factor.
        res = drive_score.trip_score(distance_km=12, duration_min=20, idle_minutes=0,
                                     harsh_events=2, max_speed=60)
        self.assertFalse(res['provisional'])
        self.assertIsNone(_factor(res, 'confidence'))
        # per10 = 2 / (12/10) ≈ 1.667 → pen ≈ 10.0; raw == score at full confidence
        self.assertEqual(res['score'], 90)

    def test_zeroish_inputs_clamp_sanely(self):
        res = drive_score.trip_score(distance_km=0, duration_min=0, idle_minutes=0,
                                     harsh_events=0, max_speed=0)
        self.assertTrue(0 <= res['score'] <= 100)
        self.assertEqual(res['points'], 0)          # 0 km earns nothing
        self.assertTrue(res['provisional'])

    def test_negative_and_extreme_inputs_stay_in_range(self):
        # Negatives coerce to 0; a brutal long trip floors at 0, never negative.
        lo = drive_score.trip_score(distance_km=-5, duration_min=-10, idle_minutes=-3,
                                    harsh_events=-4, max_speed=-99)
        self.assertTrue(0 <= lo['score'] <= 100)
        hi = drive_score.trip_score(distance_km=20, duration_min=20, idle_minutes=20,
                                    harsh_events=100, max_speed=300)
        self.assertTrue(0 <= hi['score'] <= 100)


# ---------------------------------------------------------------------------
# Driving profile (rewards/drive_score.py driving_profile) — pure fn.
# ---------------------------------------------------------------------------

def _trip(score, distance_km, harsh_events=0, max_speed=60, duration_min=30, idle_minutes=0):
    return {'score': score, 'distance_km': distance_km, 'harsh_events': harsh_events,
            'max_speed': max_speed, 'duration_min': duration_min, 'idle_minutes': idle_minutes}


class DrivingProfileTests(SimpleTestCase):
    def test_danger_override_by_harsh_rate_beats_high_avg(self):
        # 3 harsh events over 10 km = 30 harsh/100km → Dangerous, despite avg 95.
        p = drive_score.driving_profile([_trip(95, 10, harsh_events=3, max_speed=80)])
        self.assertEqual(p['harshPer100km'], 30.0)
        self.assertEqual(p['band'], 'Dangerous')

    def test_danger_override_by_top_speed_beats_high_avg(self):
        p = drive_score.driving_profile([_trip(95, 10, harsh_events=0, max_speed=170)])
        self.assertEqual(p['band'], 'Dangerous')

    def test_risky_override_by_harsh_rate(self):
        # 3 harsh over 60 km = 5 harsh/100km → Risky (>=5, <10), avg still high.
        p = drive_score.driving_profile([_trip(95, 60, harsh_events=3, max_speed=80)])
        self.assertEqual(p['harshPer100km'], 5.0)
        self.assertEqual(p['band'], 'Risky')

    def test_risky_override_by_top_speed(self):
        p = drive_score.driving_profile([_trip(95, 10, harsh_events=0, max_speed=150)])
        self.assertEqual(p['band'], 'Risky')

    def test_long_bad_trip_not_diluted_by_many_short_good_trips(self):
        # 1 long bad trip (100 km, score 20) + 20 short "perfect" hops (0.1 km).
        # Count-average would be ~96; distance-weighting (MIN_W=0.5) pins it low.
        # (hops are 0.5 km — above MIN_TRIP_KM, so they are graded, not skipped)
        trips = [_trip(20, 100)] + [_trip(100, 0.5) for _ in range(20)]
        p = drive_score.driving_profile(trips)
        self.assertEqual(p['avgScore'], 27)   # (20*100 + 20*100*0.5) / (100 + 20*0.5)
        self.assertLess(p['avgScore'], 50)     # the long bad trip dominates

    def test_empty_profile_is_no_data(self):
        p = drive_score.driving_profile([])
        self.assertEqual(p['band'], 'No data')
        self.assertEqual(p['trips'], 0)


# ---------------------------------------------------------------------------
# Data-quality guardrails (CFO, 2026-09-08): a GPS lock-on spike is not driving.
# The live bug: the CFO's own profile read "DANGEROUS · 27.3 harsh/100km ·
# peak 132 km/h" off ONE row that covered 0.00 km in 12 seconds. That row
# supplied 2 of his 3 harsh events and the entire 132 km/h reading. His fastest
# real driving was 74 km/h.
# ---------------------------------------------------------------------------

# The CFO's six real rows, copied off prod 2026-09-08.
_CFO_TRIPS = [
    _trip(67,  0.00, harsh_events=2, max_speed=131.8, duration_min=0.2,  idle_minutes=0.2),
    _trip(100, 1.22, harsh_events=0, max_speed=44.7,  duration_min=2.5),
    _trip(100, 0.18, harsh_events=0, max_speed=73.9,  duration_min=0.1),
    _trip(82,  2.65, harsh_events=1, max_speed=73.7,  duration_min=16.4),
    _trip(92,  6.22, harsh_events=0, max_speed=69.5,  duration_min=6.5),
    _trip(84,  0.71, harsh_events=0, max_speed=48.6,  duration_min=3.4, idle_minutes=0.3),
]


class TripQualityTests(SimpleTestCase):
    def test_zero_distance_spike_is_not_a_drive(self):
        q = drive_score.trip_quality(distance_km=0.0, duration_min=0.2, max_speed=131.8)
        self.assertFalse(q['valid'])
        self.assertEqual(q['reason'], 'no_distance')
        self.assertEqual(q['max_speed'], 0.0)

    def test_twelve_second_hop_is_not_a_drive(self):
        q = drive_score.trip_quality(distance_km=0.18, duration_min=0.1, max_speed=73.9)
        self.assertFalse(q['valid'])

    def test_real_short_trip_is_still_graded(self):
        q = drive_score.trip_quality(distance_km=0.71, duration_min=3.4, max_speed=48.6)
        self.assertTrue(q['valid'])
        self.assertTrue(q['speed_reliable'])
        self.assertEqual(q['max_speed'], 48.6)

    def test_peak_the_distance_cannot_account_for_is_unknown_not_zero_risk(self):
        # 130 km/h held for 10 s covers 0.36 km. A 0.4 km trip can just about
        # support it; a 0.31 km trip cannot, so that reading is discarded.
        ok = drive_score.trip_quality(distance_km=0.40, duration_min=2.0, max_speed=130)
        bad = drive_score.trip_quality(distance_km=0.31, duration_min=2.0, max_speed=130)
        self.assertTrue(ok['speed_reliable'])
        self.assertEqual(ok['max_speed'], 130)
        self.assertTrue(bad['valid'])            # the trip itself is real
        self.assertFalse(bad['speed_reliable'])  # ...but the speed is not
        self.assertEqual(bad['max_speed'], 0.0)

    def test_absurd_speed_is_never_reported(self):
        q = drive_score.trip_quality(distance_km=50, duration_min=30, max_speed=400)
        self.assertFalse(q['speed_reliable'])
        self.assertEqual(q['max_speed'], 0.0)

    def test_long_real_trip_keeps_its_high_peak(self):
        q = drive_score.trip_quality(distance_km=91.96, duration_min=101.4, max_speed=141.2)
        self.assertTrue(q['valid'])
        self.assertTrue(q['speed_reliable'])
        self.assertEqual(q['max_speed'], 141.2)


class ProfileGuardrailTests(SimpleTestCase):
    def test_cfo_profile_is_not_dangerous_and_reports_his_real_top_speed(self):
        p = drive_score.driving_profile(_CFO_TRIPS)
        self.assertEqual(p['excludedTrips'], 2)
        self.assertEqual(p['trips'], 4)
        self.assertEqual(p['topSpeed'], 74)          # 73.7, not the phantom 132
        self.assertNotEqual(p['band'], 'Dangerous')
        self.assertNotIn('120', ' '.join(p['flags']))

    def test_junk_row_cannot_supply_harsh_events(self):
        # 2 of his 3 recorded events came off the 0 km row — 1 real one is left.
        p = drive_score.driving_profile(_CFO_TRIPS)
        self.assertEqual(p['harshEvents'], 1)        # not 3
        # ...and a rate off ONE event is withheld rather than printed as 9.3.
        self.assertIsNone(p['harshPer100km'])

    def test_verdict_never_claims_speeding_when_there_is_none(self):
        p = drive_score.driving_profile(_CFO_TRIPS)
        self.assertNotIn('speeding', p['verdict'].lower())

    def test_band_and_flags_never_contradict(self):
        # A driver must not land "Solid" while being told they drive
        # aggressively — one trustworthiness gate serves both (mirror gate).
        p = drive_score.driving_profile([_trip(78, 3.94, harsh_events=2, max_speed=72.3,
                                               duration_min=7.1, idle_minutes=0.6)])
        self.assertEqual(p['band'], 'Solid')
        self.assertNotIn('aggressively', ' '.join(p['flags']))

    def test_two_events_on_a_few_km_is_not_yet_a_habit(self):
        p = drive_score.driving_profile([_trip(95, 10, harsh_events=1, max_speed=80)])
        self.assertNotEqual(p['band'], 'Dangerous')  # was Dangerous before the guard
        self.assertEqual(p['band'], 'Solid')

    def test_real_aggression_is_still_called_out(self):
        # The guard must not become a way for a bad driver to hide: 43 events
        # over 124.6 km (a live tester) stays Dangerous.
        trips = [_trip(79, 124.6 / 8, harsh_events=6, max_speed=106) for _ in range(8)]
        p = drive_score.driving_profile(trips)
        self.assertEqual(p['band'], 'Dangerous')
        self.assertIn('aggressively', ' '.join(p['flags']))

    def test_all_junk_reads_as_no_data_not_as_a_grade(self):
        p = drive_score.driving_profile([_CFO_TRIPS[0], _CFO_TRIPS[2]])
        self.assertEqual(p['band'], 'No data')
        self.assertEqual(p['excludedTrips'], 2)
        self.assertEqual(p['topSpeed'], 0)

    def test_rate_is_withheld_on_a_sub_kilometre_denominator(self):
        # 2 events over 0.5 km: the sample is far too small to state a rate, and
        # it must never be printed as "2.0 per 100 km" (the raw count mislabelled).
        p = drive_score.driving_profile([_trip(60, 0.5, harsh_events=2, max_speed=40,
                                              duration_min=3)])
        self.assertIsNone(p['harshPer100km'])
        self.assertEqual(p['harshEvents'], 2)

    def test_a_trustworthy_sample_still_gets_its_rate(self):
        p = drive_score.driving_profile([_trip(60, 20, harsh_events=4, max_speed=90,
                                              duration_min=30)])
        self.assertEqual(p['harshPer100km'], 20.0)


# ---------------------------------------------------------------------------
# Customer feedback endpoint (rewards/customer_views.customer_feedback).
# ---------------------------------------------------------------------------

class CustomerFeedbackTests(APITestCase):
    URL = '/api/v1/rewards/customer/feedback/'

    def setUp(self):
        self.member = RewardMember.objects.create(
            customer_name='T member', email='drive@gmail.com', is_active=True)
        self.token = customer_auth.start_session(self.member)

    def _post(self, body, auth=True):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'} if auth else {}
        return self.client.post(self.URL, body, format='json', **headers)

    def test_requires_customer_session(self):
        r = self._post({'rating': 5}, auth=False)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(CustomerFeedback.objects.count(), 0)

    def test_rating_out_of_range_and_non_numeric_are_400(self):
        for bad in (0, 6, 'abc'):
            r = self._post({'rating': bad})
            self.assertEqual(r.status_code, 400, msg=f'rating={bad!r}')
        self.assertEqual(CustomerFeedback.objects.count(), 0)

    def test_valid_feedback_truncates_and_coerces_area(self):
        r = self._post({'rating': 5, 'area': 'not-a-real-area',
                        'comment': 'x' * 2500, 'wants': 'y' * 2500})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(CustomerFeedback.objects.count(), 1)
        row = CustomerFeedback.objects.get()
        self.assertEqual(row.rating, 5)
        self.assertEqual(row.area, 'overall')          # invalid area coerced
        self.assertEqual(len(row.comment), 2000)        # truncated to 2000
        self.assertEqual(len(row.wants), 2000)

    def test_daily_cap_accepts_but_stops_creating_rows(self):
        for _ in range(10):
            self.assertEqual(self._post({'rating': 4}).status_code, 201)
        self.assertEqual(CustomerFeedback.objects.count(), 10)
        # 11th still 201 (accepted silently) but NO new row past FEEDBACK_DAILY_CAP=10.
        r = self._post({'rating': 4})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(CustomerFeedback.objects.count(), 10)


class CustomerHealthEndpointTests(APITestCase):
    """The phone app's own consent + steps endpoints.

    These exist because the published Android app previously called the STAFF
    endpoints, which demand a member id in the body and a staff token — a
    customer has neither. Here the member comes from the login token only.
    """
    CONSENT_URL = '/api/v1/rewards/customer/health-consent/'
    METRICS_URL = '/api/v1/rewards/customer/health-metrics/'

    def setUp(self):
        self.member = RewardMember.objects.create(
            customer_name='H member', email='health@gmail.com', is_active=True)
        self.other = RewardMember.objects.create(
            customer_name='Other member', email='other@gmail.com', is_active=True)
        self.token = customer_auth.start_session(self.member)

    def _post(self, url, body, auth=True):
        headers = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'} if auth else {}
        return self.client.post(url, body, format='json', **headers)

    def test_both_endpoints_require_a_signed_in_member(self):
        self.assertEqual(self._post(self.CONSENT_URL, {'dataTypes': ['steps']}, auth=False).status_code, 401)
        self.assertEqual(self._post(self.METRICS_URL, {'steps': 5000}, auth=False).status_code, 401)
        self.assertEqual(HealthConsent.objects.count(), 0)
        self.assertEqual(HealthMetric.objects.count(), 0)

    def test_consent_is_recorded_against_the_token_holder(self):
        r = self._post(self.CONSENT_URL, {'dataTypes': ['steps']})
        self.assertEqual(r.status_code, 201)
        consent = HealthConsent.objects.get()
        self.assertEqual(consent.member_id, self.member.id)
        self.assertEqual(consent.data_types, ['steps'])
        self.assertTrue(consent.is_active)

    def test_self_reported_steps_earn_nothing_by_default(self):
        """CFO 7-Sep-2026: a typed step count never earns points — only the
        device-synced ledger (rewards/customer_views.customer_steps_sync) does."""
        r = self._post(self.METRICS_URL, {'steps': 12000})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pointsAwarded'], 0)
        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 0)

    @override_settings(NEXUS_MANUAL_STEP_POINTS_ENABLED=True)
    def test_steps_award_points_once_per_day(self):
        r = self._post(self.METRICS_URL, {'steps': 12000})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pointsAwarded'], 12)
        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 12)

        # Re-posting the same day must not award again (idempotent per day).
        again = self._post(self.METRICS_URL, {'steps': 12000})
        self.assertEqual(again.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 12)
        self.assertEqual(HealthMetric.objects.filter(member=self.member).count(), 1)

    def test_a_member_id_in_the_body_is_ignored(self):
        """Anti-tamper: the payload cannot redirect the write to another member."""
        r = self._post(self.METRICS_URL, {'steps': 3000, 'memberId': str(self.other.id)})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(HealthMetric.objects.filter(member=self.member).exists())
        self.assertFalse(HealthMetric.objects.filter(member=self.other).exists())
        self.other.refresh_from_db()
        self.assertEqual(self.other.points_balance, 0)


# ---------------------------------------------------------------------------
# Click & Drive ingest + stored-record guardrails (CFO, 2026-09-08).
# ---------------------------------------------------------------------------

class DriveTripIngestGuardTests(APITestCase):
    """The ingest gate and the stored quality flags must use ONE parser.

    A previous two-parser bug let a P90k cap be bypassed; here the risk is the
    mirror image — a trip rejected on the way in but graded on the way out (or
    stored with flags that disagree with what every read path believes).
    """
    TRIP_URL = '/api/v1/rewards/customer/drive/trip/'
    DRIVE_URL = '/api/v1/rewards/customer/drive/'

    def setUp(self):
        from .models import CustomerDriveTrip
        self.Trip = CustomerDriveTrip
        self.member = RewardMember.objects.create(
            customer_name='Drive member', email='drive@gmail.com', is_active=True)
        self.token = customer_auth.start_session(self.member)

    def _post(self, body):
        return self.client.post(self.TRIP_URL, body, format='json',
                                HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def test_zero_distance_gps_spike_is_rejected_and_not_stored(self):
        r = self._post({'distanceKm': 0.0, 'durationMin': 0.2, 'idleMinutes': 0.2,
                        'harshEvents': 2, 'maxSpeed': 131.8})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.Trip.objects.count(), 0)

    def test_twelve_second_hop_is_rejected(self):
        r = self._post({'distanceKm': 0.18, 'durationMin': 0.1, 'harshEvents': 0,
                        'maxSpeed': 73.9})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.Trip.objects.count(), 0)

    def test_real_trip_is_stored_with_a_valid_verdict(self):
        r = self._post({'distanceKm': 6.22, 'durationMin': 6.5, 'idleMinutes': 0,
                        'harshEvents': 0, 'maxSpeed': 69.5})
        self.assertEqual(r.status_code, 201)
        t = self.Trip.objects.get()
        self.assertTrue(t.is_valid)
        self.assertTrue(t.speed_reliable)
        self.assertEqual(t.invalid_reason, '')

    def test_unsupported_peak_is_stored_raw_but_flagged_and_never_penalised(self):
        # 200 km/h held for 10 s needs 0.56 km; this trip covered 0.40 km.
        r = self._post({'distanceKm': 0.40, 'durationMin': 2.0, 'harshEvents': 0,
                        'maxSpeed': 200.0})
        self.assertEqual(r.status_code, 201)
        t = self.Trip.objects.get()
        self.assertTrue(t.is_valid)
        self.assertFalse(t.speed_reliable)
        self.assertEqual(t.max_speed, 200.0)          # raw reading kept for audit
        # No speed penalty may be charged off a spike (score itself is blended
        # toward neutral because 0.4 km is a short trip — that is separate).
        speed = [f for f in r.json()['trip']['factors'] if f['key'] == 'speed'][0]
        self.assertEqual(speed['penalty'], 0.0)
        self.assertEqual(speed['note'], 'peak speed not reliable — reading discarded')
        # ...and the write's OWN echo must not report the raw spike either, or
        # the score card shows "max 200 km/h" beside a discarded reading.
        self.assertIsNone(r.json()['trip']['maxSpeed'])

    def test_stored_flags_always_agree_with_the_parser_both_ways(self):
        # Drift guard: whatever is on the row must be what trip_quality says,
        # for valid AND invalid rows — a stale flag is a silent wrong answer.
        for body in ({'distanceKm': 6.22, 'durationMin': 6.5, 'maxSpeed': 69.5},
                     {'distanceKm': 0.40, 'durationMin': 2.0, 'maxSpeed': 200.0},
                     {'distanceKm': 91.96, 'durationMin': 101.4, 'maxSpeed': 141.2}):
            self._post({**body, 'idleMinutes': 0, 'harshEvents': 0})
        self.assertEqual(self.Trip.objects.count(), 3)
        for t in self.Trip.objects.all():
            q = drive_score.trip_quality(distance_km=t.distance_km,
                                         duration_min=t.duration_min,
                                         max_speed=t.max_speed)
            self.assertEqual(t.is_valid, q['valid'], f'is_valid drifted on {t.pk}')
            self.assertEqual(t.speed_reliable, q['speed_reliable'], f'speed_reliable drifted on {t.pk}')
            self.assertEqual(t.invalid_reason, q['reason'], f'invalid_reason drifted on {t.pk}')

    def test_apply_quality_flips_stale_flags_in_BOTH_directions(self):
        """The migration/command path — the thing that actually fixes the records.

        The drift test above only sees rows created through ingest, where an
        invalid row is refused before it is ever stored. So the is_valid=False
        direction — the only one that matters on prod, where 3 such rows already
        exist — was never exercised by the code that writes it. This does.
        """
        junk = self.Trip.objects.create(          # stored WRONGLY as valid
            member=self.member, started_at=timezone.now(), distance_km=0.0,
            duration_min=0.2, idle_minutes=0.2, harsh_events=2, max_speed=131.8,
            score=67, points_awarded=0, is_valid=True, invalid_reason='',
            speed_reliable=True)
        real = self.Trip.objects.create(          # stored WRONGLY as invalid
            member=self.member, started_at=timezone.now(), distance_km=6.22,
            duration_min=6.5, idle_minutes=0, harsh_events=0, max_speed=69.5,
            score=92, points_awarded=3, is_valid=False, invalid_reason='no_distance',
            speed_reliable=False)

        self.assertTrue(drive_score.apply_quality(junk))
        self.assertTrue(drive_score.apply_quality(real))
        junk.refresh_from_db(); real.refresh_from_db()

        self.assertFalse(junk.is_valid)
        self.assertEqual(junk.invalid_reason, 'no_distance')
        self.assertFalse(junk.speed_reliable)
        self.assertEqual(junk.max_speed, 131.8)   # the reading is KEPT for audit
        self.assertTrue(real.is_valid)
        self.assertEqual(real.invalid_reason, '')
        self.assertTrue(real.speed_reliable)
        # Idempotent: a second pass writes nothing.
        self.assertFalse(drive_score.apply_quality(junk))
        self.assertFalse(drive_score.apply_quality(real))

    def test_reflag_command_fixes_the_records_and_dry_run_does_not(self):
        from django.core.management import call_command
        from io import StringIO
        t = self.Trip.objects.create(
            member=self.member, started_at=timezone.now(), distance_km=0.0,
            duration_min=0.2, idle_minutes=0.2, harsh_events=2, max_speed=131.8,
            score=67, points_awarded=0, is_valid=True, speed_reliable=True)
        call_command('reflag_drive_trips', '--dry-run', stdout=StringIO())
        t.refresh_from_db()
        self.assertTrue(t.is_valid, 'a dry run must not write')
        call_command('reflag_drive_trips', stdout=StringIO())
        t.refresh_from_db()
        self.assertFalse(t.is_valid)
        self.assertEqual(self.Trip.objects.count(), 1, 'nothing may be deleted')

    def test_drive_screen_hides_an_unreliable_speed_and_labels_the_row(self):
        self.Trip.objects.create(
            member=self.member, started_at=timezone.now(), distance_km=0.0,
            duration_min=0.2, idle_minutes=0.2, harsh_events=2, max_speed=131.8,
            score=67, points_awarded=0, is_valid=False, invalid_reason='no_distance',
            speed_reliable=False)
        self.Trip.objects.create(
            member=self.member, started_at=timezone.now(), distance_km=6.22,
            duration_min=6.5, idle_minutes=0, harsh_events=0, max_speed=69.5,
            score=92, points_awarded=3)
        r = self.client.get(self.DRIVE_URL, HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d['profile']['trips'], 1)
        self.assertEqual(d['profile']['excludedTrips'], 1)
        self.assertEqual(d['profile']['topSpeed'], 70)   # 69.5, not 132
        junk = [t for t in d['trips'] if not t['counted']]
        self.assertEqual(len(junk), 1)
        self.assertIsNone(junk[0]['maxSpeed'])           # no phantom speed shown
        self.assertTrue(junk[0]['notCountedWhy'])        # ...and it says why
