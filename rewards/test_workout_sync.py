"""rewards/test_workout_sync.py — Alpha Nexus v11 verified workout-sync backend.

Mirrors test_step_sync.py for exercise sessions read from Health Connect:
  * a web-session bearer CANNOT post a workout (device-token only);
  * a paired device earns +2 per real session, max 2 sessions a day;
  * a replay of the same Health Connect session id earns 0 (deduped);
  * a re-sync of the same day earns 0 once the day is paid;
  * too-short / malformed sessions earn nothing;
  * a far-past day is refused; a future day is clamped to today.
Points are REWARDS ONLY — never insurance pricing (CFO 16-Aug-2026).
"""
from __future__ import annotations

import datetime as dt
from datetime import timedelta
from unittest import mock

from django.utils import timezone
from rest_framework.test import APITestCase

from . import customer_auth, device_auth
from .models import RewardMember, CustomerWorkoutSession

SYNC = '/api/v1/rewards/customer/workouts/sync/'

# The clock is FROZEN for every test in this file. Several of them build a
# RANGE of sessions backwards from `timezone.now()` (up to two hours) and then
# assert the per-LOCAL-DAY paid cap. Botswana is UTC+2 always, so inside the
# first two hours after local midnight that range straddles two local dates and
# the engine correctly pays the cap TWICE - once per day. CI run 34409862936
# (shard 4) hit it at 2026-09-09 22:04 UTC = 00:04 Gaborone and turned
# `test_batch_is_capped` red (8 != 4), blocking PR #808, a Markdown-only change.
# The cap is the thing under test, so the clock is pinned rather than the
# assertion loosened. Same pattern as bonu/test_botswana_time.py.
MIDMORNING_UTC = dt.datetime(2026, 9, 9, 8, 0, tzinfo=dt.timezone.utc)   # 10:00 Gaborone
AFTER_MIDNIGHT_UTC = dt.datetime(2026, 9, 9, 23, 30, tzinfo=dt.timezone.utc)  # 01:30 Gaborone


def _freeze(case, instant):
    """Pin `timezone.now()` - and with it `timezone.localdate()`, which reads
    it - for the rest of the test, setUp included."""
    p = mock.patch('django.utils.timezone.now', return_value=instant)
    p.start()
    case.addCleanup(p.stop)


def _session(sid: str, minutes: int = 30, start=None, ex_type: int = 79):
    start = start or (timezone.now() - timedelta(hours=2))
    return {
        'id': sid,
        'exerciseType': ex_type,
        'start': start.isoformat(),
        'end': (start + timedelta(minutes=minutes)).isoformat(),
    }


class WorkoutSyncTests(APITestCase):
    def setUp(self):
        _freeze(self, MIDMORNING_UTC)   # a 2h lookback cannot cross a local date
        self.member = RewardMember.objects.create(
            customer_name='W member', email='workout@gmail.com', is_active=True)
        self.session = customer_auth.start_session(self.member)
        self.device = device_auth.complete_pairing(device_auth.issue_pairing_code(self.member))

    def _sync(self, sessions, token):
        return self.client.post(SYNC, {'sessions': sessions}, format='json',
                                HTTP_AUTHORIZATION=f'Bearer {token}')

    # --- the whole point: a web session cannot post a workout ---------------
    def test_web_session_token_is_rejected(self):
        r = self._sync([_session('hc-1')], self.session)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(CustomerWorkoutSession.objects.count(), 0)

    def test_no_token_is_rejected(self):
        r = self.client.post(SYNC, {'sessions': [_session('hc-1')]}, format='json')
        self.assertEqual(r.status_code, 401)

    # --- a paired device earns +2 per real session, capped 2/day ------------
    def test_device_earns_two_per_session_max_two_sessions_a_day(self):
        r = self._sync([_session('hc-1')], self.device)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pointsAdded'], 2)
        self.assertEqual(r.data['sessionsToday'], 1)

        r2 = self._sync([_session('hc-2', start=timezone.now() - timedelta(hours=1))], self.device)
        self.assertEqual(r2.data['pointsAdded'], 2)
        self.assertEqual(r2.data['sessionsToday'], 2)

        r3 = self._sync([_session('hc-3', start=timezone.now() - timedelta(minutes=40))], self.device)
        self.assertEqual(r3.data['pointsAdded'], 0)          # third session: recorded, unpaid
        self.assertEqual(r3.data['sessionsToday'], 3)

        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 4)

    def test_replay_of_same_session_id_earns_zero(self):
        s = _session('hc-dup')
        r1 = self._sync([s], self.device)
        self.assertEqual(r1.data['pointsAdded'], 2)
        r2 = self._sync([s], self.device)                      # replay
        self.assertEqual(r2.data['pointsAdded'], 0)
        r3 = self._sync([s, s], self.device)                   # duplicate inside one batch
        self.assertEqual(r3.data['pointsAdded'], 0)
        self.assertEqual(CustomerWorkoutSession.objects.filter(member=self.member).count(), 1)
        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 2)

    def test_short_or_malformed_sessions_earn_nothing(self):
        r = self._sync([
            _session('hc-short', minutes=3),                  # under the minimum
            {'id': 'hc-bad', 'start': 'not-a-date', 'end': ''},
            {'exerciseType': 79},                              # no id
            {'id': 'hc-naive', 'start': '2026-09-07T10:00:00', 'end': '2026-09-07T10:40:00'},  # no zone
        ], self.device)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pointsAdded'], 0)
        self.assertEqual(CustomerWorkoutSession.objects.count(), 0)

    def test_client_points_are_ignored(self):
        s = _session('hc-1')
        s['points'] = 500                                      # a client cannot name its reward
        r = self._sync([s], self.device)
        self.assertEqual(r.data['pointsAdded'], 2)

    def test_far_past_session_is_refused_and_future_is_clamped(self):
        old = timezone.now() - timedelta(days=30)
        r = self._sync([_session('hc-old', start=old)], self.device)
        self.assertEqual(r.data['pointsAdded'], 0)
        self.assertFalse(CustomerWorkoutSession.objects.filter(session_id='hc-old').exists())

        future = timezone.now() + timedelta(days=3)
        r2 = self._sync([_session('hc-fut', start=future)], self.device)
        self.assertEqual(r2.data['pointsAdded'], 2)
        row = CustomerWorkoutSession.objects.get(session_id='hc-fut')
        self.assertEqual(row.day, timezone.localdate())

    def test_batch_is_capped(self):
        many = [_session(f'hc-{i}', start=timezone.now() - timedelta(minutes=i)) for i in range(60)]
        r = self._sync(many, self.device)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pointsAdded'], 4)            # still max 2 paid sessions
        self.assertLessEqual(CustomerWorkoutSession.objects.count(), 25)

    def test_apple_health_source_is_recorded_and_unknown_source_falls_back(self):
        r = self.client.post(SYNC, {'sessions': [_session('hc-ios')], 'source': 'apple_health'},
                             format='json', HTTP_AUTHORIZATION=f'Bearer {self.device}')
        self.assertEqual(r.data['pointsAdded'], 2)
        self.assertEqual(CustomerWorkoutSession.objects.get(session_id='hc-ios').source, 'apple_health')
        r2 = self.client.post(SYNC, {'sessions': [_session('hc-x', start=timezone.now() - timedelta(hours=1))],
                                     'source': 'typed-by-hand'},
                              format='json', HTTP_AUTHORIZATION=f'Bearer {self.device}')
        self.assertEqual(CustomerWorkoutSession.objects.get(session_id='hc-x').source, 'health_connect')

    def test_logout_revokes_device_token(self):
        self.client.post('/api/v1/rewards/customer/logout/', {}, format='json',
                         HTTP_AUTHORIZATION=f'Bearer {self.session}')
        r = self._sync([_session('hc-after')], self.device)
        self.assertEqual(r.status_code, 401)


class WorkoutCapAfterGaboroneMidnightTests(APITestCase):
    """The cap, proven INSIDE the window that turned CI red.

    Frozen at 2026-09-09 23:30 UTC = 01:30 Gaborone on the 10th, with all 60
    sessions built inside that local day (00:31 -> 01:30). The batch limit
    keeps the first 25; two of them are paid and the rest are stored at 0.
    Revert the freeze in `WorkoutSyncTests.setUp` and `test_batch_is_capped`
    up there goes red again on any run started in the first two hours of a
    Gaborone day; this class stays green because it never reads the real clock.
    """

    def setUp(self):
        _freeze(self, AFTER_MIDNIGHT_UTC)
        self.member = RewardMember.objects.create(
            customer_name='Midnight member', email='midnight@gmail.com', is_active=True)
        self.device = device_auth.complete_pairing(device_auth.issue_pairing_code(self.member))

    def test_the_daily_cap_still_pays_twice_only(self):
        self.assertEqual(timezone.localdate(), dt.date(2026, 9, 10))   # we really are past midnight
        many = [_session(f'hc-m{i}', start=timezone.now() - timedelta(minutes=i))
                for i in range(60)]
        r = self.client.post(SYNC, {'sessions': many}, format='json',
                             HTTP_AUTHORIZATION=f'Bearer {self.device}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pointsAdded'], 4)
        # every stored session landed on the ONE local day, not split across two
        self.assertEqual(
            set(CustomerWorkoutSession.objects.values_list('day', flat=True)),
            {dt.date(2026, 9, 10)})
        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 4)
