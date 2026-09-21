"""
HR leave administration (Unami Butale, 2026-07-27). Covers the new surfaces:
  * certificate download gate (view / 404 / 403)
  * all-leave oversight (pending / approved, department filter)
  * department analytics (on leave today, upcoming 14 days)
  * dual-approval: manager-approve on sick leave → HR review pending
  * HR verify / flag
  * apply_leave accepts a fixed reason_category and no longer forces a 15-word why
  * the daily digest builder
"""
import datetime as _dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Company, Currency
from hris.leave_digest import build_leave_digest
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee

D = _dt.date


class LeaveAdminTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.annual = LeaveType.objects.create(code='annual', name='Annual Leave')
        cls.sick = LeaveType.objects.create(code='sick', name='Sick Leave',
                                            requires_medical_cert=True)

        # Employee (applies for leave).
        cls.emp_user = User.objects.create_user('alice', email='alice@ad.co.bw', password='x')
        cls.emp = Employee.objects.create(employee_number='E1', full_name='Alice M',
                                          company=cls.company, department='Underwriting',
                                          user=cls.emp_user)
        # Line manager on file so apply_leave has someone to route to (a leave
        # request needs an approver — pre-existing rule, unrelated to this work).
        cls.mgr_emp = Employee.objects.create(employee_number='M1', full_name='Mary Manager',
                                              company=cls.company, department='Underwriting')
        cls.hp = HRISProfile.objects.create(employee=cls.emp, manager=cls.mgr_emp)

        # HR head (superuser → 'superadmin' → manage_leave_admin + approve_team_leave).
        cls.hr = User.objects.create_superuser('unami', email='ubutale@ad.co.bw', password='x')

        # A DIFFERENT manager: holds approve_team_leave (manages someone else) and
        # can view ADIC, but does NOT review Alice's leave — must be denied her cert.
        from core.models import UserCompanyAccess
        cls.foreign_mgr = User.objects.create_user('frank', email='frank@ad.co.bw', password='x')
        cls.foreign_mgr_emp = Employee.objects.create(employee_number='M2', full_name='Frank Foreign',
                                                      company=cls.company, user=cls.foreign_mgr)
        sub = Employee.objects.create(employee_number='S1', full_name='Sub Ordinate', company=cls.company)
        HRISProfile.objects.create(employee=sub, manager=cls.foreign_mgr_emp)  # → role 'mgr'
        UserCompanyAccess.objects.create(user=cls.foreign_mgr, company=cls.company, can_view=True)

    def _client(self, user):
        c = APIClient(); c.force_authenticate(user=user); return c

    def _lr(self, lt, status=LeaveRequest.Status.PENDING, cert=False,
            start=D(2026, 8, 3), end=D(2026, 8, 5)):
        lr = LeaveRequest.objects.create(
            profile=self.hp, leave_type=lt, start_date=start, end_date=end,
            days=Decimal('3'), status=status)
        if cert:
            lr.medical_certificate.save('note.pdf', ContentFile(b'%PDF-1.4 fake'), save=True)
        return lr

    # ── ask #4: certificate download ──────────────────────────────────────
    def test_hr_can_view_certificate(self):
        lr = self._lr(self.sick, cert=True)
        r = self._client(self.hr).get(f'/hris/api/leave-requests/{lr.id}/certificate/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b''.join(r.streaming_content), b'%PDF-1.4 fake')

    def test_certificate_served_with_safe_content_type(self):
        lr = self._lr(self.sick, cert=True)  # note.pdf
        r = self._client(self.hr).get(f'/hris/api/leave-requests/{lr.id}/certificate/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_apply_rejects_non_pdf_image_certificate(self):
        # A .html/.svg upload would be stored XSS when served inline to HR.
        html = SimpleUploadedFile('evil.html', b'<script>alert(1)</script>',
                                  content_type='text/html')
        r = self._client(self.emp_user).post('/hris/api/leave-requests/', {
            'type': 'sick', 'start_date': '2026-09-01', 'end_date': '2026-09-02',
            'reason_category': 'medical', 'certificate': html,
        }, format='multipart')
        self.assertEqual(r.status_code, 400)
        self.assertIn('PDF', r.json()['detail'])

    def test_certificate_404_when_absent(self):
        lr = self._lr(self.annual, cert=False)
        r = self._client(self.hr).get(f'/hris/api/leave-requests/{lr.id}/certificate/')
        self.assertEqual(r.status_code, 404)

    def test_certificate_forbidden_for_employee(self):
        lr = self._lr(self.sick, cert=True)
        r = self._client(self.emp_user).get(f'/hris/api/leave-requests/{lr.id}/certificate/')
        self.assertEqual(r.status_code, 403)

    def test_certificate_forbidden_for_unrelated_manager(self):
        # Frank manages someone else — he must not open Alice's medical cert
        # even though he is a manager in the same company (PII tightening).
        lr = self._lr(self.sick, cert=True)
        r = self._client(self.foreign_mgr).get(f'/hris/api/leave-requests/{lr.id}/certificate/')
        self.assertEqual(r.status_code, 403)

    def test_may_view_certificate_matrix(self):
        # Direct test of the PII gate: HR sees all; the named reviewer sees their
        # own; an unrelated person does not.
        from hris.leave_admin import _may_view_certificate
        lr = self._lr(self.sick, cert=True)
        self.assertTrue(_may_view_certificate(self.hr, lr))            # HR
        self.assertFalse(_may_view_certificate(self.foreign_mgr, lr))  # unrelated mgr
        lr.requested_approver = self.foreign_mgr
        self.assertTrue(_may_view_certificate(self.foreign_mgr, lr))   # now the reviewer

    # ── asks #1 / #2: all leave ───────────────────────────────────────────
    def test_all_leave_status_filter(self):
        self._lr(self.annual, status=LeaveRequest.Status.PENDING)
        self._lr(self.annual, status=LeaveRequest.Status.APPROVED)
        c = self._client(self.hr)
        pend = c.get('/hris/api/leave-admin/all/?status=pending').json()
        appr = c.get('/hris/api/leave-admin/all/?status=approved').json()
        self.assertEqual(pend['count'], 1)
        self.assertEqual(appr['count'], 1)
        self.assertEqual(pend['rows'][0]['status'], 'pending')

    def test_all_leave_department_filter(self):
        self._lr(self.annual, status=LeaveRequest.Status.PENDING)
        c = self._client(self.hr)
        hit = c.get('/hris/api/leave-admin/all/?status=pending&department=underwriting').json()
        miss = c.get('/hris/api/leave-admin/all/?status=pending&department=claims').json()
        self.assertEqual(hit['count'], 1)
        self.assertEqual(miss['count'], 0)

    def test_all_leave_forbidden_for_employee(self):
        r = self._client(self.emp_user).get('/hris/api/leave-admin/all/?status=pending')
        self.assertEqual(r.status_code, 403)

    # ── ask #3: analytics ─────────────────────────────────────────────────
    def test_analytics_today_and_upcoming(self):
        today = timezone.now().date()
        self._lr(self.annual, status=LeaveRequest.Status.APPROVED,
                 start=today - _dt.timedelta(days=1), end=today + _dt.timedelta(days=1))
        self._lr(self.annual, status=LeaveRequest.Status.APPROVED,
                 start=today + _dt.timedelta(days=5), end=today + _dt.timedelta(days=6))
        data = self._client(self.hr).get('/hris/api/leave-admin/analytics/').json()
        self.assertEqual(len(data['on_leave_today']), 1)
        self.assertEqual(len(data['upcoming_14d']), 1)
        self.assertTrue(any(d['department'] == 'Underwriting' for d in data['by_department']))

    # ── ask #5: dual approval ─────────────────────────────────────────────
    def test_manager_approve_sick_enters_hr_review(self):
        lr = self._lr(self.sick, cert=True)
        r = self._client(self.hr).post(
            f'/hris/api/leave-requests/{lr.id}/decide/',
            {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(lr.hr_review_state, LeaveRequest.HRReviewState.PENDING)

    def test_annual_approve_no_hr_review(self):
        lr = self._lr(self.annual)
        self._client(self.hr).post(
            f'/hris/api/leave-requests/{lr.id}/decide/',
            {'decision': 'approve'}, format='json')
        lr.refresh_from_db()
        self.assertEqual(lr.hr_review_state, LeaveRequest.HRReviewState.NOT_REQUIRED)

    def test_hr_verify_and_flag(self):
        lr = self._lr(self.sick, cert=True, status=LeaveRequest.Status.APPROVED)
        lr.hr_review_state = LeaveRequest.HRReviewState.PENDING
        lr.save(update_fields=['hr_review_state'])
        c = self._client(self.hr)

        # flag needs a note
        bad = c.post(f'/hris/api/leave-admin/{lr.id}/hr-verify/',
                     {'decision': 'flag'}, format='json')
        self.assertEqual(bad.status_code, 400)

        ok = c.post(f'/hris/api/leave-admin/{lr.id}/hr-verify/',
                    {'decision': 'verify', 'notes': 'Cert checks out.'}, format='json')
        self.assertEqual(ok.status_code, 200)
        lr.refresh_from_db()
        self.assertEqual(lr.hr_review_state, LeaveRequest.HRReviewState.VERIFIED)
        self.assertEqual(lr.hr_reviewer_id, self.hr.id)

    def test_hr_cannot_verify_own_leave(self):
        # Segregation of duties: HR can't authenticate their own leave.
        hr_emp = Employee.objects.create(employee_number='E9', full_name='Unami B',
                                         company=self.company, user=self.hr)
        hr_hp = HRISProfile.objects.create(employee=hr_emp)
        lr = LeaveRequest.objects.create(
            profile=hr_hp, leave_type=self.sick, start_date=D(2026, 8, 3),
            end_date=D(2026, 8, 5), days=Decimal('3'),
            status=LeaveRequest.Status.APPROVED,
            hr_review_state=LeaveRequest.HRReviewState.PENDING)
        r = self._client(self.hr).post(f'/hris/api/leave-admin/{lr.id}/hr-verify/',
                                        {'decision': 'verify'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_hr_queue_lists_pending(self):
        lr = self._lr(self.sick, cert=True, status=LeaveRequest.Status.APPROVED)
        lr.hr_review_state = LeaveRequest.HRReviewState.PENDING
        lr.save(update_fields=['hr_review_state'])
        data = self._client(self.hr).get('/hris/api/leave-admin/hr-queue/').json()
        self.assertEqual(data['count'], 1)
        self.assertTrue(data['rows'][0]['certificate_url'])

    # ── ask #6: fixed reason, no forced 15-word why ───────────────────────
    def test_apply_accepts_category_without_long_reason(self):
        r = self._client(self.emp_user).post('/hris/api/leave-requests/', {
            'type': 'annual', 'start_date': '2026-09-01', 'end_date': '2026-09-02',
            'reason_category': 'undisclosed',
        }, format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        lr = LeaveRequest.objects.get(pk=r.json()['id'])
        self.assertEqual(lr.reason_category, 'undisclosed')
        self.assertEqual(lr.reason, '')

    def test_apply_rejects_blank_reason_and_category(self):
        r = self._client(self.emp_user).post('/hris/api/leave-requests/', {
            'type': 'annual', 'start_date': '2026-09-01', 'end_date': '2026-09-02',
        }, format='multipart')
        self.assertEqual(r.status_code, 400)

    def test_apply_rejects_bad_category(self):
        r = self._client(self.emp_user).post('/hris/api/leave-requests/', {
            'type': 'annual', 'start_date': '2026-09-01', 'end_date': '2026-09-02',
            'reason_category': 'not-a-category',
        }, format='multipart')
        self.assertEqual(r.status_code, 400)

    # ── ask #8: digest builder ────────────────────────────────────────────
    def test_digest_builds_when_leave_present(self):
        today = timezone.now().date()
        self._lr(self.annual, status=LeaveRequest.Status.APPROVED,
                 start=today, end=today + _dt.timedelta(days=1))
        digest = build_leave_digest(today)
        self.assertIsNotNone(digest)
        self.assertIn('On leave today', digest['html'])
        self.assertIn('Alice M', digest['html'])

    def test_digest_empty_returns_none(self):
        # No leave anywhere near today.
        self.assertIsNone(build_leave_digest(D(2020, 1, 1)))
