"""
hris/tests/test_leave_exceptions_read_only.py

Tests for the READ-ONLY Time Doctor guard visibility endpoint
(CFO 2026-09-18, Easy PR E: "Time Doctor visibility"). Every test proves ONE
promise the endpoint makes:

  - a manager sees the current rows for their reports;
  - a manager does NOT see other teams' rows;
  - a plain employee sees ONLY their own row;
  - the endpoint never triggers a deduction (no LeaveRequest / no
    WorkdayJustification write happens during a GET);
  - only GET is allowed (POST / PATCH / DELETE all 405);
  - the response's `guard_state` field mirrors the service's existing
    `status` values verbatim — no new states are invented.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from hris.models import HRISProfile, LeaveRequest, TrackingDirective, WorkdayJustification
from integrations.models import TimeDoctorDailySnapshot
from payroll.models import Employee


DAY = date(2026, 9, 17)


def _td_row(prof, uid, productive, tracked=None):
    return {
        'user_id': uid,
        'name': prof.employee.full_name,
        'email': prof.employee.email,
        'hours_tracked': productive if tracked is None else tracked,
        'productive_hours': productive,
        'productive_pct': 0,
        'tracked_today': True,
    }


@override_settings(WORKFORCE_DATA_GUARD_AI=False)
class LeaveExceptionsReadOnlyTests(APITestCase):
    """The endpoint is `hris:api-leave-exceptions-read`; a plain GET on the
    latest snapshot day drives every scope-and-safety promise."""

    @classmethod
    def setUpTestData(cls):
        # A manager, their report, an unrelated team member, and an HR user.
        cls.mgr_user = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw')
        cls.rep_user = User.objects.create_user('rep', email='rep@alphadirect.co.bw')
        cls.other_user = User.objects.create_user('other', email='other@alphadirect.co.bw')
        cls.hr_user = User.objects.create_user('hr', email='ubutale@alphadirect.co.bw')

        cls.mgr = Employee.objects.create(
            employee_number='M1', full_name='Line Manager',
            email='mgr@alphadirect.co.bw', status='active', user=cls.mgr_user)
        cls.rep = Employee.objects.create(
            employee_number='R1', full_name='Direct Report',
            email='rep@alphadirect.co.bw', status='active', user=cls.rep_user)
        cls.other = Employee.objects.create(
            employee_number='O1', full_name='Other Team Person',
            email='other@alphadirect.co.bw', status='active', user=cls.other_user)

        # Profiles; the manager has one direct report + one unrelated employee.
        cls.mgr_prof = HRISProfile.objects.create(employee=cls.mgr)
        cls.rep_prof = HRISProfile.objects.create(employee=cls.rep, manager=cls.mgr)
        cls.other_prof = HRISProfile.objects.create(employee=cls.other)   # no manager

        # Everyone we care about is tracking-eligible.
        for e in (cls.mgr, cls.rep, cls.other):
            TrackingDirective.objects.create(employee=e, expected_to_track=True)

        # A Time Doctor snapshot with low/no productive hours for all three so
        # they all appear on the guard feed for DAY.
        TimeDoctorDailySnapshot.objects.create(
            company_id='test-co', as_of=DAY, totals={},
            payload=[
                _td_row(cls.mgr_prof, 'uid-mgr', 0.5),
                _td_row(cls.rep_prof, 'uid-rep', 0.5),
                _td_row(cls.other_prof, 'uid-other', 0.5),
            ])

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('hris:api-leave-exceptions-read'),
                               {'days': 1})

    # ── SCOPE ────────────────────────────────────────────────────────────────
    def test_get_returns_current_rows_for_a_manager(self):
        resp = self._get(self.mgr_user)
        self.assertEqual(resp.status_code, 200, resp.content)
        rows = [r for d in resp.json()['days'] for r in d['rows']]
        names = {r['employee'] for r in rows}
        self.assertIn('Direct Report', names)   # the manager sees their report
        self.assertEqual(resp.json()['scope'], 'manager')

    def test_get_scopes_to_direct_reports_for_non_hr(self):
        # The manager MUST NOT see the unrelated team member.
        resp = self._get(self.mgr_user)
        rows = [r for d in resp.json()['days'] for r in d['rows']]
        names = {r['employee'] for r in rows}
        self.assertNotIn('Other Team Person', names)

    def test_get_excludes_admin_data_for_a_plain_employee(self):
        # A plain employee (no reports, not HR) sees only their own row.
        resp = self._get(self.other_user)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['scope'], 'self')
        rows = [r for d in body['days'] for r in d['rows']]
        names = {r['employee'] for r in rows}
        self.assertEqual(names, {'Other Team Person'})

    # ── SAFETY ───────────────────────────────────────────────────────────────
    def test_get_never_triggers_a_deduction(self):
        # A GET must not raise a LeaveRequest or write a WorkdayJustification.
        # If the guard ever ran off the read path both counts would go up.
        before_lr = LeaveRequest.objects.count()
        before_wj = WorkdayJustification.objects.count()
        resp = self._get(self.hr_user)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(LeaveRequest.objects.count(), before_lr)
        self.assertEqual(WorkdayJustification.objects.count(), before_wj)

    def test_endpoint_is_read_only(self):
        # Only GET is exposed. POST / PATCH / DELETE all 405.
        self.client.force_login(self.hr_user)
        url = reverse('hris:api-leave-exceptions-read')
        self.assertEqual(self.client.post(url, {}).status_code, 405)
        self.assertEqual(self.client.patch(url, {}).status_code, 405)
        self.assertEqual(self.client.delete(url).status_code, 405)

    # ── SHAPE ────────────────────────────────────────────────────────────────
    def test_response_marks_preview_vs_deducted_clearly(self):
        # `guard_state` MUST equal the row's existing `status` field verbatim —
        # do not invent new states. HR is the widest scope, so we assert on
        # every row it returns.
        resp = self._get(self.hr_user)
        rows = [r for d in resp.json()['days'] for r in d['rows']]
        self.assertTrue(rows, 'the HR view returned no rows to check')
        for r in rows:
            self.assertIn('guard_state', r)
            self.assertEqual(r['guard_state'], r['status'])
