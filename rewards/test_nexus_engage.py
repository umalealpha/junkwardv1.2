"""Teams, the screening voucher and referrals — the CFO's own pass conditions.

CFO 2026-09-08, approving ideas 6, 7 and 10. Each test below is one of the
criteria written into the goal contract before any code existed:
  - a team total that DOUBLE-COUNTS a member, or one member seeing another
    team's data, are the named failure modes for teams;
  - unlocking a screening WITHOUT the pulse checks, or twice in one year, are
    the named failure modes for the voucher;
  - a self-referral, a duplicate credit on replay, or ANY payout before the
    CFO sets the value, are the named failure modes for referrals.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from rewards import customer_auth, nexus_engage, nexus_teams, screening_unlock
from rewards.models import (
    HealthMetric, NexusTeam, NexusTeamMember, Referral, RewardMember,
    ScreeningVoucher,
)


def _member(email, name='Test Member'):
    return RewardMember.objects.create(customer_name=name, email=email, is_active=True,
                                       enrolled_at=timezone.localdate())


class _Base(APITestCase):
    def setUp(self):
        self.me = _member('me@example.com', 'Mpho Kgosi')
        self.friend = _member('friend@example.com', 'Naledi Moeng')
        self.outsider = _member('outsider@example.com', 'Someone Else')
        self.tok = {m.email: customer_auth.start_session(m)
                    for m in (self.me, self.friend, self.outsider)}

    def _get(self, url, who):
        return self.client.get(url, HTTP_AUTHORIZATION=f'Bearer {self.tok[who.email]}')

    def _post(self, url, who, body=None):
        return self.client.post(url, body or {}, format='json',
                                HTTP_AUTHORIZATION=f'Bearer {self.tok[who.email]}')


class TeamTests(_Base):
    URL = '/api/v1/rewards/customer/team/'
    JOIN = '/api/v1/rewards/customer/team/join/'
    LEAVE = '/api/v1/rewards/customer/team/leave/'

    def test_a_signed_out_caller_gets_nothing(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)
        self.assertEqual(self.client.post(self.JOIN, {'code': 'X'}, format='json').status_code, 401)

    def test_no_team_yet_says_so_plainly(self):
        r = self._get(self.URL, self.me)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['inTeam'])
        self.assertTrue(r.json()['label'])

    def test_creating_a_team_puts_me_in_it_with_a_join_code(self):
        r = self._post(self.URL, self.me, {'name': 'Team Kgosi'})
        self.assertEqual(r.status_code, 201)
        d = r.json()
        self.assertTrue(d['inTeam'])
        self.assertEqual(d['teamName'], 'Team Kgosi')
        self.assertEqual(d['size'], 1)
        self.assertTrue(d['joinCode'])
        self.assertEqual(NexusTeamMember.objects.filter(member=self.me).count(), 1)

    def test_a_team_needs_a_name(self):
        r = self._post(self.URL, self.me, {'name': '   '})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(NexusTeam.objects.count(), 0)

    def test_a_friend_joins_with_the_code_and_both_see_the_same_total(self):
        code = self._post(self.URL, self.me, {'name': 'Family'}).json()['joinCode']
        r = self._post(self.JOIN, self.friend, {'code': code})
        self.assertEqual(r.status_code, 200)
        mine = self._get(self.URL, self.me).json()
        theirs = self._get(self.URL, self.friend).json()
        self.assertEqual(mine['size'], 2)
        self.assertEqual(theirs['size'], 2)
        self.assertEqual(mine['totalPoints'], theirs['totalPoints'])
        self.assertEqual(mine['teamName'], theirs['teamName'])

    def test_the_code_works_in_lower_case_with_spaces(self):
        code = self._post(self.URL, self.me, {'name': 'Family'}).json()['joinCode']
        r = self._post(self.JOIN, self.friend, {'code': f'  {code.lower()} '})
        self.assertEqual(r.status_code, 200)

    def test_an_outsider_never_sees_the_team(self):
        # The named failure mode. There is no read-by-id path at all, so the
        # only thing an outsider can ask for is their OWN (empty) team.
        code = self._post(self.URL, self.me, {'name': 'Family'}).json()['joinCode']
        self._post(self.JOIN, self.friend, {'code': code})
        r = self._get(self.URL, self.outsider)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['inTeam'])
        self.assertNotIn('Family', r.content.decode())

    def test_a_bad_code_is_refused(self):
        r = self._post(self.JOIN, self.friend, {'code': 'NOPE99'})
        self.assertEqual(r.status_code, 400)

    def test_i_cannot_be_in_two_teams(self):
        self._post(self.URL, self.me, {'name': 'First'})
        other = self._post(self.URL, self.friend, {'name': 'Second'}).json()['joinCode']
        r = self._post(self.JOIN, self.me, {'code': other})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(NexusTeamMember.objects.filter(member=self.me).count(), 1)

    def test_creating_a_second_team_is_refused(self):
        self._post(self.URL, self.me, {'name': 'First'})
        r = self._post(self.URL, self.me, {'name': 'Second'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(NexusTeam.objects.count(), 1)

    def test_the_capacity_check_locks_the_team_row(self):
        """Fable, 2026-09-09: the original 'lock' was a plain SELECT COUNT(*).

        Django strips FOR UPDATE from every aggregate, and locking existing
        child rows would not block an INSERT anyway — so two friends could both
        take the last seat and the team reached 7. The parent row must be
        locked, and this asserts the SQL that proves it.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        code = self._post(self.URL, self.me, {'name': 'Locked'}).json()['joinCode']
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self._post(self.JOIN, self.friend, {'code': code}).status_code, 200)
        locking = [q for q in ctx.captured_queries
                   if 'rewards_nexusteam' in q['sql'].lower() and 'for update' in q['sql'].lower()]
        self.assertTrue(locking, 'the team row was never locked before counting seats')

    def test_a_full_team_turns_the_next_person_away(self):
        code = self._post(self.URL, self.me, {'name': 'Full'}).json()['joinCode']
        for i in range(nexus_teams.MAX_TEAM_SIZE - 1):
            m = _member(f'extra{i}@example.com')
            self.tok[m.email] = customer_auth.start_session(m)
            self.assertEqual(self._post(self.JOIN, m, {'code': code}).status_code, 200)
        r = self._post(self.JOIN, self.outsider, {'code': code})
        self.assertEqual(r.status_code, 400)
        self.assertIn('full', r.json()['detail'].lower())

    def test_leaving_is_idempotent_and_frees_me_to_join_again(self):
        code = self._post(self.URL, self.me, {'name': 'Family'}).json()['joinCode']
        self._post(self.JOIN, self.friend, {'code': code})
        self.assertEqual(self._post(self.LEAVE, self.friend).status_code, 200)
        self.assertFalse(self._post(self.LEAVE, self.friend).json()['inTeam'])
        self.assertEqual(self._post(self.JOIN, self.friend, {'code': code}).status_code, 200)

    def test_team_totals_add_up_from_one_trip_query(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from rewards.models import CustomerDriveTrip
        code = self._post(self.URL, self.me, {'name': 'Family'}).json()['joinCode']
        self._post(self.JOIN, self.friend, {'code': code})
        for m, km in ((self.me, 12.5), (self.friend, 7.5)):
            CustomerDriveTrip.objects.create(
                member=m, started_at=timezone.now(), distance_km=km, duration_min=20,
                idle_minutes=0, harsh_events=0, max_speed=70, score=90, points_awarded=0)
        with CaptureQueriesContext(connection) as ctx:
            d = self._get(self.URL, self.me).json()
        self.assertEqual(d['totalKm'], 20.0)
        self.assertEqual(d['totalTrips'], 2)
        # Panel finding K5: this used to run one trip query PER teammate. One
        # GROUP BY covers the whole team however many people are in it.
        trip_queries = [q for q in ctx.captured_queries
                        if 'customerdrivetrip' in q['sql'].lower()]
        self.assertEqual(len(trip_queries), 1,
                         f'expected ONE trip query, got {len(trip_queries)}')

    def test_a_teammate_is_never_counted_twice(self):
        code = self._post(self.URL, self.me, {'name': 'Family'}).json()['joinCode']
        self._post(self.JOIN, self.friend, {'code': code})
        self._post(self.JOIN, self.friend, {'code': code})     # second attempt
        self.assertEqual(self._get(self.URL, self.me).json()['size'], 2)


class ScreeningTests(_Base):
    URL = '/api/v1/rewards/customer/screening/'
    CLAIM = '/api/v1/rewards/customer/screening/claim/'

    def _scans(self, member, n):
        for i in range(n):
            HealthMetric.objects.create(member=member, date=date(2026, 1, 1) + timedelta(days=i),
                                        resting_hr=62, steps=0, points_awarded=0)

    def test_a_signed_out_caller_cannot_claim(self):
        self.assertEqual(self.client.post(self.CLAIM).status_code, 401)
        self.assertEqual(ScreeningVoucher.objects.count(), 0)

    def test_locked_one_scan_short(self):
        self._scans(self.me, screening_unlock.REQUIRED_SCANS - 1)
        d = self._get(self.URL, self.me).json()
        self.assertFalse(d['unlocked'])
        self.assertEqual(d['remaining'], 1)
        self.assertEqual(d['scansDone'], screening_unlock.REQUIRED_SCANS - 1)

    def test_claiming_without_the_scans_is_refused_and_writes_nothing(self):
        self._scans(self.me, screening_unlock.REQUIRED_SCANS - 1)
        r = self._post(self.CLAIM, self.me)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ScreeningVoucher.objects.count(), 0)

    def test_the_required_scan_unlocks_and_issues_one_voucher(self):
        self._scans(self.me, screening_unlock.REQUIRED_SCANS)
        self.assertTrue(self._get(self.URL, self.me).json()['unlocked'])
        r = self._post(self.CLAIM, self.me)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(ScreeningVoucher.objects.filter(member=self.me).count(), 1)
        d = r.json()
        self.assertIsNotNone(d['voucher'])
        self.assertTrue(d['voucher']['code'])
        self.assertTrue(d['voucher']['expiresOn'])

    def test_a_second_claim_in_the_same_year_is_refused(self):
        # The named failure mode: two screenings in one year.
        self._scans(self.me, screening_unlock.REQUIRED_SCANS + 5)
        self.assertEqual(self._post(self.CLAIM, self.me).status_code, 201)
        r = self._post(self.CLAIM, self.me)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ScreeningVoucher.objects.filter(member=self.me).count(), 1)

    def test_the_next_screening_must_be_earned_again(self):
        """CFO ruling 2026-09-09: earn it again each year, not once for ever.

        Counting all-time scans meant one burst of scanning bought a free
        screening every following year with no further checks.
        """
        # Last year: ten checks, then a voucher issued AFTER them, long enough
        # ago that the 365-day window has passed.
        old_start = timezone.localdate() - timedelta(days=500)
        for i in range(screening_unlock.REQUIRED_SCANS):
            HealthMetric.objects.create(member=self.me, date=old_start + timedelta(days=i),
                                        resting_hr=64, steps=0, points_awarded=0)
        issued = timezone.localdate() - timedelta(days=400)
        ScreeningVoucher.objects.create(
            member=self.me, code='OLDVCH01', issued_on=issued,
            expires_on=issued + timedelta(days=90), scans_at_issue=screening_unlock.REQUIRED_SCANS)

        # This year, with NO new checks, the count is back to zero.
        d = self._get(self.URL, self.me).json()
        self.assertEqual(d['scansDone'], 0, 'old checks must not count again')
        self.assertFalse(d['unlocked'])
        self.assertEqual(d['remaining'], screening_unlock.REQUIRED_SCANS)
        self.assertEqual(self._post(self.CLAIM, self.me).status_code, 400)

        # Ten FRESH checks earn the next one.
        for i in range(screening_unlock.REQUIRED_SCANS):
            HealthMetric.objects.create(member=self.me, date=timezone.localdate() - timedelta(days=i),
                                        resting_hr=60, steps=0, points_awarded=0)
        d = self._get(self.URL, self.me).json()
        self.assertEqual(d['scansDone'], screening_unlock.REQUIRED_SCANS)
        self.assertTrue(d['unlocked'])
        self.assertEqual(self._post(self.CLAIM, self.me).status_code, 201)
        self.assertEqual(ScreeningVoucher.objects.filter(member=self.me).count(), 2)
        # ...and the new voucher records the FRESH checks that earned it.
        newest = ScreeningVoucher.objects.exclude(code='OLDVCH01').get(member=self.me)
        self.assertEqual(newest.scans_at_issue, screening_unlock.REQUIRED_SCANS)

    def test_the_screen_says_when_the_next_one_is_due(self):
        self._scans(self.me, screening_unlock.REQUIRED_SCANS)
        self._post(self.CLAIM, self.me)
        d = self._get(self.URL, self.me).json()
        self.assertFalse(d['unlocked'])
        self.assertIsNotNone(d['nextEligible'])

    def test_one_members_scans_never_unlock_anothers_voucher(self):
        self._scans(self.friend, screening_unlock.REQUIRED_SCANS + 5)
        self.assertFalse(self._get(self.URL, self.me).json()['unlocked'])
        self.assertEqual(self._post(self.CLAIM, self.me).status_code, 400)

    def test_a_voucher_carries_no_money_value(self):
        self._scans(self.me, screening_unlock.REQUIRED_SCANS)
        body = self._post(self.CLAIM, self.me).content.decode().lower()
        for word in ('pula', 'bwp', 'amount', 'price', 'cost'):
            self.assertNotIn(word, body)


class ReferralTests(_Base):
    URL = '/api/v1/rewards/customer/referral/'
    OTP = '/api/v1/rewards/customer/request-otp/'

    def test_every_member_has_a_stable_code(self):
        first = self._get(self.URL, self.me).json()['code']
        second = self._get(self.URL, self.me).json()['code']
        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertNotEqual(first, self._get(self.URL, self.friend).json()['code'])

    def test_the_screen_says_the_reward_waits_for_an_active_policy(self):
        d = self._get(self.URL, self.me).json()
        self.assertIn('active', d['label'].lower())
        self.assertEqual(d['friendsJoined'], 0)

    def test_signing_up_on_a_code_records_the_referral_and_pays_nothing(self):
        code = self._get(self.URL, self.me).json()['code']
        r = self.client.post(self.OTP, {'email': 'newjoiner@example.com', 'referralCode': code},
                             format='json')
        self.assertEqual(r.status_code, 200)
        joined = RewardMember.objects.get(email='newjoiner@example.com')
        ref = Referral.objects.get(referred=joined)
        self.assertEqual(ref.referrer_id, self.me.id)
        # 🔴 Nothing may be paid at signup.
        self.assertEqual(ref.points_awarded, 0)
        self.assertIsNone(ref.qualified_at)
        self.me.refresh_from_db()
        joined.refresh_from_db()
        self.assertEqual(self.me.points_balance, 0)
        self.assertEqual(joined.points_balance, 0)

    def test_a_bad_code_still_lets_the_person_join(self):
        r = self.client.post(self.OTP, {'email': 'nocode@example.com', 'referralCode': 'ZZZZZZZZ'},
                             format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(RewardMember.objects.filter(email='nocode@example.com').exists())
        self.assertEqual(Referral.objects.count(), 0)

    def test_nobody_can_refer_themselves(self):
        code = self._get(self.URL, self.me).json()['code']
        self.assertFalse(nexus_engage.record_referral(self.me, code))
        self.assertEqual(Referral.objects.count(), 0)

    def test_the_same_member_cannot_be_referred_twice(self):
        code = self._get(self.URL, self.me).json()['code']
        other = self._get(self.URL, self.friend).json()['code']
        self.assertTrue(nexus_engage.record_referral(self.outsider, code))
        self.assertFalse(nexus_engage.record_referral(self.outsider, code))    # replay
        self.assertFalse(nexus_engage.record_referral(self.outsider, other))   # someone else
        self.assertEqual(Referral.objects.filter(referred=self.outsider).count(), 1)

    def test_one_member_may_refer_several_friends(self):
        code = self._get(self.URL, self.me).json()['code']
        for i in range(3):
            self.assertTrue(nexus_engage.record_referral(_member(f'f{i}@example.com'), code))
        self.assertEqual(self._get(self.URL, self.me).json()['friendsJoined'], 3)

    def test_the_yearly_cap_is_enforced(self):
        from rewards import provisional_rates
        code = self._get(self.URL, self.me).json()['code']
        cap = provisional_rates.REFERRAL_MAX_PER_MEMBER_PER_YEAR
        for i in range(cap):
            self.assertTrue(nexus_engage.record_referral(_member(f'c{i}@example.com'), code))
        self.assertFalse(nexus_engage.record_referral(_member('over@example.com'), code))
        self.assertEqual(Referral.objects.filter(referrer=self.me).count(), cap)

    def test_no_money_value_is_ever_returned(self):
        body = self._get(self.URL, self.me).content.decode().lower()
        for word in ('pula', 'bwp', 'discount', 'premium'):
            self.assertNotIn(word, body)
