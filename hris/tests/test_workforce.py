"""
hris/tests/test_workforce.py

Tests for the Workforce Daily Brief rules (CFO 2026-07-14):
  - required hours per day (weekday 6.5 / Saturday 3 from 2026-08-01, 4 before /
    Sunday 0 / holidays)
  - met / justified / unjustified classification
  - external-meeting 1.5h cap
  - holiday_off_dates + required-hours integration with PublicHoliday
  - brief HTML renders for each status

Run:  python manage.py test hris.tests.test_workforce
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from hris import workforce, workforce_brief
from hris.models import PublicHoliday

# A fixed, holiday-free reference week: 2026-07-13 is an ordinary Monday (NOT a
# Botswana public holiday — 2026-07-20 is President's Day, so it is deliberately
# avoided). These drive the weekday-hours maths (6.5h weekday, Sat 3h, Sun 0h)
# and the holiday test, none of which depend on today's date. The only
# date-relative test — the /my-brief 14-day window — uses its own rolling date
# (see JustifyDayViewTests.WORK) instead of these.
MON = datetime.date(2026, 7, 13)   # Monday
SAT = datetime.date(2026, 7, 18)   # Saturday
SUN = datetime.date(2026, 7, 19)   # Sunday
# The 3h Saturday takes effect 2026-08-01, so the post-policy week is a separate
# pair of fixtures — 2026-08-03 is an ordinary Monday, 2026-08-08 its Saturday.
MON_NEW = datetime.date(2026, 8, 3)
SAT_NEW = datetime.date(2026, 8, 8)


class RequiredHoursTests(SimpleTestCase):
    def test_weekday_is_6_5(self):
        self.assertEqual(workforce.base_required_hours(MON), Decimal('6.5'))

    def test_saturday_is_3_from_the_policy_date(self):
        self.assertEqual(workforce.base_required_hours(SAT_NEW), Decimal('3'))

    def test_saturday_before_the_policy_date_is_still_4(self):
        # Effective-dated on purpose: a re-run over July must judge people
        # against the 4h that was actually asked of them (the July audit).
        self.assertEqual(workforce.base_required_hours(SAT), Decimal('4'))

    def test_sunday_is_zero(self):
        self.assertEqual(workforce.base_required_hours(SUN), Decimal('0'))

    def test_off_holiday_zero(self):
        # A public holiday on which staff do NOT work → 0 even on a weekday.
        self.assertEqual(workforce.required_hours_for_date(MON, {MON}), Decimal('0'))

    def test_working_holiday_uses_normal_rule(self):
        # A holiday flagged as a working day is NOT in the off set → normal hours.
        self.assertEqual(workforce.required_hours_for_date(MON, set()), Decimal('6.5'))


class ManagerRequiredHoursTests(SimpleTestCase):
    """Managers / EXCO / FM carry a 4.5h weekday requirement (CFO 2026-07-23).
    Saturday (3h), Sunday (0h) and holidays (0h) are unchanged."""

    def test_manager_weekday_is_4_5(self):
        self.assertEqual(workforce.base_required_hours(MON, is_manager=True), Decimal('4.5'))

    def test_manager_saturday_matches_staff(self):
        self.assertEqual(workforce.base_required_hours(SAT_NEW, is_manager=True), Decimal('3'))
        self.assertEqual(workforce.base_required_hours(SAT, is_manager=True), Decimal('4'))

    def test_manager_sunday_still_zero(self):
        self.assertEqual(workforce.base_required_hours(SUN, is_manager=True), Decimal('0'))

    def test_manager_off_holiday_zero(self):
        self.assertEqual(workforce.required_hours_for_date(MON, {MON}, is_manager=True), Decimal('0'))

    def test_manager_weekday_via_required_for_date(self):
        self.assertEqual(workforce.required_hours_for_date(MON, set(), is_manager=True), Decimal('4.5'))

    def test_weekly_totals(self):
        # A JULY week still totals on the old 4h Saturday: staff 36.5, manager 26.5.
        self.assertEqual(workforce.weekly_required_hours(MON), Decimal('36.5'))
        self.assertEqual(workforce.weekly_required_hours(MON, is_manager=True), Decimal('26.5'))
        # An AUGUST week uses the new 3h Saturday: staff 35.5, manager 25.5.
        self.assertEqual(workforce.weekly_required_hours(MON_NEW), Decimal('35.5'))
        self.assertEqual(workforce.weekly_required_hours(MON_NEW, is_manager=True), Decimal('25.5'))
        # Mon–Fri (days=5, the badge target): staff 32.5; manager 22.5.
        self.assertEqual(workforce.weekly_required_hours(MON, is_manager=False, days=5), Decimal('32.5'))
        self.assertEqual(workforce.weekly_required_hours(MON, is_manager=True, days=5), Decimal('22.5'))


class ManagerDetectionTests(SimpleTestCase):
    """hris.workforce_roles.is_manager_hours_employee — the CLOSED set
    (CFO 2026-07-23): claims/underwriting manager, Chief* (EXCO), and the
    email allowlist (Bharath, Oprah the FM, C-suite). Nothing else."""

    @staticmethod
    def _emp(job_title='', email=''):
        from types import SimpleNamespace
        return SimpleNamespace(job_title=job_title, email=email)

    def test_claims_manager_title(self):
        from hris.workforce_roles import is_manager_hours_employee
        self.assertTrue(is_manager_hours_employee(self._emp(job_title='Claims Manager')))

    def test_underwriting_manager_title(self):
        from hris.workforce_roles import is_manager_hours_employee
        self.assertTrue(is_manager_hours_employee(self._emp(job_title='Underwriting Manager')))

    def test_chief_is_exco(self):
        from hris.workforce_roles import is_manager_hours_employee
        self.assertTrue(is_manager_hours_employee(self._emp(job_title='Chief Human Capital Officer')))

    def test_bharath_by_email(self):
        from hris.workforce_roles import is_manager_hours_employee
        self.assertTrue(is_manager_hours_employee(
            self._emp(job_title='Senior Sales and Marketing Manager',
                      email='bbalasubramanian@alphadirect.co.bw')))

    def test_fm_oprah_by_email(self):
        from hris.workforce_roles import is_manager_hours_employee
        self.assertTrue(is_manager_hours_employee(
            self._emp(job_title='', email='omogomotsi@alphadirect.co.bw')))

    def test_operations_manager_excluded(self):
        # "and that's all" — Operations/Parts/Senior managers stay on 6.5h.
        from hris.workforce_roles import is_manager_hours_employee
        self.assertFalse(is_manager_hours_employee(self._emp(job_title='Operations Manager')))
        self.assertFalse(is_manager_hours_employee(self._emp(job_title='Parts Manager')))
        self.assertFalse(is_manager_hours_employee(self._emp(job_title='Senior Manager')))

    def test_ordinary_staff_excluded(self):
        from hris.workforce_roles import is_manager_hours_employee
        self.assertFalse(is_manager_hours_employee(
            self._emp(job_title='Junior Claims Underwriter', email='someone@alphadirect.co.bw')))
        self.assertFalse(is_manager_hours_employee(None))

    def test_env_extends_allowlist(self):
        import os
        from unittest import mock
        from hris.workforce_roles import is_manager_hours_employee
        emp = self._emp(email='newperson@alphadirect.co.bw')
        self.assertFalse(is_manager_hours_employee(emp))
        with mock.patch.dict(os.environ, {'OMNI_HALFDAY_MANAGERS': 'newperson'}):
            self.assertTrue(is_manager_hours_employee(emp))


class ClassifyTests(SimpleTestCase):
    def test_met(self):
        self.assertEqual(workforce.classify_day(Decimal('7.5'), Decimal('8')), 'met')

    def test_exactly_met(self):
        self.assertEqual(workforce.classify_day(Decimal('7.5'), Decimal('7.5')), 'met')

    def test_justified_with_leave(self):
        self.assertEqual(
            workforce.classify_day(Decimal('7.5'), Decimal('4'), justified=Decimal('3.5')), 'justified')

    def test_unjustified(self):
        self.assertEqual(workforce.classify_day(Decimal('7.5'), Decimal('4')), 'unjustified')

    def test_not_required(self):
        self.assertEqual(workforce.classify_day(Decimal('0'), Decimal('0')), 'not_required')

    def test_shortfall(self):
        self.assertEqual(workforce.shortfall_hours(Decimal('7.5'), Decimal('5')), Decimal('2.5'))
        self.assertEqual(workforce.shortfall_hours(Decimal('7.5'), Decimal('9')), Decimal('0'))


class MeetingCapTests(SimpleTestCase):
    def test_within_cap(self):
        self.assertTrue(workforce.meeting_minutes_valid(90))
        self.assertTrue(workforce.meeting_minutes_valid(45))

    def test_over_cap_rejected(self):
        self.assertFalse(workforce.meeting_minutes_valid(91))
        self.assertFalse(workforce.meeting_minutes_valid(180))

    def test_zero_or_bad_rejected(self):
        for bad in (0, -10, None, 'x'):
            self.assertFalse(workforce.meeting_minutes_valid(bad))


class BriefHtmlTests(SimpleTestCase):
    def _brief(self, status, needs=False):
        return {
            'as_of': '2026-07-13', 'name': 'Test Employee',
            'required': Decimal('7.5'), 'tracked': Decimal('4.0'),
            'shortfall': Decimal('3.5'), 'status': status, 'needs_justification': needs,
            'leave': {'taken_month': Decimal('0'), 'pending_days': Decimal('0'), 'pending_count': 0},
            'tasks': [{'title': 'Do X', 'priority': 'High'}],
            'announcements': [{'category': 'Meeting', 'title': 'HR sync', 'body': '10am'}],
        }

    def test_renders_all_statuses(self):
        for st in ('met', 'not_required', 'justified', 'unjustified', 'no_data'):
            html = workforce_brief.build_brief_html(self._brief(st, needs=(st == 'unjustified')))
            self.assertIn('Alpha Direct — Daily Brief', html)
            self.assertIn('Test Employee', html)

    def test_unjustified_shows_action_block(self):
        html = workforce_brief.build_brief_html(self._brief('unjustified', needs=True))
        self.assertIn('Action needed', html)


class BriefToggleAccessTests(SimpleTestCase):
    """Only CFO / Arun / Arjun (or a superuser) may flip the switch."""
    def _user(self, email, superuser=False):
        class U:  # duck-typed user, no DB
            pass
        u = U(); u.email = email; u.is_superuser = superuser
        return u

    def test_allowed_admins(self):
        from hris.workforce_views import _can_toggle
        for e in ('pganesharajah@alphadirect.co.bw', 'aiyer@alphadirect.co.bw',
                  'arjuniyer@alphadirect.co.bw', 'PGaneSharajah@AlphaDirect.co.bw'):
            self.assertTrue(_can_toggle(self._user(e)), e)

    def test_others_denied(self):
        from hris.workforce_views import _can_toggle
        for e in ('someone@alphadirect.co.bw', 'ubutale@alphadirect.co.bw', ''):
            self.assertFalse(_can_toggle(self._user(e)), e)

    def test_superuser_allowed(self):
        from hris.workforce_views import _can_toggle
        self.assertTrue(_can_toggle(self._user('anyone@x.com', superuser=True)))


class ExcludeKeywordTests(SimpleTestCase):
    """Agent exclusion is WHOLE-WORD: 'intern' must not catch "Internal Audit",
    'commission' must not catch "Commissions Administrator" (Fable 2026-07-14)."""

    def _hit(self, text):
        from hris.eligibility import _exclude_patterns
        return any(p.search(text.lower()) for p in _exclude_patterns())

    def test_real_agents_excluded(self):
        for t in ('Sales Agent', 'Agents', 'Independent Broker', 'Intern', 'Commission Clerk'):
            self.assertTrue(self._hit(t), t)

    def test_staff_roles_not_excluded(self):
        for t in ('Internal Audit', 'International Desk', 'Commissions Administrator',
                  'Claims Handler', 'Finance'):
            self.assertFalse(self._hit(t), t)


class HolidayOffDatesTests(TestCase):
    def test_off_holiday_excluded_but_working_holiday_not(self):
        off = PublicHoliday.objects.create(
            country_code='BW', holiday_date=MON, name='Off Holiday', is_working_day=False)
        work = PublicHoliday.objects.create(
            country_code='BW', holiday_date=SAT, name='Working Holiday', is_working_day=True)
        off_dates = workforce_brief.holiday_off_dates('BW')
        self.assertIn(off.holiday_date, off_dates)
        self.assertNotIn(work.holiday_date, off_dates)
        # required hours: off holiday → 0; working holiday (Saturday) → 3
        self.assertEqual(workforce.required_hours_for_date(MON, off_dates), Decimal('0'))
        self.assertEqual(workforce.required_hours_for_date(SAT, off_dates), Decimal('4'))


class JustifyDayViewTests(TestCase):
    """End-to-end through the API: profile resolution (no is_active field on
    HRISProfile — Fable review), the REAL 1.5h meeting cap, leave linking and
    amount validation."""

    # The /my-brief endpoint only surfaces short days from the last 14 days (a
    # window relative to today), so this class needs an IN-WINDOW date, not the
    # fixed module MON. The justify view keys off the row's stored hours, not the
    # weekday, so this only has to be recent and in the past — 3 days back is
    # always inside the window and never collides with a seeded public holiday.
    WORK = datetime.date.today() - datetime.timedelta(days=3)

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        from payroll.models import Employee
        from hris.models import HRISProfile, WorkdayJustification
        cls.emp = Employee.objects.create(
            employee_number='WF-001', full_name='Test Worker',
            email='worker@alphadirect.co.bw')
        cls.prof = HRISProfile.objects.create(employee=cls.emp)
        cls.user = User.objects.create_user('worker', 'worker@alphadirect.co.bw', 'x')
        cls.row = WorkdayJustification.objects.create(
            profile=cls.prof, work_date=cls.WORK,
            required_hours=Decimal('7.5'), tracked_hours=Decimal('3.5'),
            status=WorkdayJustification.Status.UNJUSTIFIED)

    def _client(self):
        from rest_framework.test import APIClient
        c = APIClient()
        c.force_authenticate(self.user)
        return c

    def test_my_brief_resolves_profile(self):
        # Would 500 with the old is_active filter (field doesn't exist).
        r = self._client().get('/api/v1/timedoctor/my-brief/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['employee'], 'Test Worker')
        self.assertEqual(len(r.data['days']), 1)

    def test_meeting_cap_only_justifies_meeting_length(self):
        # 4h short, 90-min meeting → 1.5h justified, day stays UNJUSTIFIED.
        from hris.models import WorkdayJustification
        r = self._client().post('/api/v1/timedoctor/justify/', {
            'work_date': self.WORK.isoformat(), 'reason': 'external_meeting',
            'meeting_minutes': 90, 'justification': 'Broker meeting'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.justified_hours, Decimal('1.50'))
        self.assertEqual(self.row.status, WorkdayJustification.Status.UNJUSTIFIED)
        self.assertIsNotNone(self.row.responded_at)
        # ...and the pop-up must not nag again for a day already answered.
        r2 = self._client().get('/api/v1/timedoctor/my-brief/')
        self.assertEqual(len(r2.data['days']), 0)

    def test_meeting_covers_small_gap_parks_for_review(self):
        # A self-report that WOULD clear the day no longer self-clears — it
        # parks as EXPLAINED until a manager signs it off (Fable 2026-07-14).
        from hris.models import WorkdayJustification
        self.row.tracked_hours = Decimal('6.5')   # only 1h short
        self.row.save(update_fields=['tracked_hours'])
        r = self._client().post('/api/v1/timedoctor/justify/', {
            'work_date': self.WORK.isoformat(), 'reason': 'external_meeting',
            'meeting_minutes': 60, 'justification': 'Reinsurer call'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['pending_review'])
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, WorkdayJustification.Status.EXPLAINED)

    def test_other_reason_parks_for_review_not_justified(self):
        from hris.models import WorkdayJustification
        r = self._client().post('/api/v1/timedoctor/justify/', {
            'work_date': self.WORK.isoformat(), 'reason': 'other',
            'justification': 'Power cut at home'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, WorkdayJustification.Status.EXPLAINED)
        self.assertIsNotNone(self.row.responded_at)

    def test_on_leave_without_approved_leave_rejected(self):
        # Pending / missing leave is NOT evidence — apply first (Fable 2026-07-14).
        from hris.models import LeaveRequest, LeaveType, WorkdayJustification
        lt = LeaveType.objects.create(code='ANN-WF2', name='Annual 2')
        LeaveRequest.objects.create(
            profile=self.prof, leave_type=lt, start_date=self.WORK, end_date=self.WORK,
            days=Decimal('1'), status=LeaveRequest.Status.PENDING)
        r = self._client().post('/api/v1/timedoctor/justify/', {
            'work_date': self.WORK.isoformat(), 'reason': 'on_leave',
            'justification': 'Annual leave'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, WorkdayJustification.Status.UNJUSTIFIED)

    def test_manager_review_approve_and_reject(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        from hris.models import WorkdayJustification
        self.row.tracked_hours = Decimal('6.5')
        self.row.save(update_fields=['tracked_hours'])
        self._client().post('/api/v1/timedoctor/justify/', {
            'work_date': self.WORK.isoformat(), 'reason': 'external_meeting',
            'meeting_minutes': 60, 'justification': 'Reinsurer call'}, format='json')
        mgr = User.objects.create_user('mgr', 'mgr@alphadirect.co.bw', 'x', is_staff=True)
        mc = APIClient(); mc.force_authenticate(mgr)
        # non-manager cannot review
        r = self._client().post('/api/v1/timedoctor/justifications/review/', {
            'id': str(self.row.id), 'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403)
        # pending list shows it
        lst = mc.get('/api/v1/timedoctor/justifications/pending/')
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(len(lst.data['items']), 1)
        # approve → justified (1h gap fully covered by the 1h meeting)
        r = mc.post('/api/v1/timedoctor/justifications/review/', {
            'id': str(self.row.id), 'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, WorkdayJustification.Status.JUSTIFIED)
        self.assertEqual(self.row.reviewed_by_id, mgr.id)
        # reject path: park it again, then reject → unjustified, hours zeroed
        self.row.status = WorkdayJustification.Status.EXPLAINED
        self.row.save(update_fields=['status'])
        r = mc.post('/api/v1/timedoctor/justifications/review/', {
            'id': str(self.row.id), 'decision': 'reject', 'note': 'No evidence'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, WorkdayJustification.Status.UNJUSTIFIED)
        self.assertEqual(self.row.justified_hours, Decimal('0'))

    def test_bad_amount_is_400_not_500(self):
        r = self._client().post('/api/v1/timedoctor/justify/', {
            'work_date': self.WORK.isoformat(), 'reason': 'client_visit',
            'client_name': 'Choppies', 'amount': 'not-a-number'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_on_leave_links_leave_request(self):
        from hris.models import LeaveRequest, LeaveType, WorkdayJustification
        lt = LeaveType.objects.create(code='ANN-WF', name='Annual')
        lr = LeaveRequest.objects.create(
            profile=self.prof, leave_type=lt, start_date=self.WORK, end_date=self.WORK,
            days=Decimal('1'), status=LeaveRequest.Status.APPROVED)
        r = self._client().post('/api/v1/timedoctor/justify/', {
            'work_date': self.WORK.isoformat(), 'reason': 'on_leave',
            'justification': 'Annual leave'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.row.refresh_from_db()
        self.assertEqual(self.row.linked_leave_id, lr.id)
        self.assertEqual(self.row.status, WorkdayJustification.Status.JUSTIFIED)

    def test_terminated_employee_gets_no_profile(self):
        from payroll.models import Employee
        self.emp.status = Employee.Status.TERMINATED
        self.emp.save(update_fields=['status'])
        r = self._client().get('/api/v1/timedoctor/my-brief/')
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data['employee'])


class BriefJustifiedLeaveTests(TestCase):
    """Approved leave counts as justified up front — no 'explain yourself'
    email for someone on approved leave (Fable review)."""

    def test_brief_justified_when_leave_covers_day(self):
        from payroll.models import Employee
        from hris.models import HRISProfile
        emp = Employee.objects.create(employee_number='WF-002', full_name='Leave Taker')
        prof = HRISProfile.objects.create(employee=emp)
        brief = workforce_brief.build_employee_brief(
            prof, MON, Decimal('0'), set(), justified_hours=Decimal('7.5'))
        self.assertEqual(brief['status'], 'justified')
        self.assertFalse(brief['needs_justification'])


class BriefHtmlEscapeTests(SimpleTestCase):
    def test_user_content_is_escaped(self):
        brief = {
            'as_of': '2026-07-13', 'name': 'Evil <script>alert(1)</script>',
            'required': Decimal('7.5'), 'tracked': Decimal('4.0'),
            'shortfall': Decimal('3.5'), 'status': 'met', 'needs_justification': False,
            'leave': {'taken_month': Decimal('0'), 'pending_days': Decimal('0'), 'pending_count': 0},
            'tasks': [{'title': '<img src=x onerror=alert(1)>', 'priority': 'High'}],
            'announcements': [{'category': 'Meeting', 'title': 'A & B', 'body': '<b>raw</b>'}],
        }
        html = workforce_brief.build_brief_html(brief)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;script&gt;', html)


class TrackingDirectiveTests(TestCase):
    """An explicit don't-track click must actually take someone off tracking
    (CFO 2026-08-01 — the setup-page buttons had been writing rows nothing read).
    Payslips are stubbed so the test states the rule, not payroll plumbing."""

    @classmethod
    def setUpTestData(cls):
        from payroll.models import Employee
        from hris.models import HRISProfile
        cls.emp = Employee.objects.create(
            employee_number='TD-001', full_name='Tracked Person',
            email='tracked@alphadirect.co.bw', status='active')
        cls.prof = HRISProfile.objects.create(employee=cls.emp)

    def _profiles(self):
        from unittest.mock import patch
        from hris import eligibility
        with patch.object(eligibility, '_paid_employee_ids', return_value={self.emp.id}):
            return eligibility.tracking_profiles(), eligibility.tracking_roster()

    def test_paid_employee_tracks_by_default(self):
        profiles, roster = self._profiles()
        self.assertIn(self.prof, profiles)
        self.assertTrue([r for r in roster if r['employee_id'] == self.emp.id][0]['expected'])

    def test_dont_track_directive_excludes(self):
        from hris.models import TrackingDirective
        TrackingDirective.objects.create(employee=self.emp, expected_to_track=False,
                                         note='CFO: not tracked')
        profiles, roster = self._profiles()
        self.assertNotIn(self.prof, profiles)
        row = [r for r in roster if r['employee_id'] == self.emp.id][0]
        self.assertFalse(row['expected'])
        self.assertEqual(row['source'], 'directive')


class SaturdayChaserFailClosedTests(TestCase):
    """send_saturday_explain must send NOTHING when Time Doctor can't be read —
    an unproven zero must never become an accusation (DeepSeek review, HIGH)."""

    @classmethod
    def setUpTestData(cls):
        from payroll.models import Employee
        from hris.models import HRISProfile
        cls.emp = Employee.objects.create(
            employee_number='SAT-001', full_name='Saturday Person',
            email='satperson@alphadirect.co.bw', status='active')
        cls.prof = HRISProfile.objects.create(employee=cls.emp)

    def test_no_email_when_time_doctor_read_fails(self):
        from unittest.mock import patch
        from io import StringIO
        from django.core import mail
        from django.core.management import call_command
        from hris import eligibility
        from django.core.management.base import CommandError
        with patch.object(eligibility, 'tracking_profiles', return_value=[self.prof]), \
             patch('integrations.td_matching.active_td_users', side_effect=RuntimeError('TD down')):
            out, err = StringIO(), StringIO()
            with self.assertRaises(CommandError):      # non-zero exit, so cron sees it
                call_command('send_saturday_explain', '--date', SAT_NEW.isoformat(),
                             '--send', stdout=out, stderr=err)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('NO explain emails were sent', err.getvalue())

    def test_unmatched_person_is_held_not_emailed(self):
        # A person with no Time Doctor account has no data to settle, so the
        # guard cannot clear them — they must be held AND still counted in the
        # held list even when a matched person is held too (DeepSeek round 2).
        from unittest.mock import patch
        from io import StringIO
        from django.core import mail
        from django.core.management import call_command
        from hris import eligibility
        with patch.object(eligibility, 'tracking_profiles', return_value=[self.prof]), \
             patch('integrations.td_matching.active_td_users', return_value=[]), \
             patch('integrations.timedoctor.TimeDoctorClient.users', return_value=[]):
            out, err = StringIO(), StringIO()
            call_command('send_saturday_explain', '--date', SAT_NEW.isoformat(),
                         '--send', stdout=out, stderr=err)
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('Saturday Person', out.getvalue())
        self.assertIn('guardrail HELD', out.getvalue())


class ArjunOnTheManagerReportTests(SimpleTestCase):
    """CFO 2026-08-07 REVERSED the 2026-08-01 removal — Arjun (arjuniyer@) is back
    on the Time Doctor manager exceptions report (WORKFORCE_EXCEPTIONS_TO), added
    alongside Pako Kago, Legakwa Ntabeni and Bonno Ben. He remains OFF the
    separate HR-only silence-escalation list (that decision was NOT reversed)."""

    ARJUN = 'arjuniyer@alphadirect.co.bw'

    def test_on_the_daily_manager_report(self):
        from django.conf import settings
        recips = [e.lower() for e in settings.WORKFORCE_EXCEPTIONS_TO]
        self.assertIn(self.ARJUN, recips)          # re-added 2026-08-07
        # the rest of the group is still there
        self.assertIn('pganesharajah@alphadirect.co.bw', recips)
        self.assertIn('ubutale@alphadirect.co.bw', recips)

    def test_not_on_the_manager_silence_escalation(self):
        from hris import manager_accountability as ma
        self.assertNotIn(self.ARJUN, [e.lower() for e in ma.ESCALATION_EMAILS])
        # The list is Human Resources only (CFO 2026-08-03) — the note tells the
        # manager this is an HR matter, so HR is who receives it. This used to
        # assert the CEO was still on it; he came off with the CFO when the
        # copy changed. See test_manager_accountability_hr_only.
        self.assertIn('ubutale@alphadirect.co.bw', ma.ESCALATION_EMAILS)

    def test_still_allowed_to_use_the_workforce_screens(self):
        # Removing him from EMAILS must not remove his ACCESS.
        from hris import workforce_views
        self.assertIn(self.ARJUN, {e.lower() for e in workforce_views.DEFAULT_BRIEF_ADMINS})
