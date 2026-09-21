"""Controlled override of a locked maternity date (bug f4464440, requirement 3).

A locked date moves only with new evidence, a reason code, and a SECOND approver
who is not the proposer, the employee, or the original approver.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import AuditLog, Company, Role, UserRoleAssignment
from hris.maternity_override_models import MaternityDateOverride
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee


def _cert():
    return SimpleUploadedFile('evi.pdf', b'%PDF-1.4 new evidence', content_type='application/pdf')


class MaternityOverrideTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='MOV', name='Maternity Override Co')
        cls.hris_role, _ = Role.objects.get_or_create(code='HRIS', defaults={'name': 'HRIS', 'level': 3})

        def hr(username):
            u = User.objects.create_user(username, f'{username}@alphadirect.co.bw', 'x')
            UserRoleAssignment.objects.create(user=u, role=cls.hris_role)
            return u

        cls.proposer = hr('movprop')       # HR who proposes
        cls.second   = hr('movsecond')     # a DIFFERENT HR approver
        cls.orig_appr_user = User.objects.create_user(
            'movappr', 'movappr@alphadirect.co.bw', 'x')

        cls.emp = Employee.objects.create(
            company=cls.co, employee_number='MOV-1', full_name='Mum On Leave')
        cls.prof = HRISProfile.objects.create(employee=cls.emp, gender='F')
        cls.mtype = LeaveType.objects.create(code='maternity', name='Maternity Leave',
                                             default_annual_days=98)
        cls.start = timezone.localdate() + dt.timedelta(days=10)
        cls.lr = LeaveRequest.objects.create(
            profile=cls.prof, leave_type=cls.mtype,
            start_date=cls.start, end_date=cls.start + dt.timedelta(days=97),
            days=98, status=LeaveRequest.Status.APPROVED,
            requested_approver=cls.orig_appr_user)

    def _client(self, user):
        c = APIClient(); c.force_authenticate(user); return c

    def _propose(self, user=None, **extra):
        body = {
            'reason_code': 'complications_extension',
            'new_end_date': (self.start + dt.timedelta(days=111)).isoformat(),  # 14 days more
            'certificate': _cert(),
        }
        body.update(extra)
        return self._client(user or self.proposer).post(
            f'/hris/api/leave-requests/{self.lr.pk}/maternity-override/', body, format='multipart')

    def test_propose_creates_pending_without_moving_the_date(self):
        r = self._propose()
        self.assertEqual(r.status_code, 201, r.data)
        self.lr.refresh_from_db()
        self.assertEqual(self.lr.end_date, self.start + dt.timedelta(days=97))  # unchanged yet
        ov = MaternityDateOverride.objects.get(leave_request=self.lr)
        self.assertEqual(ov.status, 'pending')

    def test_second_approver_applies_the_new_date(self):
        ov_id = self._propose().data['id']
        r = self._client(self.second).post(
            f'/hris/api/maternity-overrides/{ov_id}/decide/', {'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.lr.refresh_from_db()
        self.assertEqual(self.lr.end_date, self.start + dt.timedelta(days=111))
        self.assertTrue(AuditLog.objects.filter(
            table_name='MaternityDateOverride', action='approve').exists())

    def test_proposer_cannot_approve_their_own(self):
        ov_id = self._propose().data['id']
        r = self._client(self.proposer).post(
            f'/hris/api/maternity-overrides/{ov_id}/decide/', {'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.data)
        self.lr.refresh_from_db()
        self.assertEqual(self.lr.end_date, self.start + dt.timedelta(days=97))  # not moved

    def test_original_approver_cannot_second_approve(self):
        UserRoleAssignment.objects.create(user=self.orig_appr_user, role=self.hris_role)
        ov_id = self._propose().data['id']
        r = self._client(self.orig_appr_user).post(
            f'/hris/api/maternity-overrides/{ov_id}/decide/', {'action': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.data)

    def test_non_hr_cannot_propose(self):
        plain = User.objects.create_user('movplain', 'movplain@alphadirect.co.bw', 'x')
        r = self._propose(user=plain)
        self.assertEqual(r.status_code, 403, r.data)
