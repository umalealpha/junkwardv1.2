"""ELRA monthly performance feedback — escalation, evidentiary guard, lock,
retention, feature-flag dormancy, and entity-scope isolation.

CFO directive 2026-06-25. Exercises the real model + API paths (no mocks).
"""
import datetime as dt

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, Role, UserCompanyAccess, UserProfile, UserRoleAssignment
from hris.models import HRISProfile
from hris.performance_feedback_models import (
    CONSEC_LOW_FOR_PIP, CONSEC_LOW_FOR_WARNING, MonthlyCheckIn,
    PerformanceCheckRating, PerformanceImprovementPlan,
)
from payroll.models import Employee

CHECKINS_URL = '/hris/api/performance/checkins/'


def _unlock(user):
    p, _ = UserProfile.objects.get_or_create(user=user)
    p.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    p.save(update_fields=['hris_unlocked_until'])
    return p


def _checkin(profile, year, month, rating, **kw):
    kw.setdefault('conversation_date', dt.date(year, month, 15))
    if rating in (PerformanceCheckRating.BELOW, PerformanceCheckRating.SIG_BELOW):
        kw.setdefault('evidence', 'Missed 3 deadlines.')
        kw.setdefault('concerns', 'Turnaround time below standard.')
    return MonthlyCheckIn.objects.create(
        profile=profile, period_year=year, period_month=month,
        overall_rating=rating, **kw)


class EscalationLogicTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADIA', name='ADIC (test)')
        cls.emp = Employee.objects.create(employee_number='E1', full_name='Test Emp', company=cls.co)
        cls.profile = HRISProfile.objects.create(employee=cls.emp)

    def test_consecutive_low_escalates_to_warning_then_pip(self):
        c1 = _checkin(self.profile, 2026, 1, PerformanceCheckRating.BELOW)
        self.assertEqual(c1.consecutive_low_count, 1)
        self.assertFalse(c1.warning_recommended)
        self.assertFalse(c1.pip_triggered)

        c2 = _checkin(self.profile, 2026, 2, PerformanceCheckRating.SIG_BELOW)
        self.assertEqual(c2.consecutive_low_count, CONSEC_LOW_FOR_WARNING)
        self.assertTrue(c2.warning_recommended)
        self.assertFalse(c2.pip_triggered)
        self.assertFalse(PerformanceImprovementPlan.objects.filter(profile=self.profile).exists())

        c3 = _checkin(self.profile, 2026, 3, PerformanceCheckRating.BELOW)
        self.assertEqual(c3.consecutive_low_count, CONSEC_LOW_FOR_PIP)
        self.assertTrue(c3.pip_triggered)
        pips = PerformanceImprovementPlan.objects.filter(profile=self.profile)
        self.assertEqual(pips.count(), 1)                 # idempotent — exactly one
        self.assertEqual(pips.first().opened_from_checkin_id, c3.id)

    def test_good_month_resets_streak(self):
        _checkin(self.profile, 2026, 1, PerformanceCheckRating.BELOW)
        _checkin(self.profile, 2026, 2, PerformanceCheckRating.MEETS)
        c3 = _checkin(self.profile, 2026, 3, PerformanceCheckRating.BELOW)
        self.assertEqual(c3.consecutive_low_count, 1)      # Feb broke the streak
        self.assertFalse(c3.warning_recommended)

    def test_retention_set_on_create(self):
        c = _checkin(self.profile, 2026, 6, PerformanceCheckRating.MEETS)
        self.assertIsNotNone(c.retention_until)
        self.assertGreater(c.retention_until, c.conversation_date)

    def test_lock_after_both_signoffs(self):
        now = timezone.now()
        c = _checkin(self.profile, 2026, 7, PerformanceCheckRating.MEETS,
                     manager_signed_at=now, employee_signed_at=now)
        self.assertTrue(c.is_locked)

    def test_clean_requires_evidence_for_low_rating(self):
        c = MonthlyCheckIn(profile=self.profile, period_year=2026, period_month=8,
                           conversation_date=dt.date(2026, 8, 15),
                           overall_rating=PerformanceCheckRating.BELOW,
                           evidence='', concerns='')
        with self.assertRaises(ValidationError):
            c.full_clean()


class PerformanceApiAccessTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co_a = Company.objects.create(code='ADIA', name='ADIC (test)')
        cls.co_b = Company.objects.create(code='ADRB', name='ADRisk (test)')
        cls.emp_a = Employee.objects.create(employee_number='A1', full_name='Anna Adic', company=cls.co_a)
        cls.emp_b = Employee.objects.create(employee_number='B1', full_name='Bonolo Adrisk', company=cls.co_b)
        cls.prof_a = HRISProfile.objects.create(employee=cls.emp_a)
        cls.prof_b = HRISProfile.objects.create(employee=cls.emp_b)
        cls.ci_a = _checkin(cls.prof_a, 2026, 5, PerformanceCheckRating.MEETS)
        cls.ci_b = _checkin(cls.prof_b, 2026, 5, PerformanceCheckRating.MEETS)

        cls.hr_role, _ = Role.objects.get_or_create(
            code='HR_MANAGER', defaults={'name': 'HR Manager', 'level': 2})
        cls.restricted_hr = User.objects.create_user(
            username='hr_adrb', email='hr_adrb@example.com', password='x')
        UserRoleAssignment.objects.create(user=cls.restricted_hr, role=cls.hr_role)
        UserCompanyAccess.objects.create(
            user=cls.restricted_hr, company=cls.co_b, can_view=True, granted_by=cls.restricted_hr)
        _unlock(cls.restricted_hr)

        cls.admin = User.objects.create_user(
            username='grp_admin', email='admin@example.com', password='x',
            is_superuser=True, is_staff=True)
        _unlock(cls.admin)

    @override_settings(ELRA_PERF_ENABLED=False)
    def test_feature_flag_off_returns_403(self):
        # Force the flag OFF explicitly — do NOT rely on the ambient default,
        # which is True in the deployed settings (the module is live post-DPIA).
        # This asserts the dormancy kill-switch still returns 403 regardless of
        # environment. (Previously relied on the default being unset/False, so
        # it gave a false failure when run against prod settings.)
        self.client.force_authenticate(self.admin)
        resp = self.client.get(CHECKINS_URL)
        self.assertEqual(resp.status_code, 403, resp.content)

    @override_settings(ELRA_PERF_ENABLED=True)
    def test_entity_restricted_hr_sees_only_its_entity(self):
        self.client.force_authenticate(self.restricted_hr)
        resp = self.client.get(CHECKINS_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {row['id'] for row in resp.json()['checkins']}
        self.assertIn(str(self.ci_b.id), ids)
        self.assertNotIn(str(self.ci_a.id), ids)   # ADIC check-in must NOT leak

    @override_settings(ELRA_PERF_ENABLED=True)
    def test_unrestricted_sees_all_entities(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(CHECKINS_URL)
        self.assertEqual(resp.status_code, 200, resp.content)
        ids = {row['id'] for row in resp.json()['checkins']}
        self.assertIn(str(self.ci_a.id), ids)
        self.assertIn(str(self.ci_b.id), ids)

    @override_settings(ELRA_PERF_ENABLED=True)
    def test_locked_checkin_rejects_patch(self):
        now = timezone.now()
        locked = _checkin(self.prof_b, 2026, 9, PerformanceCheckRating.MEETS,
                          manager_signed_at=now, employee_signed_at=now)
        self.assertTrue(locked.is_locked)
        self.client.force_authenticate(self.restricted_hr)
        resp = self.client.patch(f'{CHECKINS_URL}{locked.id}/', {'manager_comments': 'edit attempt'})
        self.assertEqual(resp.status_code, 409, resp.content)


@override_settings(ELRA_PERF_ENABLED=True)
class EmployeeDecisionTest(APITestCase):
    """Employee Accept / Partially accept / Decline + the >=50-word rule on a
    partial/decline (CFO 2026-08-18)."""

    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADIA', name='ADIC (test)')
        cls.user = User.objects.create_user('anna', email='anna@example.com', password='x')
        cls.emp = Employee.objects.create(
            employee_number='A1', full_name='Anna Adic', company=cls.co, user=cls.user)
        cls.prof = HRISProfile.objects.create(employee=cls.emp)
        cls.ci = _checkin(cls.prof, 2026, 5, PerformanceCheckRating.MEETS)

    def _patch(self, data):
        self.client.force_authenticate(self.user)
        return self.client.patch(f'{CHECKINS_URL}{self.ci.id}/?mine=1', data, format='json')

    def test_accept_needs_no_words(self):
        r = self._patch({'employee_decision': 'accept', 'sign': True})
        self.assertEqual(r.status_code, 200, r.content)
        self.ci.refresh_from_db()
        self.assertEqual(self.ci.employee_decision, 'accept')

    def test_partial_under_50_words_is_rejected(self):
        r = self._patch({'employee_decision': 'partial',
                         'employee_response': 'I disagree with a few points here.'})
        self.assertEqual(r.status_code, 400, r.content)
        self.ci.refresh_from_db()
        self.assertEqual(self.ci.employee_decision, '')

    def test_partial_with_50_words_is_accepted(self):
        fifty = ' '.join(['point'] * 50)
        r = self._patch({'employee_decision': 'partial',
                         'employee_response': fifty, 'sign': True})
        self.assertEqual(r.status_code, 200, r.content)
        self.ci.refresh_from_db()
        self.assertEqual(self.ci.employee_decision, 'partial')

    def test_decline_under_50_words_is_rejected(self):
        r = self._patch({'employee_decision': 'decline',
                         'employee_response': 'No, this is wrong.'})
        self.assertEqual(r.status_code, 400, r.content)
