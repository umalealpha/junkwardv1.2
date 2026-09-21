"""The long-overdue-task gate + the leave decision notice (CFO 2026-08-07).

Covers the five things the CFO asked for, against the real models and API
paths (no mocks):

  1. A leave decision emails the EMPLOYEE — approved, declined and cancelled.
  2. Applying with work more than 2 days overdue raises a CEO/CFO
     countersignature, and the manager cannot approve until it is signed.
     Sick leave is never gated.
  3. Long-overdue work writes itself into the feedback AND caps the rating.
  5. The one-click manager feedback page records a real MonthlyCheckIn.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, OmniTask, UserCompanyAccess, UserProfile
from hris import exec_signoff_service, overdue_gate, perf_panel
from hris.exec_signoff_models import ExecSignoff
from hris.models import HRISProfile, LeaveType, LeaveRequest
from payroll.models import Employee

APPLY_URL = '/hris/api/leave-requests/'
MINE_URL = '/hris/api/leave-requests/mine/'


def _decide_url(lr):
    return f'/hris/api/leave-requests/{lr.pk}/decide/'


def _unlock(user):
    p, _ = UserProfile.objects.get_or_create(user=user)
    p.hris_unlocked_until = timezone.now() + dt.timedelta(hours=8)
    p.save(update_fields=['hris_unlocked_until'])
    return p


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADOG', name='ADIC (overdue test)')

        cls.staff_user = User.objects.create_user(
            'ogstaff', 'ogstaff@alphadirect.co.bw', 'x', first_name='Otto', last_name='Staff')
        cls.mgr_user = User.objects.create_user(
            'ogmgr', 'ogmgr@alphadirect.co.bw', 'x', first_name='Mo', last_name='Manager')
        cls.cfo_user = User.objects.create_user(
            'ogcfo', 'ogcfo@alphadirect.co.bw', 'x', first_name='Cee', last_name='Effo')

        cls.staff_emp = Employee.objects.create(
            company=cls.co, employee_number='OG-1', full_name='Otto Staff',
            email='ogstaff@alphadirect.co.bw', user=cls.staff_user)
        cls.mgr_emp = Employee.objects.create(
            company=cls.co, employee_number='OG-2', full_name='Mo Manager',
            email='ogmgr@alphadirect.co.bw', user=cls.mgr_user)

        cls.staff_prof = HRISProfile.objects.create(
            employee=cls.staff_emp, manager=cls.mgr_emp)
        cls.mgr_prof = HRISProfile.objects.create(employee=cls.mgr_emp)

        # The CFO title is what makes someone a valid countersigner.
        UserProfile.objects.update_or_create(
            user=cls.cfo_user, defaults={'title': UserProfile.Title.CFO, 'is_active': True})

        for u in (cls.staff_user, cls.mgr_user, cls.cfo_user):
            UserCompanyAccess.objects.get_or_create(user=u, company=cls.co)

        cls.annual = LeaveType.objects.create(code='annual', name='Annual leave')
        cls.sick = LeaveType.objects.create(
            code='sick', name='Sick leave', requires_medical_cert=True)

    def make_overdue_task(self, user, days=5, title='Bank recon'):
        """A task whose deadline is `days` ago and which is still open."""
        return OmniTask.objects.create(
            assigner=self.cfo_user, assignee=user, title=title,
            due_at=timezone.localdate() - dt.timedelta(days=days),
            due_time=dt.time(16, 0),
            status=OmniTask.Status.PENDING,
        )

    def pending_leave(self, leave_type=None):
        return LeaveRequest.objects.create(
            profile=self.staff_prof, leave_type=leave_type or self.annual,
            start_date=timezone.localdate() + dt.timedelta(days=7),
            end_date=timezone.localdate() + dt.timedelta(days=8),
            days=2, status=LeaveRequest.Status.PENDING,
        )


# ── 1. the employee is told the answer ──────────────────────────────────────

class LeaveDecisionReachesEmployeeTest(Base):
    def test_approval_emails_the_employee(self):
        lr = self.pending_leave()
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        mail.outbox = []

        r = self.client.post(_decide_url(lr), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)

        to_employee = [m for m in mail.outbox if 'ogstaff@alphadirect.co.bw' in m.to]
        self.assertTrue(to_employee, 'the employee was never told their leave was approved')
        self.assertIn('approved', to_employee[0].subject.lower())

    def test_decline_emails_the_employee_with_the_reason(self):
        lr = self.pending_leave()
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        mail.outbox = []

        r = self.client.post(_decide_url(lr),
                             {'decision': 'reject', 'notes': 'Too many people out.'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.data)
        to_employee = [m for m in mail.outbox if 'ogstaff@alphadirect.co.bw' in m.to]
        self.assertTrue(to_employee, 'the employee was never told their leave was declined')
        body = to_employee[0].alternatives[0][0] if to_employee[0].alternatives else to_employee[0].body
        self.assertIn('Too many people out.', body)

    def test_the_employee_can_read_the_status_back(self):
        lr = self.pending_leave()
        lr.status = LeaveRequest.Status.APPROVED
        lr.approver = self.mgr_user
        lr.decided_at = timezone.now()
        lr.save()
        self.client.force_authenticate(self.staff_user)
        r = self.client.get(MINE_URL)
        self.assertEqual(r.status_code, 200, r.data)
        rows = r.data['requests']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], LeaveRequest.Status.APPROVED)
        self.assertEqual(rows[0]['approver'], 'Mo Manager')


# ── 2. the gate ─────────────────────────────────────────────────────────────

class OverdueDetectionTest(Base):
    def test_two_days_is_the_threshold(self):
        self.assertEqual(overdue_gate.overdue_days_threshold(), 2)

    def test_a_task_one_day_late_does_not_count(self):
        self.make_overdue_task(self.staff_user, days=1)
        self.assertEqual(overdue_gate.overdue_summary(self.staff_user)['count'], 0)

    def test_a_task_five_days_late_counts(self):
        self.make_overdue_task(self.staff_user, days=5)
        s = overdue_gate.overdue_summary(self.staff_user)
        self.assertEqual(s['count'], 1)
        self.assertGreaterEqual(s['tasks'][0]['days_overdue'], 4)

    def test_a_completed_task_never_counts(self):
        t = self.make_overdue_task(self.staff_user, days=9)
        t.status = OmniTask.Status.DONE
        t.completed_at = timezone.now()
        t.save()
        self.assertEqual(overdue_gate.overdue_summary(self.staff_user)['count'], 0)

    def test_a_task_with_no_due_date_never_counts(self):
        OmniTask.objects.create(assigner=self.cfo_user, assignee=self.staff_user,
                                title='Someday', status=OmniTask.Status.PENDING)
        self.assertEqual(overdue_gate.overdue_summary(self.staff_user)['count'], 0)

    def test_sick_leave_is_exempt(self):
        self.assertTrue(overdue_gate.leave_type_is_exempt(self.sick))
        self.assertFalse(overdue_gate.leave_type_is_exempt(self.annual))


class LeaveGateTest(Base):
    def test_manager_cannot_approve_until_the_executive_signs(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        self.assertIsNotNone(so)

        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(_decide_url(lr), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 409, r.data)
        self.assertTrue(r.data.get('needs_exec_signoff'))
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)

    def test_after_the_executive_signs_the_manager_can_approve(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        exec_signoff_service.decide(so, self.cfo_user, True)

        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(_decide_url(lr), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)

    def test_a_manager_may_still_decline_while_unsigned(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(_decide_url(lr), {'decision': 'reject', 'notes': 'no'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.data)

    def test_sick_leave_is_never_gated(self):
        self.make_overdue_task(self.staff_user, days=30)
        lr = self.pending_leave(leave_type=self.sick)
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.sick)
        self.assertIsNone(so, 'sick leave must never need an executive signature')

    def test_no_overdue_work_means_no_gate(self):
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        self.assertIsNone(so)

    def test_only_the_ceo_or_cfo_can_sign(self):
        self.assertTrue(exec_signoff_service.user_can_sign(self.cfo_user))
        self.assertFalse(exec_signoff_service.user_can_sign(self.mgr_user))

    def test_a_decline_refuses_the_leave_and_tells_the_employee(self):
        from hris.exec_signoff_actions import _refuse_underlying
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        mail.outbox = []
        exec_signoff_service.decide(so, self.cfo_user, False, 'Clear the recon first.')
        _refuse_underlying(so)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.REFUSED)
        self.assertTrue([m for m in mail.outbox if 'ogstaff@alphadirect.co.bw' in m.to])

    def test_the_signature_request_reaches_the_cfo(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        mail.outbox = []
        exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        self.assertTrue(
            OmniTask.objects.filter(assignee=self.cfo_user,
                                    title__startswith='Sign off:').exists())
        self.assertTrue([m for m in mail.outbox if 'ogcfo@alphadirect.co.bw' in m.to])


# ── 3. the automatic negative + the rating cap ──────────────────────────────

@override_settings(ELRA_PERF_ENABLED=True)
class PerformancePenaltyTest(Base):
    def test_overdue_work_lands_in_the_panel(self):
        self.make_overdue_task(self.staff_user, days=5)
        today = timezone.localdate()
        panel = perf_panel.employee_month_panel(self.staff_emp, today.year, today.month)
        self.assertEqual(panel['tasks_overdue_long'], 1)

    def test_overdue_work_writes_itself_into_the_concerns_box(self):
        self.make_overdue_task(self.staff_user, days=5, title='Bank recon')
        today = timezone.localdate()
        panel = perf_panel.employee_month_panel(self.staff_emp, today.year, today.month)
        draft = perf_panel.draft_feedback(panel, [])
        self.assertIn('Bank recon', draft['concerns'])
        self.assertIn('past the due date', draft['concerns'])

    def test_the_rating_is_capped_at_partially_meets(self):
        self.make_overdue_task(self.staff_user, days=5)
        today = timezone.localdate()
        panel = perf_panel.employee_month_panel(self.staff_emp, today.year, today.month)
        self.assertEqual(perf_panel.rating_cap(panel), 'PA')
        self.assertTrue(perf_panel.rating_blocked(panel, 'ME'))
        self.assertTrue(perf_panel.rating_blocked(panel, 'EX'))
        self.assertFalse(perf_panel.rating_blocked(panel, 'PA'))
        self.assertFalse(perf_panel.rating_blocked(panel, 'BE'))

    def test_no_cap_when_nothing_is_overdue(self):
        today = timezone.localdate()
        panel = perf_panel.employee_month_panel(self.staff_emp, today.year, today.month)
        self.assertIsNone(perf_panel.rating_cap(panel))
        self.assertFalse(perf_panel.rating_blocked(panel, 'EX'))

    def test_the_api_refuses_a_rating_above_the_cap(self):
        self.make_overdue_task(self.staff_user, days=5)
        today = timezone.localdate()
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post('/hris/api/performance/checkins/', {
            'profile': str(self.staff_prof.pk),
            'period_year': today.year, 'period_month': today.month,
            'conversation_date': today.isoformat(),
            'overall_rating': 'ME',
        }, format='json')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(r.data.get('rating_cap'), 'PA')

    def test_the_api_accepts_a_rating_at_the_cap(self):
        self.make_overdue_task(self.staff_user, days=5)
        today = timezone.localdate()
        _unlock(self.mgr_user)
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post('/hris/api/performance/checkins/', {
            'profile': str(self.staff_prof.pk),
            'period_year': today.year, 'period_month': today.month,
            'conversation_date': today.isoformat(),
            'overall_rating': 'PA',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.data)


# ── 5. one-click feedback from the email ────────────────────────────────────

@override_settings(ELRA_PERF_ENABLED=True)
class OneClickManagerFeedbackTest(Base):
    def test_the_page_lists_the_outstanding_report(self):
        from hris.manager_feedback_actions import action_url
        today = timezone.localdate()
        url = action_url(self.mgr_emp, today.year, today.month)
        path = url.split('omni.alphadirect.co.bw')[-1]
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Otto Staff', r.content.decode())

    def test_saving_records_a_real_checkin(self):
        from hris.manager_feedback_actions import make_token
        from hris.performance_feedback_models import MonthlyCheckIn
        today = timezone.localdate()
        token = make_token(self.mgr_emp, today.year, today.month)
        r = self.client.post(f'/hris/api/manager-feedback/{token}/submit/', {
            'profile': str(self.staff_prof.pk),
            'strengths': 'Handled the month-end well.',
            'concerns': '', 'support_provided': 'More time on recons.',
            'overall_rating': 'ME',
        })
        self.assertEqual(r.status_code, 200, r.content[:400])
        ci = MonthlyCheckIn.objects.filter(profile=self.staff_prof).first()
        self.assertIsNotNone(ci, 'the one-click page did not record a check-in')
        self.assertEqual(ci.overall_rating, 'ME')
        self.assertEqual(ci.reviewer_id, self.mgr_user.id)

    def test_the_cap_also_applies_on_the_one_click_page(self):
        from hris.manager_feedback_actions import make_token
        from hris.performance_feedback_models import MonthlyCheckIn
        self.make_overdue_task(self.staff_user, days=5)
        today = timezone.localdate()
        token = make_token(self.mgr_emp, today.year, today.month)
        r = self.client.post(f'/hris/api/manager-feedback/{token}/submit/', {
            'profile': str(self.staff_prof.pk),
            'strengths': 'x', 'concerns': 'y', 'support_provided': 'z',
            'overall_rating': 'ME',
        })
        self.assertEqual(r.status_code, 400, r.content[:400])
        self.assertFalse(MonthlyCheckIn.objects.filter(profile=self.staff_prof).exists())

    def test_a_tampered_token_is_refused(self):
        r = self.client.get('/hris/api/manager-feedback/not-a-real-token/')
        self.assertEqual(r.status_code, 400)


# ── the in-app manager path (CFO 2026-08-07: "what else are we making hard") ──

class ManagerCanApproveInAppTest(Base):
    """A line manager who is NOT on the five-person HRIS whitelist could not
    approve leave in omni at all — only through the emailed link. Same
    authority, two doors; only one of them worked."""

    def test_manager_sees_their_own_queue(self):
        self.pending_leave()
        self.client.force_authenticate(self.mgr_user)
        r = self.client.get('/hris/api/leave-requests/queue/')
        self.assertEqual(r.status_code, 200, r.data)

    def test_manager_can_approve_their_own_report(self):
        lr = self.pending_leave()
        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(_decide_url(lr), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)

    def test_manager_cannot_approve_someone_elses_report(self):
        other_user = User.objects.create_user('ogother', 'ogother@alphadirect.co.bw', 'x')
        other_emp = Employee.objects.create(
            company=self.co, employee_number='OG-9', full_name='Not Mine',
            email='ogother@alphadirect.co.bw', user=other_user)
        UserCompanyAccess.objects.get_or_create(user=other_user, company=self.co)
        other_prof = HRISProfile.objects.create(employee=other_emp)   # no manager
        lr = LeaveRequest.objects.create(
            profile=other_prof, leave_type=self.annual,
            start_date=timezone.localdate() + dt.timedelta(days=3),
            end_date=timezone.localdate() + dt.timedelta(days=4),
            days=2, status=LeaveRequest.Status.PENDING)

        self.client.force_authenticate(self.mgr_user)
        r = self.client.post(_decide_url(lr), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.data)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)

    def test_an_ordinary_employee_still_cannot_approve(self):
        lr = self.pending_leave()
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(_decide_url(lr), {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.data)


# ── DeepSeek review 2026-08-07: a decline must actually stop it ─────────────

class ExecDeclineIsFinalTest(Base):
    def test_a_declined_signoff_still_blocks_the_approval(self):
        """The block used to lift the moment an executive said NO — the exact
        opposite of the decision. Only a signature clears the way."""
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        exec_signoff_service.decide(so, self.cfo_user, False, 'Clear the recon first.')

        still = exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk)
        self.assertIsNotNone(still, 'a decline must keep blocking, not release')
        self.assertEqual(still.status, ExecSignoff.Status.DECLINED)
        self.assertIn('declined', exec_signoff_service.block_message(still).lower())

    # REMOVED 2026-08-18: incentives no longer use the maker/approver overdue
    # countersignature gate. That gate blocked an APPROVER (Unami) from signing
    # because HE carried overdue work — wrong, managers always do. The incentive
    # discipline check now looks at the EMPLOYEE receiving the money, at submit
    # (see hris/tests/test_incentives.py). Leave + loans keep this gate.

    def test_an_approval_decision_cannot_overturn_a_decline(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        exec_signoff_service.decide(so, self.cfo_user, False, 'No.')
        # auto_resolve is how loans/incentives self-satisfy the signature; it
        # must refuse to touch a decision that has already been made.
        self.assertIsNone(exec_signoff_service.auto_resolve(
            ExecSignoff.Module.LEAVE, lr.pk, self.cfo_user))
        so.refresh_from_db()
        self.assertEqual(so.status, ExecSignoff.Status.DECLINED)


class NoSignerMeansNoDeadlockTest(Base):
    def test_without_an_active_ceo_or_cfo_no_block_is_raised(self):
        """Nobody could ever clear it, so the application would be frozen for
        good. Log loudly and let the normal approval route run."""
        UserProfile.objects.filter(user=self.cfo_user).update(is_active=False)
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        with self.assertLogs('hris.exec_signoff_service', level='ERROR') as logs:
            so = exec_signoff_service.require_signoff(
                ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        self.assertIsNone(so)
        self.assertIn('no active CEO/CFO', ' '.join(logs.output))
        self.assertIsNone(
            exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk))


# ── DeepSeek review round 2 fixes ───────────────────────────────────────────

class DeclinePropagatesToEveryModuleTest(Base):
    def test_a_declined_loan_is_actually_declined(self):
        from hris.exec_signoff_actions import _refuse_underlying
        from staff_loans.models import StaffLoanApplication
        app = StaffLoanApplication.objects.create(
            employee=self.staff_emp, loan_type=StaffLoanApplication.LoanType.STAFF,
            amount_requested=5000, term_months_requested=6,
            status=StaffLoanApplication.Status.PENDING_CFO)
        so = ExecSignoff.objects.create(
            module=ExecSignoff.Module.LOAN, object_id=app.pk,
            applicant=self.staff_user, applicant_name='Otto Staff')
        exec_signoff_service.decide(so, self.cfo_user, False, 'Clear the recon.')
        _refuse_underlying(so)
        app.refresh_from_db()
        self.assertEqual(app.status, StaffLoanApplication.Status.DECLINED)
        self.assertIn('overdue work', app.decline_reason)

    def test_a_declined_incentive_is_actually_rejected(self):
        from hris.exec_signoff_actions import _refuse_underlying
        from hris.incentive_models import IncentiveRequest
        req = IncentiveRequest.objects.create(
            title='May incentives', period='2026-05', maker=self.mgr_user,
            company=self.co, status=IncentiveRequest.Status.PENDING)
        so = ExecSignoff.objects.create(
            module=ExecSignoff.Module.INCENTIVE, object_id=req.pk,
            applicant=self.mgr_user, applicant_name='Mo Manager')
        exec_signoff_service.decide(so, self.cfo_user, False, 'Not now.')
        _refuse_underlying(so)
        req.refresh_from_db()
        self.assertEqual(req.status, IncentiveRequest.Status.REJECTED)


class LoanRejectionIsNotASignatureTest(Base):
    def test_rejecting_a_loan_does_not_record_an_executive_signature(self):
        from django.core.exceptions import ValidationError
        from staff_loans import services as loan_services
        from staff_loans.models import StaffLoanApplication
        app = StaffLoanApplication.objects.create(
            employee=self.staff_emp, loan_type=StaffLoanApplication.LoanType.STAFF,
            amount_requested=5000, term_months_requested=6,
            status=StaffLoanApplication.Status.PENDING_CFO)
        so = ExecSignoff.objects.create(
            module=ExecSignoff.Module.LOAN, object_id=app.pk,
            applicant=self.staff_user, applicant_name='Otto Staff')
        self.cfo_user.is_superuser = True
        self.cfo_user.save(update_fields=['is_superuser'])
        # A rejection with no reason must fail — and must NOT have already
        # stamped the sign-off as signed on the way in.
        with self.assertRaises(ValidationError):
            loan_services.cfo_decide(app, self.cfo_user, approve=False, decline_reason='')
        so.refresh_from_db()
        self.assertEqual(so.status, ExecSignoff.Status.PENDING)


@override_settings(ELRA_PERF_ENABLED=True)
class FeedbackTokenHygieneTest(Base):
    def test_a_terminated_managers_link_stops_working(self):
        from hris.manager_feedback_actions import make_token
        today = timezone.localdate()
        token = make_token(self.mgr_emp, today.year, today.month)
        self.mgr_emp.status = Employee.Status.TERMINATED
        self.mgr_emp.save(update_fields=['status'])
        r = self.client.get(f'/hris/api/manager-feedback/{token}/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('no longer active', r.content.decode())

    def test_a_double_tap_saves_once_and_says_so(self):
        from hris.manager_feedback_actions import make_token
        from hris.performance_feedback_models import MonthlyCheckIn
        today = timezone.localdate()
        token = make_token(self.mgr_emp, today.year, today.month)
        payload = {'profile': str(self.staff_prof.pk), 'strengths': 'Good month.',
                   'concerns': '', 'support_provided': '', 'overall_rating': 'ME'}
        first = self.client.post(f'/hris/api/manager-feedback/{token}/submit/', payload)
        self.assertEqual(first.status_code, 200, first.content[:300])
        second = self.client.post(f'/hris/api/manager-feedback/{token}/submit/', payload)
        self.assertIn(second.status_code, (200, 409))
        self.assertEqual(
            MonthlyCheckIn.objects.filter(
                profile=self.staff_prof, period_year=today.year,
                period_month=today.month).count(), 1)


class DecideGuardsItselfTest(Base):
    def test_a_non_executive_cannot_sign_even_by_calling_decide_directly(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        with self.assertRaises(exec_signoff_service.SignoffRefused):
            exec_signoff_service.decide(so, self.mgr_user, True)
        so.refresh_from_db()
        self.assertEqual(so.status, ExecSignoff.Status.PENDING)

    def test_a_decided_signoff_cannot_be_decided_again(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        exec_signoff_service.decide(so, self.cfo_user, True)
        with self.assertRaises(exec_signoff_service.SignoffRefused):
            exec_signoff_service.decide(so, self.cfo_user, False)


@override_settings(ELRA_PERF_ENABLED=True)
class ReportWithoutALoginTest(Base):
    def test_feedback_works_for_someone_with_no_omni_login(self):
        """61 of 64 staff are login-linked; the rest must not 500 the page."""
        from hris.manager_feedback_actions import make_token
        from hris.performance_feedback_models import MonthlyCheckIn
        nolog = Employee.objects.create(
            company=self.co, employee_number='OG-7', full_name='No Login',
            email='ognologin@alphadirect.co.bw')          # no user link
        prof = HRISProfile.objects.create(employee=nolog, manager=self.mgr_emp)
        today = timezone.localdate()
        token = make_token(self.mgr_emp, today.year, today.month)
        r = self.client.post(f'/hris/api/manager-feedback/{token}/submit/', {
            'profile': str(prof.pk), 'strengths': 'Steady.',
            'concerns': '', 'support_provided': '', 'overall_rating': 'ME'})
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(MonthlyCheckIn.objects.filter(profile=prof).exists())


# ── Fable review 2026-08-07: what TEAM_CAPS actually opened ─────────────────

class FinanceTitleWasAlreadyWhitelistedTest(Base):
    """Fable's concern was that hris_role() maps the finance titles to role
    'hr' — which holds approve_team_leave — so the lighter TEAM_CAPS tier
    might have handed a finance-titled account the whole company's queue.

    It did not: user_can_access_hris() whitelists cfo / finance_manager /
    financial_controller / hr_manager / ceo / coo BY TITLE, so those accounts
    were already through the privileged tier before this change. Pinning that
    here, because if the title whitelist is ever narrowed the guards in
    leave_queue and _may_view_certificate become the only thing standing
    between a finance title and every employee's medical certificate."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.fc_user = User.objects.create_user(
            'ogfc', 'ogfc@alphadirect.co.bw', 'x', first_name='Fin', last_name='Controller')
        UserProfile.objects.update_or_create(
            user=cls.fc_user,
            defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER, 'is_active': True})
        UserCompanyAccess.objects.get_or_create(user=cls.fc_user, company=cls.co)

    def test_a_finance_title_resolves_to_hr_and_was_already_whitelisted(self):
        from core.hris_access import hris_role, user_can_access_hris
        self.assertEqual(hris_role(self.fc_user), 'hr')
        self.assertTrue(
            user_can_access_hris(self.fc_user),
            'finance titles are whitelisted BY TITLE — TEAM_CAPS granted them nothing new')


class PlainManagerSeesOnlyTheirTeamTest(Base):
    """Role 'mgr' is the persona TEAM_CAPS genuinely let in for the first time.
    Everything it can now reach must be its own team and nothing else."""

    def test_no_company_wide_oversight(self):
        from core.hris_access import hris_role, user_can_access_hris
        self.assertEqual(hris_role(self.mgr_user), 'mgr')
        self.assertFalse(user_can_access_hris(self.mgr_user))

        other_emp = Employee.objects.create(
            company=self.co, employee_number='OG-8', full_name='Not Mine',
            email='ognotmine@alphadirect.co.bw')
        other_prof = HRISProfile.objects.create(employee=other_emp)   # no manager
        LeaveRequest.objects.create(
            profile=other_prof, leave_type=self.annual,
            start_date=timezone.localdate() + dt.timedelta(days=3),
            end_date=timezone.localdate() + dt.timedelta(days=4),
            days=2, status=LeaveRequest.Status.PENDING)
        self.pending_leave()          # Otto Staff — genuinely theirs

        self.client.force_authenticate(self.mgr_user)
        r = self.client.get('/hris/api/leave-requests/queue/')
        self.assertEqual(r.status_code, 200, r.data)
        names = {row['employee'] for row in r.data['pending']}
        self.assertIn('Otto Staff', names)
        self.assertNotIn('Not Mine', names,
                         'the team tier must never show another team\'s leave')

    def test_cannot_pull_another_teams_medical_certificate(self):
        from hris.leave_admin import _may_view_certificate
        other_emp = Employee.objects.create(
            company=self.co, employee_number='OG-10', full_name='Someone Else',
            email='ogelse@alphadirect.co.bw')
        other_prof = HRISProfile.objects.create(employee=other_emp)
        theirs = LeaveRequest.objects.create(
            profile=other_prof, leave_type=self.sick,
            start_date=timezone.localdate() + dt.timedelta(days=2),
            end_date=timezone.localdate() + dt.timedelta(days=3),
            days=2, status=LeaveRequest.Status.PENDING)
        self.assertFalse(
            _may_view_certificate(self.mgr_user, theirs),
            'staff medical data must stay behind the whitelist for other teams')

    def test_can_see_their_own_reports_certificate(self):
        from hris.leave_admin import _may_view_certificate
        ours = self.pending_leave(leave_type=self.sick)
        self.assertTrue(_may_view_certificate(self.mgr_user, ours))

    def test_the_whitelist_guard_does_not_break_real_hr(self):
        """The two new `and user_can_access_hris(...)` guards must not take
        anything away from someone who genuinely holds HRIS access."""
        from hris.leave_admin import _may_view_certificate
        hr_user = User.objects.create_user('oghr', 'oghr@alphadirect.co.bw', 'x')
        hr_user.is_superuser = True
        hr_user.save(update_fields=['is_superuser'])
        UserCompanyAccess.objects.get_or_create(user=hr_user, company=self.co)
        anyones = self.pending_leave(leave_type=self.sick)
        self.assertTrue(_may_view_certificate(hr_user, anyones))


# ── the second door: applying for leave from an email link, no sign-in ──────

class NoLoginApplyIsGatedTooTest(Base):
    """main gained a no-login "apply for leave from an email link" path the
    same day. Without the gate on it too, that link would BE the way round the
    executive countersignature — apply from your phone and skip the check."""

    def _url(self):
        from hris.leave_apply_nologin import make_leave_apply_token
        return f'/hris/api/apply-leave/{make_leave_apply_token(self.staff_prof)}/'

    def test_applying_by_email_link_raises_the_countersignature(self):
        self.make_overdue_task(self.staff_user, days=5)
        r = self.client.post(self._url(), {
            'leave_type': str(self.annual.id),
            'start_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
            'end_date': (timezone.localdate() + dt.timedelta(days=8)).isoformat(),
            'reason': 'Family trip.',
        })
        self.assertEqual(r.status_code, 200, r.content[:300])
        lr = LeaveRequest.objects.filter(profile=self.staff_prof).order_by('-created_at').first()
        self.assertIsNotNone(lr)
        self.assertIsNotNone(
            exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk),
            'the no-login link must not be a way round the executive check')
        self.assertIn('CEO or CFO', r.content.decode())

    def test_sick_leave_by_email_link_is_still_exempt(self):
        self.make_overdue_task(self.staff_user, days=30)
        r = self.client.post(self._url(), {
            'leave_type': str(self.sick.id),
            'start_date': timezone.localdate().isoformat(),
            'end_date': timezone.localdate().isoformat(),
            'reason': 'Flu.',
        })
        self.assertEqual(r.status_code, 200, r.content[:300])
        lr = LeaveRequest.objects.filter(
            profile=self.staff_prof, leave_type=self.sick).order_by('-created_at').first()
        self.assertIsNotNone(lr)
        self.assertIsNone(
            exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk),
            'nobody chases a task before reporting they are ill')

    def test_no_overdue_work_means_no_gate_on_the_link_either(self):
        r = self.client.post(self._url(), {
            'leave_type': str(self.annual.id),
            'start_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
            'end_date': (timezone.localdate() + dt.timedelta(days=8)).isoformat(),
            'reason': 'Family trip.',
        })
        self.assertEqual(r.status_code, 200, r.content[:300])
        lr = LeaveRequest.objects.filter(profile=self.staff_prof).order_by('-created_at').first()
        self.assertIsNone(
            exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk))
        self.assertNotIn('CEO or CFO', r.content.decode())


# ── telling the person WHY (CFO 2026-08-07, second pass) ────────────────────

class StaffAreToldWhyTest(Base):
    """The first cut told people the rule but not the work: one line of text on
    leave, nothing at all on loans and incentives. Every submit response now
    carries the same block, and it NAMES the tasks."""

    def test_leave_response_names_the_tasks(self):
        # CFO ruling (feature_views apply): an overdue task HARD-BLOCKS leave of
        # any type for anyone (400) — no exec-signoff soft path on this endpoint.
        # The block must still NAME the work holding the person up.
        self.make_overdue_task(self.staff_user, days=5, title='Bank recon June')
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, {
            'type': 'annual',
            'start_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
            'end_date': (timezone.localdate() + dt.timedelta(days=8)).isoformat(),
            'reason': 'Family trip.', 'reason_category': 'personal',
        })
        self.assertEqual(r.status_code, 400, r.data)
        # the person is told WHICH work is holding them up — in the message …
        self.assertIn('Bank recon June', r.data['detail'])
        self.assertIn('cannot be', r.data['detail'])
        # … and in the structured list
        titles = [t['title'] for t in r.data['overdue_tasks']]
        self.assertIn('Bank recon June', titles,
                      'the person must be told WHICH work is holding them up')
        self.assertIn('due_at', r.data['overdue_tasks'][0])

    def test_a_clean_employee_gets_no_warning_at_all(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.post(APPLY_URL, {
            'type': 'annual',
            'start_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
            'end_date': (timezone.localdate() + dt.timedelta(days=8)).isoformat(),
            'reason': 'Family trip.', 'reason_category': 'personal',
        })
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(r.data['needs_exec_signoff'])
        self.assertEqual(r.data['overdue_tasks'], [])
        self.assertEqual(r.data['overdue_message'], '')

    def test_the_pre_warning_endpoint_lists_my_own_tasks(self):
        self.make_overdue_task(self.staff_user, days=5, title='Bank recon June')
        self.client.force_authenticate(self.staff_user)
        r = self.client.get('/hris/api/my-overdue-tasks/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['days_threshold'], 2)
        self.assertEqual(r.data['tasks'][0]['title'], 'Bank recon June')

    def test_the_pre_warning_is_silent_when_nothing_is_overdue(self):
        self.client.force_authenticate(self.staff_user)
        r = self.client.get('/hris/api/my-overdue-tasks/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 0)

    def test_the_pre_warning_never_leaks_another_persons_tasks(self):
        self.make_overdue_task(self.mgr_user, days=9, title='Not yours')
        self.client.force_authenticate(self.staff_user)
        r = self.client.get('/hris/api/my-overdue-tasks/')
        self.assertEqual(r.data['count'], 0)

    # REMOVED 2026-08-18: the incentive submit response no longer carries a
    # maker-overdue signoff payload — the maker's own tasks no longer gate an
    # incentive (that blocked Unami's approval). The employee-overdue check and
    # the manager declaration are covered in hris/tests/test_incentives.py.

    def test_the_email_page_names_the_tasks_too(self):
        from hris.leave_apply_nologin import make_leave_apply_token
        self.make_overdue_task(self.staff_user, days=5, title='Bank recon June')
        token = make_leave_apply_token(self.staff_prof)
        r = self.client.post(f'/hris/api/apply-leave/{token}/', {
            'leave_type': str(self.annual.id),
            'start_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
            'end_date': (timezone.localdate() + dt.timedelta(days=8)).isoformat(),
            'reason': 'Family trip.',
        })
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.content.decode()
        self.assertIn('Bank recon June', body)
        self.assertIn('days late', body)


# ── the team list a manager taps (CFO 2026-08-07) ───────────────────────────

@override_settings(ELRA_PERF_ENABLED=True)
class TappableTeamListTest(Base):
    """"When I open this I should see the staff list, I should be able to click
    here and give feedback — we need to make performance coaching easy." A
    dropdown above a form made the manager hunt; a list of tappable rows does
    not."""

    def _token(self):
        from hris.manager_feedback_actions import make_token
        t = timezone.localdate()
        return make_token(self.mgr_emp, t.year, t.month), t

    def test_the_landing_page_is_the_team_list(self):
        token, _ = self._token()
        r = self.client.get(f'/hris/api/manager-feedback/{token}/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Otto Staff', body)
        # each person is a whole tappable row pointing at their own form
        self.assertIn(f'?profile={self.staff_prof.pk}', body)
        self.assertIn('Give feedback →', body)
        # the form is NOT on the landing page — you pick a person first
        self.assertNotIn('What they did well', body)

    def test_tapping_a_person_opens_their_form(self):
        token, _ = self._token()
        r = self.client.get(f'/hris/api/manager-feedback/{token}/?profile={self.staff_prof.pk}')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('Otto Staff', body)
        self.assertIn('What they did well', body)
        self.assertIn('Back to my team', body)

    def test_the_list_flags_who_has_overdue_work(self):
        self.make_overdue_task(self.staff_user, days=5)
        token, _ = self._token()
        r = self.client.get(f'/hris/api/manager-feedback/{token}/')
        self.assertIn('1 overdue', r.content.decode())

    def test_a_bad_profile_id_falls_back_to_the_list_not_a_crash(self):
        token, _ = self._token()
        r = self.client.get(f'/hris/api/manager-feedback/{token}/?profile=not-a-uuid')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Give feedback →', r.content.decode())

    def test_saving_offers_the_next_person(self):
        token, t = self._token()
        r = self.client.post(f'/hris/api/manager-feedback/{token}/submit/', {
            'profile': str(self.staff_prof.pk), 'strengths': 'Solid month.',
            'concerns': '', 'support_provided': '', 'overall_rating': 'ME'})
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertIn('Saved', r.content.decode())


class RefundQueueIsPersonalTest(Base):
    """A card headed "Refunds for YOU to process" showed the CFO the whole
    company's queue, because the superuser bypass skipped the my-queue filter.
    He was told to pay three refunds that were routed to somebody else."""

    def _claim(self, approver):
        from hris.expense_claim_models import ExpenseClaim
        return ExpenseClaim.objects.create(
            profile=UserProfile.objects.get_or_create(user=self.staff_user)[0],
            amount=500, description='Client engagement', approver=approver,
            status=ExpenseClaim.Status.SUBMITTED,
            expense_date=timezone.localdate())

    def test_a_superuser_sees_only_refunds_routed_to_them(self):
        other = User.objects.create_user('ogacct', 'ogacct@alphadirect.co.bw', 'x')
        self._claim(approver=other)          # somebody else's queue
        mine = self._claim(approver=self.cfo_user)

        self.cfo_user.is_superuser = True
        self.cfo_user.save(update_fields=['is_superuser'])
        self.client.force_authenticate(self.cfo_user)
        r = self.client.get('/api/v1/expense-claims/queue/')
        self.assertEqual(r.status_code, 200, r.data)
        ids = {row['id'] for row in r.data}
        self.assertIn(str(mine.id), ids)
        self.assertEqual(len(ids), 1,
                         'a personal queue must never show another person\'s work')


# ── /fabe panel findings 2026-08-07 ─────────────────────────────────────────

class SignerFallbackTest(Base):
    """OpenAI judge: "the no-signer fallback bypasses the required executive
    gate". It did — with no CEO/CFO profile the control quietly switched itself
    off company-wide. Superusers now backstop it, so it never disappears and
    never deadlocks."""

    def test_superusers_backstop_when_no_titled_signer_exists(self):
        root = User.objects.create_user('ogroot', 'ogroot@alphadirect.co.bw', 'x')
        root.is_superuser = True
        root.save(update_fields=['is_superuser'])
        UserProfile.objects.filter(user=self.cfo_user).update(is_active=False)

        self.assertIn(root, exec_signoff_service.signer_users())
        self.assertTrue(exec_signoff_service.user_can_sign(root))

        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        self.assertIsNotNone(so, 'the gate must not switch itself off')
        exec_signoff_service.decide(so, root, True)
        self.assertIsNone(
            exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk))

    def test_a_superuser_cannot_sign_around_a_real_cfo(self):
        root = User.objects.create_user('ogroot2', 'ogroot2@alphadirect.co.bw', 'x')
        root.is_superuser = True
        root.save(update_fields=['is_superuser'])
        # cfo_user is active and titled, so the fallback tier must stay closed.
        self.assertFalse(exec_signoff_service.user_can_sign(root))
        self.assertNotIn(root, exec_signoff_service.signer_users())


class DecideIsRaceSafeTest(Base):
    """Gemini + DeepSeek judges: decide() read is_pending then wrote, so two
    executives on the same emailed link could both pass the check and the
    second would silently overwrite the first — an approval quietly replacing
    a decline."""

    def test_the_second_decision_loses_and_says_so(self):
        self.make_overdue_task(self.staff_user, days=5)
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)

        # Two in-memory handles on the same row — exactly what two open links are.
        a = ExecSignoff.objects.get(pk=so.pk)
        b = ExecSignoff.objects.get(pk=so.pk)
        exec_signoff_service.decide(a, self.cfo_user, False, 'Clear the recon.')
        with self.assertRaises(exec_signoff_service.SignoffRefused):
            exec_signoff_service.decide(b, self.cfo_user, True, 'Actually fine.')

        so.refresh_from_db()
        self.assertEqual(so.status, ExecSignoff.Status.DECLINED,
                         'an approval must never quietly overwrite a decline')
        self.assertEqual(so.decision_notes, 'Clear the recon.')


class ThresholdIsTunableTest(Base):
    def test_the_threshold_reads_from_settings(self):
        from django.test import override_settings
        with override_settings(HRIS_OVERDUE_BLOCK_DAYS=7):
            self.assertEqual(overdue_gate.overdue_days_threshold(), 7)
            self.make_overdue_task(self.staff_user, days=5)
            self.assertEqual(overdue_gate.overdue_summary(self.staff_user)['count'], 0,
                             '5 days late must not trip a 7-day threshold')
        self.assertEqual(overdue_gate.overdue_days_threshold(), 2)


class GateFailsSafeNotOpenTest(Base):
    """All three /fabe judges, unanimously: swallowing a check failure let the
    application through CLEAN — the control could switch itself off with only a
    log line. Blocking would be worse (nobody should be unable to ASK for leave
    because a query broke), so it now goes through FLAGGED for a human."""

    def test_a_broken_check_raises_a_review_signoff_instead_of_passing_clean(self):
        from unittest.mock import patch
        lr = self.pending_leave()
        with patch('hris.overdue_gate.overdue_summary', side_effect=RuntimeError('db down')):
            with self.assertLogs('hris.exec_signoff_service', level='ERROR'):
                so = exec_signoff_service.require_signoff(
                    ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        self.assertIsNotNone(so, 'a broken check must not pass silently')
        self.assertTrue(so.overdue_snapshot.get('check_failed'))
        self.assertIn('could not run', so.reason)
        # and the manager really is held until someone looks at it
        self.assertIsNotNone(
            exec_signoff_service.blocking_signoff(ExecSignoff.Module.LEAVE, lr.pk))

    def test_a_broken_check_never_stops_the_person_applying(self):
        from unittest.mock import patch
        self.client.force_authenticate(self.staff_user)
        with patch('hris.overdue_gate.overdue_summary', side_effect=RuntimeError('db down')):
            r = self.client.post(APPLY_URL, {
                'type': 'annual',
                'start_date': (timezone.localdate() + dt.timedelta(days=7)).isoformat(),
                'end_date': (timezone.localdate() + dt.timedelta(days=8)).isoformat(),
                'reason': 'Family trip.', 'reason_category': 'personal',
            })
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(LeaveRequest.objects.filter(profile=self.staff_prof).exists())

    def test_sick_leave_is_exempt_even_when_the_check_is_broken(self):
        from unittest.mock import patch
        lr = self.pending_leave(leave_type=self.sick)
        with patch('hris.overdue_gate.overdue_summary', side_effect=RuntimeError('db down')):
            so = exec_signoff_service.require_signoff(
                ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.sick)
        self.assertIsNone(so, 'illness is exempt before any check is attempted')


class FailSafeCopyIsHonestTest(Base):
    """Fable: the fail-safe path reused the happy-path wording, so it told the
    applicant "you have 0 tasks more than 2 days past the due date" over an
    empty list, told the approver the same, and told the executive the person
    was "carrying overdue work". All three were untrue."""

    def _failed_signoff(self):
        from unittest.mock import patch
        lr = self.pending_leave()
        with patch('hris.overdue_gate.overdue_summary', side_effect=RuntimeError('db down')):
            with self.assertLogs('hris.exec_signoff_service', level='ERROR'):
                return lr, exec_signoff_service.require_signoff(
                    ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)

    def test_the_applicant_is_not_accused_of_zero_overdue_tasks(self):
        from hris import overdue_gate
        _, so = self._failed_signoff()
        why = overdue_gate.signoff_payload(so)
        self.assertTrue(why['needs_exec_signoff'])
        self.assertTrue(why['check_failed'])
        self.assertEqual(why['overdue_tasks'], [])
        self.assertNotIn('0 task', why['overdue_message'])
        self.assertIn('could not check', why['overdue_message'].lower())

    def test_the_approver_is_told_the_truth(self):
        _, so = self._failed_signoff()
        msg = exec_signoff_service.block_message(so)
        self.assertIn('could not run', msg)
        self.assertNotIn('0 task', msg)

    def test_the_signer_page_does_not_show_an_empty_overdue_box(self):
        from hris.exec_signoff_actions import make_token
        _, so = self._failed_signoff()
        token = make_token(so, self.cfo_user)
        r = self.client.get(f'/hris/api/exec-signoff/{token}/')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('could not run', body)
        self.assertNotIn('while carrying', body)

    def test_the_signer_email_does_not_accuse_either(self):
        from hris.exec_signoff_email import build_exec_signoff_email
        _, so = self._failed_signoff()
        mail_d = build_exec_signoff_email(so, self.cfo_user)
        self.assertIsNotNone(mail_d)
        self.assertIn('could not', mail_d['html'])
        self.assertNotIn('while carrying work that is past its due date', mail_d['html'])

    def test_the_normal_path_still_names_the_work(self):
        from hris import overdue_gate
        self.make_overdue_task(self.staff_user, days=5, title='Bank recon June')
        lr = self.pending_leave()
        so = exec_signoff_service.require_signoff(
            ExecSignoff.Module.LEAVE, lr, self.staff_user, leave_type=self.annual)
        why = overdue_gate.signoff_payload(so)
        self.assertFalse(why.get('check_failed'))
        self.assertIn('Bank recon June', [t['title'] for t in why['overdue_tasks']])
        self.assertIn('more than 2 days', why['overdue_message'])


class DeclineReachesTheApplicantOnEveryModuleTest(Base):
    """Fable: only leave notified the applicant, but the signer's Done page
    claimed "they have been told why" for all three."""

    def test_a_declined_loan_emails_the_applicant(self):
        from hris.exec_signoff_actions import _refuse_underlying
        from staff_loans.models import StaffLoanApplication
        app = StaffLoanApplication.objects.create(
            employee=self.staff_emp, loan_type=StaffLoanApplication.LoanType.STAFF,
            amount_requested=5000, term_months_requested=6,
            status=StaffLoanApplication.Status.PENDING_CFO)
        so = ExecSignoff.objects.create(
            module=ExecSignoff.Module.LOAN, object_id=app.pk,
            applicant=self.staff_user, applicant_name='Otto Staff')
        exec_signoff_service.decide(so, self.cfo_user, False, 'Clear the recon.')
        mail.outbox = []
        _refuse_underlying(so)
        to_them = [m for m in mail.outbox if 'ogstaff@alphadirect.co.bw' in m.to]
        self.assertTrue(to_them, 'the applicant was never told their loan was declined')
        self.assertIn('declined', to_them[0].subject.lower())

    def test_a_declined_incentive_emails_the_maker(self):
        from hris.exec_signoff_actions import _refuse_underlying
        from hris.incentive_models import IncentiveRequest
        req = IncentiveRequest.objects.create(
            title='May incentives', period='2026-05', maker=self.mgr_user,
            company=self.co, status=IncentiveRequest.Status.PENDING)
        so = ExecSignoff.objects.create(
            module=ExecSignoff.Module.INCENTIVE, object_id=req.pk,
            applicant=self.mgr_user, applicant_name='Mo Manager')
        exec_signoff_service.decide(so, self.cfo_user, False, 'Not now.')
        mail.outbox = []
        _refuse_underlying(so)
        self.assertTrue([m for m in mail.outbox if 'ogmgr@alphadirect.co.bw' in m.to])
