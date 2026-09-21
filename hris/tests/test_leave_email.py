"""Tests for the rich leave-approval email + one-click token approval
(CFO 2026-07-14)."""
from __future__ import annotations

import datetime as _dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import OmniTask
from hris.leave_actions import make_leave_action_token
from hris.leave_email import build_leave_email
from hris.models import HRISProfile, LeaveRequest, LeaveType
from payroll.models import Employee


class LeaveEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mgr_user = User.objects.create_user(
            'boss', email='boss@alphadirect.co.bw', password='x',
            first_name='Big', last_name='Boss')
        # Manager needs a role that holds approve_team_leave. hris_role treats a
        # user with at least one direct report as 'mgr'; make the manager
        # is_administrator so hris_role -> 'admin' (holds approve_team_leave).
        from core.models import UserProfile
        UserProfile.objects.update_or_create(
            user=cls.mgr_user, defaults={'is_administrator': True, 'is_active': True})
        cls.emp_user = User.objects.create_user(
            'worker', email='worker@alphadirect.co.bw', password='x',
            first_name='Work', last_name='Er')

        # employee_number is unique with no auto-value: two Employees created
        # without one both default to '' and collide. Give each a distinct number.
        cls.mgr_emp = Employee.objects.create(full_name='Big Boss',
                                              employee_number='E-MGR',
                                              email='boss@alphadirect.co.bw',
                                              user=cls.mgr_user)
        cls.emp = Employee.objects.create(full_name='Work Er',
                                          employee_number='E-WORKER',
                                          email='worker@alphadirect.co.bw',
                                          user=cls.emp_user)
        cls.profile = HRISProfile.objects.create(employee=cls.emp, manager=cls.mgr_emp)
        cls.annual = LeaveType.objects.create(code='annual', name='Annual Leave',
                                              default_annual_days=20, paid_pct=100)

    def _mk_leave(self, start, end):
        return LeaveRequest.objects.create(
            profile=self.profile, leave_type=self.annual,
            start_date=start, end_date=end, reason='Family time',
            status=LeaveRequest.Status.PENDING)

    def test_build_email_has_balances_and_buttons(self):
        lr = self._mk_leave(_dt.date(2026, 8, 3), _dt.date(2026, 8, 5))
        mail = build_leave_email(lr)
        self.assertIsNotNone(mail)
        self.assertEqual(mail['to'], ['boss@alphadirect.co.bw'])
        html = mail['html']
        self.assertIn('Work Er', html)
        self.assertIn('Annual (normal) days available now', html)
        self.assertIn('Sick days remaining after this leave', html)
        self.assertIn('Leave already pending', html)
        self.assertIn('/hris/api/leave-action/', html)   # one-click button
        self.assertIn('Approve', html)

    def test_no_manager_falls_back_to_exco_not_silence(self):
        """CFO instruction 2026-07-25 — supersedes the old "returns None".

        This previously asserted that a profile with no manager produced NO
        email — the request silently reached nobody, with no error and no queue.
        Three real active staff plus every `M365-` mailbox shell hit that path.
        It must now land on the EXCO catch-all instead.
        """
        User.objects.create_user('excoboard', email='excoboard@alphadirect.co.bw',
                                 password='x')
        self.profile.manager = None
        self.profile.save(update_fields=['manager'])
        lr = self._mk_leave(_dt.date(2026, 8, 3), _dt.date(2026, 8, 5))
        mail = build_leave_email(lr)
        self.assertIsNotNone(mail)
        self.assertEqual(mail['to'], ['excoboard@alphadirect.co.bw'])
        # restore
        self.profile.manager = self.mgr_emp
        self.profile.save(update_fields=['manager'])

    def test_blocklisted_shared_mailbox_never_receives_leave(self):
        """A manager record pointing at the shared admin@ box must not get it."""
        User.objects.create_user('excoboard2', email='excoboard@alphadirect.co.bw',
                                 password='x')
        self.mgr_emp.email = 'admin@alphadirect.co.bw'
        self.mgr_emp.save(update_fields=['email'])
        lr = self._mk_leave(_dt.date(2026, 8, 24), _dt.date(2026, 8, 25))
        mail = build_leave_email(lr)
        self.assertIsNotNone(mail)
        self.assertNotIn('admin@alphadirect.co.bw', mail['to'])
        self.assertEqual(mail['to'], ['excoboard@alphadirect.co.bw'])
        self.mgr_emp.email = 'boss@alphadirect.co.bw'
        self.mgr_emp.save(update_fields=['email'])

    def test_deactivated_approver_falls_back(self):
        """A blocked login (e.g. the killed shared account) must not swallow it."""
        User.objects.create_user('excoboard3', email='excoboard@alphadirect.co.bw',
                                 password='x')
        dead = User.objects.create_user('deadacct', email='dead@alphadirect.co.bw',
                                        password='x')
        dead.is_active = False
        dead.save(update_fields=['is_active'])
        lr = self._mk_leave(_dt.date(2026, 9, 1), _dt.date(2026, 9, 2))
        lr.requested_approver = dead
        lr.save(update_fields=['requested_approver'])
        mail = build_leave_email(lr)
        self.assertIsNotNone(mail)
        self.assertEqual(mail['to'], ['excoboard@alphadirect.co.bw'])

    def test_live_named_manager_still_wins(self):
        """The safety net must never steal a request from a real manager."""
        User.objects.create_user('excoboard4', email='excoboard@alphadirect.co.bw',
                                 password='x')
        lr = self._mk_leave(_dt.date(2026, 9, 8), _dt.date(2026, 9, 9))
        mail = build_leave_email(lr)
        self.assertEqual(mail['to'], ['boss@alphadirect.co.bw'])

    def test_token_page_get_is_side_effect_free(self):
        lr = self._mk_leave(_dt.date(2026, 8, 10), _dt.date(2026, 8, 11))
        token = make_leave_action_token(lr, self.mgr_user)
        url = reverse('hris:leave-action-page', args=[token])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Approve leave', resp.content)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)  # unchanged

    def test_token_submit_approves(self):
        lr = self._mk_leave(_dt.date(2026, 8, 17), _dt.date(2026, 8, 18))
        token = make_leave_action_token(lr, self.mgr_user)
        url = reverse('hris:leave-action-submit', args=[token])
        resp = self.client.post(url, {'decision': 'approve'})
        self.assertEqual(resp.status_code, 200)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(lr.approver_id, self.mgr_user.id)

    def test_token_submit_reject_with_reason(self):
        lr = self._mk_leave(_dt.date(2026, 8, 24), _dt.date(2026, 8, 25))
        token = make_leave_action_token(lr, self.mgr_user)
        url = reverse('hris:leave-action-submit', args=[token])
        resp = self.client.post(url, {'decision': 'reject', 'notes': 'Peak week'})
        self.assertEqual(resp.status_code, 200)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.REFUSED)
        self.assertEqual(lr.decision_notes, 'Peak week')

    def test_cannot_approve_own_leave(self):
        """A manager approving their OWN leave is blocked (SoD)."""
        mgr_profile = HRISProfile.objects.create(employee=self.mgr_emp, manager=None)
        lr = LeaveRequest.objects.create(
            profile=mgr_profile, leave_type=self.annual,
            start_date=_dt.date(2026, 9, 1), end_date=_dt.date(2026, 9, 2),
            status=LeaveRequest.Status.PENDING)
        token = make_leave_action_token(lr, self.mgr_user)
        url = reverse('hris:leave-action-submit', args=[token])
        resp = self.client.post(url, {'decision': 'approve'})
        self.assertEqual(resp.status_code, 403)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)  # not flipped

    def test_double_submit_is_idempotent(self):
        lr = self._mk_leave(_dt.date(2026, 10, 5), _dt.date(2026, 10, 6))
        token = make_leave_action_token(lr, self.mgr_user)
        url = reverse('hris:leave-action-submit', args=[token])
        self.client.post(url, {'decision': 'approve'})
        resp2 = self.client.post(url, {'decision': 'reject'})
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b'Already', resp2.content)
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)  # stayed approved

    def test_tasks_at_risk_flagged(self):
        lr = self._mk_leave(_dt.date(2026, 11, 2), _dt.date(2026, 11, 6))
        OmniTask.objects.create(
            assigner=self.mgr_user, assignee=self.emp_user,
            title='Month-end pack', due_at=_dt.date(2026, 11, 4),
            status=OmniTask.Status.PENDING)
        mail = build_leave_email(lr)
        self.assertIn('Month-end pack', mail['html'])


class AutoRaisedLeadSentenceTests(LeaveEmailTests):
    """The approver email must not tell a manager the employee applied for a row
    Omni raised itself (bug b7e41c7e) — and it must still say "has applied" for a
    real application.

    These build the REAL template. The first version of this fix passed a name
    that did not exist into the HTML branch; every caller wraps build_leave_email
    in `except Exception`, so the NameError would have silently stopped EVERY
    leave approval email on prod while the mocked tests stayed green.
    """

    def _mk_td(self, reason):
        td, _ = LeaveType.objects.get_or_create(
            code='td_deduct',
            defaults={'name': 'Time Doctor Deduction (unpaid - tracked-hours shortfall)',
                      'default_annual_days': 0, 'paid_pct': 0, 'is_paid': False})
        return LeaveRequest.objects.create(
            profile=self.profile, leave_type=td,
            start_date=_dt.date(2026, 9, 1), end_date=_dt.date(2026, 9, 2),
            reason=reason, status=LeaveRequest.Status.PENDING)

    def test_cron_raised_deduction_says_the_employee_did_not_apply(self):
        lr = self._mk_td('Auto-applied: no Time Doctor tracking and no explanation '
                         'by the 4:00 pm cut-off, Tue 02 Sep 2026.')
        html = build_leave_email(lr)['html']
        self.assertIn('did not apply for this', html)
        self.assertNotIn('has applied for', html)

    def test_self_applied_deduction_still_reads_as_an_application(self):
        lr = self._mk_td('I forgot to track on Tuesday, please deduct the day.')
        html = build_leave_email(lr)['html']
        self.assertIn('has applied for', html)
        self.assertNotIn('did not apply for this', html)

    def test_a_normal_leave_still_reads_as_an_application(self):
        lr = self._mk_leave(_dt.date(2026, 8, 3), _dt.date(2026, 8, 5))
        html = build_leave_email(lr)['html']
        self.assertIn('has applied for', html)
        self.assertNotIn('did not apply for this', html)
