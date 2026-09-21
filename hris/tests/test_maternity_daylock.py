"""Maternity leave is a system-locked statutory entitlement (bug f4464440).

Oprah Mogomotsi, 2026-09-03: OMNI let the maternity end date be typed by hand,
risking miscalculation or unauthorised shortening of a statutory entitlement.
Requirement 1 — the end date is calculated from a single statutory parameter
(ELRA s.222 = 98 calendar days, day 1 inclusive) and cannot be set by the
applicant or manager. Requirement 2 — the application cannot be submitted without
a medical certificate.

These tests pin both against the real apply-leave API path.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, UserCompanyAccess, UserProfile
from hris.feature_views import get_leave_rules
from hris.models import HRISProfile, LeaveRequest
from payroll.models import Employee

APPLY_URL = '/hris/api/leave-requests/'


def _unlock(user):
    p, _ = UserProfile.objects.get_or_create(user=user)
    p.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    p.save(update_fields=['hris_unlocked_until'])


def _cert():
    return SimpleUploadedFile('cert.pdf', b'%PDF-1.4 confinement 2026-10-01',
                              content_type='application/pdf')


class MaternityDayLockTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='MAT', name='Maternity Test Co')
        cls.user = User.objects.create_user(
            'matstaff', 'matstaff@alphadirect.co.bw', 'x',
            first_name='Mara', last_name='Staff')
        cls.emp = Employee.objects.create(
            company=cls.co, employee_number='MAT-1', full_name='Mara Staff',
            email='matstaff@alphadirect.co.bw', user=cls.user)
        # A line manager who can review the request (a genuine people-manager:
        # she manages the applicant).
        cls.mgr_user = User.objects.create_user(
            'matmgr', 'matmgr@alphadirect.co.bw', 'x', first_name='Mo', last_name='Mgr')
        cls.mgr_emp = Employee.objects.create(
            company=cls.co, employee_number='MAT-2', full_name='Mo Mgr',
            email='matmgr@alphadirect.co.bw', user=cls.mgr_user)
        HRISProfile.objects.create(employee=cls.mgr_emp)
        cls.prof = HRISProfile.objects.create(
            employee=cls.emp, gender='F', manager=cls.mgr_emp)
        UserCompanyAccess.objects.get_or_create(user=cls.user, company=cls.co)
        UserCompanyAccess.objects.get_or_create(user=cls.mgr_user, company=cls.co)

    def setUp(self):
        _unlock(self.user)
        self.client.force_authenticate(self.user)
        self.start = timezone.localdate() + dt.timedelta(days=14)

    def _post(self, **extra):
        body = {
            'type': 'maternity',
            'start_date': self.start.isoformat(),
            'end_date': (self.start + dt.timedelta(days=5)).isoformat(),  # WRONG on purpose
            'approver_id': str(self.mgr_user.pk),
            'reason': 'Maternity leave',
            'certificate': _cert(),
        }
        body.update(extra)
        return self.client.post(APPLY_URL, body, format='multipart')

    def test_end_date_is_locked_to_statutory_parameter_not_user_input(self):
        stat = int(get_leave_rules()['maternity']['days'])   # 98
        r = self._post()
        self.assertEqual(r.status_code, 201, r.data)
        lr = LeaveRequest.objects.get(profile=self.prof, leave_type__code='maternity')
        # System-locked to start + (98 - 1), ignoring the 5-day end the user sent.
        self.assertEqual(lr.end_date, self.start + dt.timedelta(days=stat - 1))
        self.assertEqual(float(lr.days), float(stat))       # all 98 as full days

    def test_missing_end_date_still_computes_from_start(self):
        r = self._post(end_date='')
        self.assertEqual(r.status_code, 201, r.data)
        lr = LeaveRequest.objects.get(profile=self.prof, leave_type__code='maternity')
        stat = int(get_leave_rules()['maternity']['days'])
        self.assertEqual(lr.end_date, self.start + dt.timedelta(days=stat - 1))

    def test_certificate_is_mandatory(self):
        body = {
            'type': 'maternity',
            'start_date': self.start.isoformat(),
            'end_date': (self.start + dt.timedelta(days=97)).isoformat(),
            'approver_id': str(self.mgr_user.pk),
            'reason': 'Maternity leave',
        }
        r = self.client.post(APPLY_URL, body, format='multipart')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('certificate', str(r.data).lower())


class MaternityOnBehalfTest(APITestCase):
    """HR-initiated maternity application for an employee with no system access
    (bug f4464440, requirement 4)."""

    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='MOB', name='Maternity OnBehalf Co')
        # HR initiator with amendment rights — the production mechanism: an HRIS
        # role assignment (this is how Dorothy/Thapelo hold it), which maps to
        # hris_role == 'hris' == user_can_amend_hris True.
        from core.models import Role, UserRoleAssignment
        cls.hris_role, _ = Role.objects.get_or_create(code='HRIS', defaults={'name': 'HRIS', 'level': 3})
        cls.hr_user = User.objects.create_user('mobhr', 'mobhr@alphadirect.co.bw', 'x')
        UserRoleAssignment.objects.create(user=cls.hr_user, role=cls.hris_role)
        cls.hr_emp = Employee.objects.create(
            company=cls.co, employee_number='MOB-HR', full_name='H R Officer',
            email='mobhr@alphadirect.co.bw', user=cls.hr_user)
        HRISProfile.objects.create(employee=cls.hr_emp)
        # Unami — the designated HR-initiated approver (also holds HRIS, so he
        # can initiate for a delegated officer's own leave).
        cls.unami = User.objects.create_user('mobunami', 'ubutale@alphadirect.co.bw', 'x')
        UserRoleAssignment.objects.create(user=cls.unami, role=cls.hris_role)
        cls.cfo = User.objects.create_user('mobcfo', 'pganesharajah@alphadirect.co.bw', 'x')
        # Target employee with NO login (no system access at leave commencement).
        cls.target_emp = Employee.objects.create(
            company=cls.co, employee_number='MOB-1', full_name='Nologin Newmum')
        cls.target_prof = HRISProfile.objects.create(employee=cls.target_emp, gender='F')
        for u in (cls.hr_user, cls.unami, cls.cfo):
            UserCompanyAccess.objects.get_or_create(user=u, company=cls.co)

    def setUp(self):
        _unlock(self.hr_user)
        self.start = timezone.localdate() + dt.timedelta(days=14)

    def _post(self, actor, **extra):
        self.client.force_authenticate(actor)
        body = {
            'type': 'maternity',
            'start_date': self.start.isoformat(),
            'end_date': (self.start + dt.timedelta(days=3)).isoformat(),  # ignored (locked)
            'reason': 'Maternity leave',
            'on_behalf_employee_id': str(self.target_emp.pk),
            'certificate': _cert(),
        }
        body.update(extra)
        return self.client.post(APPLY_URL, body, format='multipart')

    def test_hr_can_apply_on_behalf_and_approver_is_unami(self):
        r = self._post(self.hr_user)
        self.assertEqual(r.status_code, 201, r.data)
        lr = LeaveRequest.objects.get(profile=self.target_prof, leave_type__code='maternity')
        self.assertEqual(lr.requested_approver_id, self.unami.pk)   # Unami is the approver
        stat = int(get_leave_rules()['maternity']['days'])
        self.assertEqual(lr.end_date, self.start + dt.timedelta(days=stat - 1))  # still locked

    def test_non_hr_cannot_apply_on_behalf(self):
        plain = User.objects.create_user('mobplain', 'mobplain@alphadirect.co.bw', 'x')
        Employee.objects.create(company=self.co, employee_number='MOB-P',
                                full_name='Plain Person', email='mobplain@alphadirect.co.bw',
                                user=plain)
        _unlock(plain)
        r = self._post(plain)
        self.assertEqual(r.status_code, 403, r.data)

    def test_initiator_is_never_the_approver_unami_initiating_uses_cfo(self):
        _unlock(self.unami)
        r = self._post(self.unami)
        self.assertEqual(r.status_code, 201, r.data)
        lr = LeaveRequest.objects.get(profile=self.target_prof, leave_type__code='maternity')
        # Unami initiated, so the CFO stands in as approver — applicant is not approver.
        self.assertEqual(lr.requested_approver_id, self.cfo.pk)
        self.assertNotEqual(lr.requested_approver_id, self.unami.pk)
