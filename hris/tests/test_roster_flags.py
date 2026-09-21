"""Tests for roster flags — "not reporting to me" / "resigned" (CFO 2026-07-26).

Origin: Kago's test of the Monthly Manager Return showed Milidzani Muzila on his
roster when she is no longer his, and Shane Thabo Khupe had left the company
without anyone noticing. Every manager now gets a one-click way to say so.

The load-bearing design point (CFO correction 2026-07-26): a flagged person does
NOT disappear from the roster. They stay visible, marked as waiting on Unami, and
UNAMI makes the decision. Letting a manager's own click remove someone would let
anyone drop an inconvenient person out of their own accountability. Raising also
must NOT move the reporting line or end anyone's employment — moving a line changes
leave routing, and a resignation touches payroll. These tests pin that boundary.
"""
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, UserCompanyAccess, UserProfile
from hris.manager_return_service import team_profiles
from hris.models import HRISProfile
from hris.roster_flag_models import (
    FlagKind, FlagStatus, RosterFlag, can_decide, open_flags_for,
)
from payroll.models import Employee


@override_settings(ELRA_PERF_ENABLED=True)
class RosterFlagTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='RF', name='Roster Flag Co.')
        cls.mgr_user = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw')
        cls.other_user = User.objects.create_user('other', email='other@alphadirect.co.bw')
        # Unami decides roster flags (CFO 2026-07-26).
        cls.unami_user = User.objects.create_user(
            'ubutale', email='ubutale@alphadirect.co.bw')
        cls.hr_user = cls.unami_user
        # A plain HR admin who is NOT Unami — may look, may not decide.
        cls.hr_admin = User.objects.create_user('hradmin', email='hradmin@alphadirect.co.bw')
        UserProfile.objects.create(user=cls.hr_admin, is_administrator=True)
        # Entity grants. These used to be unnecessary because a user with NO
        # grant was treated as "may see every company" — the SEC-02 hole. The
        # real Unami is unrestricted and the real HR team hold 12 grants each,
        # so the fixture was the odd one out: it was passing BECAUSE of the
        # hole. Grant explicitly, as production does (CFO 2026-08-08).
        for _u in (cls.unami_user, cls.hr_admin):
            UserCompanyAccess.objects.get_or_create(
                user=_u, company=cls.co, defaults={'can_view': True})

        cls.mgr = Employee.objects.create(
            employee_number='M1', full_name='The Manager', company=cls.co,
            email='mgr@alphadirect.co.bw', user=cls.mgr_user,
            department='Finance', job_title='Manager - Finance & Planning')
        cls.other = Employee.objects.create(
            employee_number='O1', full_name='Other Manager', company=cls.co,
            email='other@alphadirect.co.bw', user=cls.other_user)
        cls.leaver = Employee.objects.create(
            employee_number='L1', full_name='The Leaver', company=cls.co,
            department='Finance', job_title='Assistant Accountant')
        cls.stayer = Employee.objects.create(
            employee_number='S1', full_name='The Stayer', company=cls.co,
            department='Finance', job_title='Junior Associate')

        cls.leaver_profile = HRISProfile.objects.create(
            employee=cls.leaver, manager=cls.mgr)
        cls.stayer_profile = HRISProfile.objects.create(
            employee=cls.stayer, manager=cls.mgr)

    def _raise(self, user, profile, kind, note=''):
        self.client.force_authenticate(user)
        return self.client.post(reverse('hris:api-roster-flags'), {
            'profile_id': str(profile.id), 'kind': kind, 'note': note}, format='json')

    # ── raising ─────────────────────────────────────────────────────────────
    def test_manager_can_flag_not_my_report(self):
        r = self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(RosterFlag.objects.count(), 1)

    def test_flagged_person_STAYS_on_the_roster(self):
        """CFO correction: they do not disappear. A manager must not be able to
        drop someone out of their own accountability with one click."""
        self.assertEqual(team_profiles(self.mgr, raiser_user=self.mgr_user).count(), 2)
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        names = sorted(p.employee.full_name for p in
                       team_profiles(self.mgr, raiser_user=self.mgr_user))
        self.assertEqual(names, ['The Leaver', 'The Stayer'])

    def test_the_roster_row_is_marked_as_waiting_on_unami(self):
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        marks = open_flags_for(self.mgr_user, [self.leaver_profile.id,
                                               self.stayer_profile.id])
        self.assertIn(str(self.leaver_profile.id), marks)
        self.assertNotIn(str(self.stayer_profile.id), marks)
        # CFO 2026-09-05: Dorothy decides alongside Unami, so the row says HR.
        self.assertEqual(marks[str(self.leaver_profile.id)]['waiting_on'],
                         'HR (Unami or Dorothy)')

    def test_raising_puts_a_decision_task_on_unamis_dashboard(self):
        from core.models import OmniTask
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        task = OmniTask.objects.filter(assignee=self.unami_user).first()
        self.assertIsNotNone(task, 'no decision task was created for Unami')
        self.assertIn('The Leaver', task.title)
        self.assertIn('STAYS', task.body)
        # OmniTask.source is varchar(30) — a longer tag silently killed the task
        # and Unami was never told. Pin the length.
        self.assertLessEqual(len(task.source), 30)
        self.assertTrue(task.source.startswith('rflag:'))

    def test_the_org_chart_is_NOT_changed_by_a_flag(self):
        """The whole safety point: a manager's click is evidence, not an edit."""
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        self.leaver_profile.refresh_from_db()
        self.leaver.refresh_from_db()
        self.assertEqual(self.leaver_profile.manager_id, self.mgr.id)   # unchanged
        self.assertEqual(self.leaver.status, Employee.Status.ACTIVE)    # still employed

    def test_a_resignation_flag_does_not_terminate_anyone(self):
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.RESIGNED,
                    note='Last day was 18 July.')
        self.leaver.refresh_from_db()
        self.assertEqual(self.leaver.status, Employee.Status.ACTIVE)
        self.assertIsNone(self.leaver.termination_date)

    def test_resignation_requires_a_note(self):
        r = self._raise(self.mgr_user, self.leaver_profile, FlagKind.RESIGNED)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(RosterFlag.objects.count(), 0)

    def test_other_requires_a_note(self):
        r = self._raise(self.mgr_user, self.leaver_profile, FlagKind.OTHER)
        self.assertEqual(r.status_code, 400)

    def test_clicking_twice_does_not_make_two_flags(self):
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        r = self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        self.assertEqual(r.status_code, 200)          # returns the existing one
        self.assertEqual(RosterFlag.objects.count(), 1)

    def test_a_manager_cannot_flag_someone_elses_report(self):
        r = self._raise(self.other_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(RosterFlag.objects.count(), 0)

    def test_bad_reason_is_rejected_with_the_valid_choices(self):
        r = self._raise(self.mgr_user, self.leaver_profile, 'because')
        self.assertEqual(r.status_code, 400)
        self.assertIn('choices', r.json())

    def test_a_flag_never_shrinks_anyone_elses_view_either(self):
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        self.assertEqual(team_profiles(self.mgr, raiser_user=self.hr_user).count(), 2)
        self.assertEqual(team_profiles(self.mgr).count(), 2)

    def test_only_unami_and_the_cfo_may_decide(self):
        self.assertTrue(can_decide(self.unami_user))
        self.assertFalse(can_decide(self.mgr_user))
        self.assertFalse(can_decide(self.hr_admin))     # HR admin != the decider

    # ── HR actioning ────────────────────────────────────────────────────────
    def test_unami_can_action_a_flag(self):
        rid = self._raise(self.mgr_user, self.leaver_profile,
                          FlagKind.NOT_MY_REPORT).json()['id']
        self.client.force_authenticate(self.hr_user)
        r = self.client.post(reverse('hris:api-roster-flags-decide', args=[rid]),
                             {'action': 'action', 'hr_note': 'Moved to Operations.'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(RosterFlag.objects.get(pk=rid).status, FlagStatus.ACTIONED)

    def test_a_manager_cannot_action_their_own_flag(self):
        rid = self._raise(self.mgr_user, self.leaver_profile,
                          FlagKind.NOT_MY_REPORT).json()['id']
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(reverse('hris:api-roster-flags-decide', args=[rid]),
                             {'action': 'action'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(RosterFlag.objects.get(pk=rid).status, FlagStatus.OPEN)

    def test_rejecting_needs_a_reason(self):
        rid = self._raise(self.mgr_user, self.leaver_profile,
                          FlagKind.NOT_MY_REPORT).json()['id']
        self.client.force_authenticate(self.hr_user)
        r = self.client.post(reverse('hris:api-roster-flags-decide', args=[rid]),
                             {'action': 'reject'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_cannot_action_the_same_flag_twice(self):
        rid = self._raise(self.mgr_user, self.leaver_profile,
                          FlagKind.NOT_MY_REPORT).json()['id']
        self.client.force_authenticate(self.hr_user)
        url = reverse('hris:api-roster-flags-decide', args=[rid])
        self.client.post(url, {'action': 'action', 'hr_note': 'done'}, format='json')
        r = self.client.post(url, {'action': 'action', 'hr_note': 'again'}, format='json')
        self.assertEqual(r.status_code, 409)

    def test_once_decided_the_row_marker_clears(self):
        rid = self._raise(self.mgr_user, self.leaver_profile,
                          FlagKind.NOT_MY_REPORT).json()['id']
        self.assertTrue(open_flags_for(self.mgr_user))
        RosterFlag.objects.filter(pk=rid).update(status=FlagStatus.ACTIONED)
        self.assertFalse(open_flags_for(self.mgr_user))

    def test_a_plain_hr_admin_cannot_decide(self):
        rid = self._raise(self.mgr_user, self.leaver_profile,
                          FlagKind.NOT_MY_REPORT).json()['id']
        self.client.force_authenticate(self.hr_admin)
        r = self.client.post(reverse('hris:api-roster-flags-decide', args=[rid]),
                             {'action': 'action', 'hr_note': 'ok'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(RosterFlag.objects.get(pk=rid).status, FlagStatus.OPEN)

    # ── listing ─────────────────────────────────────────────────────────────
    def test_manager_sees_only_their_own_flags_unami_sees_all_open(self):
        self._raise(self.mgr_user, self.leaver_profile, FlagKind.NOT_MY_REPORT)
        self.client.force_authenticate(self.mgr_user)
        mine = self.client.get(reverse('hris:api-roster-flags')).json()['flags']
        self.assertEqual(len(mine), 1)
        self.client.force_authenticate(self.hr_user)
        allopen = self.client.get(reverse('hris:api-roster-flags')).json()['flags']
        self.assertEqual(len(allopen), 1)
        self.assertEqual(allopen[0]['employee'], 'The Leaver')

    def test_model_rejects_other_without_a_note(self):
        f = RosterFlag(profile=self.leaver_profile, raised_by=self.mgr_user,
                       kind=FlagKind.OTHER, note='')
        with self.assertRaises(ValidationError):
            f.clean()
