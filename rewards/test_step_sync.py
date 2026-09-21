"""rewards/test_step_sync.py — Alpha Nexus v10 verified step-sync backend.

Proves the anti-fake core Fable demanded:
  * a web-session bearer CANNOT reach the steps endpoint (device-token only);
  * a typed / web POST earns nothing;
  * a paired device earns on the GROWTH of the day's total (delta), so a
    re-sync tops up and an exact replay earns 0;
  * the per-(member, day) row makes double-counting structurally impossible;
  * the sanity cap holds; a future date is clamped;
  * pairing codes are single-use and expire;
  * logout revokes the device token.
"""
from __future__ import annotations

from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from . import customer_auth, device_auth
from .models import (
    RewardMember, CustomerActivity, CustomerDeviceToken, CustomerDevicePairingCode,
    CustomerStepDay,
)

PAIR_START = '/api/v1/rewards/customer/pair/start/'
PAIR_DONE = '/api/v1/rewards/customer/pair/complete/'
SYNC = '/api/v1/rewards/customer/steps/sync/'
ACTIVITY = '/api/v1/rewards/customer/activity/'
LOGOUT = '/api/v1/rewards/customer/logout/'


class StepSyncTests(APITestCase):
    def setUp(self):
        self.member = RewardMember.objects.create(
            customer_name='S member', email='steps@gmail.com', is_active=True)
        self.session = customer_auth.start_session(self.member)   # 30-day web session
        self.device = device_auth.complete_pairing(device_auth.issue_pairing_code(self.member))

    def _sync(self, body, token):
        return self.client.post(SYNC, body, format='json',
                                HTTP_AUTHORIZATION=f'Bearer {token}')

    # --- the whole point: web session cannot post steps ---------------------
    def test_web_session_token_is_rejected_by_steps_endpoint(self):
        r = self._sync({'steps': 8000}, self.session)   # the WEB session bearer
        self.assertEqual(r.status_code, 401)
        self.assertEqual(CustomerStepDay.objects.count(), 0)

    def test_no_token_is_rejected(self):
        r = self.client.post(SYNC, {'steps': 8000}, format='json')
        self.assertEqual(r.status_code, 401)

    # --- a paired device earns on the delta ---------------------------------
    def test_device_earns_then_resync_tops_up_and_replay_earns_zero(self):
        r = self._sync({'steps': 5000}, self.device)     # 5 points (1/1000, capped 15)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['pointsToday'], 5)
        self.assertEqual(r.data['pointsAdded'], 5)

        r2 = self._sync({'steps': 9000}, self.device)    # grows to 9 -> +4
        self.assertEqual(r2.data['pointsToday'], 9)
        self.assertEqual(r2.data['pointsAdded'], 4)

        r3 = self._sync({'steps': 9000}, self.device)    # exact replay -> +0
        self.assertEqual(r3.data['pointsToday'], 9)
        self.assertEqual(r3.data['pointsAdded'], 0)

        r4 = self._sync({'steps': 3000}, self.device)    # lower total -> +0, no subtract
        self.assertEqual(r4.data['pointsToday'], 9)
        self.assertEqual(r4.data['pointsAdded'], 0)

        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 9)   # 5 + 4, never more
        self.assertEqual(CustomerStepDay.objects.filter(member=self.member).count(), 1)

    def test_apple_health_source_is_recorded(self):
        self._sync({'steps': 4000, 'source': 'apple_health'}, self.device)
        self.assertEqual(CustomerStepDay.objects.get(member=self.member).source, 'apple_health')

    def test_one_row_per_member_per_day(self):
        self._sync({'steps': 4000}, self.device)
        self._sync({'steps': 7000}, self.device)
        self.assertEqual(
            CustomerStepDay.objects.filter(member=self.member,
                                           day=timezone.localdate()).count(), 1)

    def test_sanity_cap_on_steps(self):
        r = self._sync({'steps': 9_999_999}, self.device)
        row = CustomerStepDay.objects.get(member=self.member)
        self.assertLessEqual(row.steps_total, 50_000)
        self.assertEqual(r.data['pointsToday'], 15)       # capped by steps_to_points

    def test_future_date_is_clamped_to_today(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        self._sync({'steps': 6000, 'day': future}, self.device)
        self.assertTrue(
            CustomerStepDay.objects.filter(member=self.member, day=timezone.localdate()).exists())
        self.assertFalse(CustomerStepDay.objects.filter(day=future).exists())

    def test_far_past_date_is_refused(self):
        old = (timezone.localdate() - timedelta(days=30)).isoformat()
        r = self._sync({'steps': 50000, 'day': old}, self.device)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(CustomerStepDay.objects.filter(day=old).exists())

    # --- pairing hygiene ----------------------------------------------------
    def test_pair_start_requires_web_session(self):
        r = self.client.post(PAIR_START, {}, format='json')      # no auth
        self.assertEqual(r.status_code, 401)

    def test_pairing_code_is_single_use(self):
        code = device_auth.issue_pairing_code(self.member)
        r1 = self.client.post(PAIR_DONE, {'code': code}, format='json')
        self.assertEqual(r1.status_code, 200)
        self.assertIn('deviceToken', r1.data)
        r2 = self.client.post(PAIR_DONE, {'code': code}, format='json')  # reuse
        self.assertEqual(r2.status_code, 400)

    def test_expired_pairing_code_is_rejected(self):
        code = device_auth.issue_pairing_code(self.member)
        CustomerDevicePairingCode.objects.filter(member=self.member).update(
            expires_at=timezone.now() - timedelta(minutes=1))
        r = self.client.post(PAIR_DONE, {'code': code}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_bad_pairing_code_is_rejected(self):
        r = self.client.post(PAIR_DONE, {'code': 'not-a-real-code'}, format='json')
        self.assertEqual(r.status_code, 400)

    # --- self-reported activity is REFUSED (CFO 7-Sep-2026: nobody may type a
    #     step count or one-tap a workout — points come from the phone's real
    #     step data only). The switch now defaults OFF. ------------------------
    def _activity(self, body):
        return self.client.post(ACTIVITY, body, format='json',
                                HTTP_AUTHORIZATION=f'Bearer {self.session}')

    def test_typed_steps_are_refused_by_default(self):
        r = self._activity({'kind': 'steps', 'steps': 12000})
        self.assertEqual(r.status_code, 400)
        self.assertIn('phone', r.data['detail'].lower())
        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 0)
        self.assertEqual(CustomerActivity.objects.filter(member=self.member).count(), 0)

    def test_one_tap_workout_is_refused_by_default(self):
        r = self._activity({'kind': 'fitness'})
        self.assertEqual(r.status_code, 400)
        self.member.refresh_from_db()
        self.assertEqual(self.member.points_balance, 0)
        self.assertEqual(CustomerActivity.objects.filter(member=self.member).count(), 0)

    @override_settings(NEXUS_MANUAL_STEP_POINTS_ENABLED=True)
    def test_legacy_switch_can_reopen_typed_steps(self):
        r = self._activity({'kind': 'steps', 'steps': 12000})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['pointsAwarded'], 3)   # legacy cap
        r2 = self._activity({'kind': 'fitness'})
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r2.data['pointsAwarded'], 2)

    # --- synced steps must count as "real activity" for the quest + streak ---
    def test_synced_steps_complete_the_quest_activity_step(self):
        from .nexus_growth import quest_state
        self.assertFalse(quest_state(self.member)['steps'][0]['done'])
        self.assertEqual(self._sync({'steps': 5000}, self.device).status_code, 200)
        self.assertTrue(quest_state(self.member)['steps'][0]['done'])

    def test_synced_steps_count_toward_the_streak(self):
        from .nexus_growth import streak_state
        self.assertFalse(streak_state(self.member)['activeToday'])
        self.assertEqual(self._sync({'steps': 5000}, self.device).status_code, 200)
        s = streak_state(self.member)
        self.assertTrue(s['activeToday'])
        self.assertEqual(s['days'], 1)

    # --- logout kills the device token --------------------------------------
    def test_logout_revokes_device_token(self):
        self.assertEqual(self._sync({'steps': 5000}, self.device).status_code, 200)
        self.client.post(LOGOUT, {}, format='json',
                         HTTP_AUTHORIZATION=f'Bearer {self.session}')
        self.assertTrue(
            CustomerDeviceToken.objects.get(member=self.member).revoked)
        r = self._sync({'steps': 6000}, self.device)     # token now dead
        self.assertEqual(r.status_code, 401)


class HealthPointsNeverSetPriceTests(APITestCase):
    """Google Play forbids health data → insurance pricing. Prove that health
    (step) points, however high, never produce a premium discount."""

    def setUp(self):
        self.member = RewardMember.objects.create(
            customer_name='D member', email='disc@gmail.com', is_active=True,
            points_balance=99999, tier='platinum')   # max wellness tier
        self.token = customer_auth.start_session(self.member)

    def test_rewards_discount_is_zero_despite_top_tier(self):
        # The premium discount is surfaced on the /rewards/ screen.
        r = self.client.get('/api/v1/rewards/customer/rewards/',
                            HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data.get('discount'), 0)

    def test_growth_savings_show_no_premium_discount(self):
        r = self.client.get('/api/v1/rewards/customer/growth/',
                            HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(r.status_code, 200)
        savings = r.data.get('savings') or {}
        self.assertEqual(savings.get('discountPct'), 0)
        self.assertIsNone(savings.get('monthlySaving'))
        self.assertIsNone(savings.get('nextTierPct'))   # no health-tier premium ladder
