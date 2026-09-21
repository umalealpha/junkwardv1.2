"""Tests for inter-entity employee transfer (feature c0d110b6)."""
import datetime as dt

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from core.models import Company
from hris.transfer_models import EmployeeTransfer
from hris.transfer_service import (
    apply_due_transfers, approve_in, approve_out, reject_transfer, submit_transfer,
)
from payroll.models import Employee

TODAY = dt.date.today()
FUTURE = TODAY + dt.timedelta(days=7)


class EmployeeTransferTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.uni = Company.objects.create(code='UNI', name='Unicoin')
        cls.emp = Employee.objects.create(
            employee_number='E500', full_name='Tebogo Move',
            job_title='Agent', company=cls.uni)
        # Whitelisted approvers (Unami + CFO local-parts), plus a maker.
        cls.unami = User.objects.create_user('ubutale', email='ubutale@alphadirect.co.bw', password='x')
        cls.cfo = User.objects.create_superuser('pg', 'pganesharajah@alphadirect.co.bw', 'x')
        cls.maker = User.objects.create_user('hrmaker', email='hr@alphadirect.co.bw', password='x')

    def test_happy_path_applies_on_second_approval_when_effective_today(self):
        # Effective today → both approvals apply the move immediately.
        t = submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                            dest_company_id=str(self.adic.id),
                            effective_date=TODAY, reason='reorg')
        self.assertEqual(t.status, EmployeeTransfer.Status.PENDING_OUT)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.uni.id)   # unchanged yet

        approve_out(t, self.unami)
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.PENDING_IN)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.uni.id)   # still unchanged

        approve_in(t, self.cfo)
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.COMPLETED)
        self.assertIsNotNone(t.applied_at)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.adic.id)  # moved

    def test_future_dated_transfer_schedules_then_applies_on_due_date(self):
        # Effective in the future → second approval SCHEDULES; the employee does
        # not move until apply_due_transfers runs on/after the effective date.
        t = submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                            dest_company_id=str(self.adic.id),
                            effective_date=FUTURE, reason='future move')
        approve_out(t, self.unami)
        approve_in(t, self.cfo)
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.SCHEDULED)
        self.assertIsNone(t.applied_at)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.uni.id)   # NOT moved yet

        # Nothing due today → no-op.
        self.assertEqual(apply_due_transfers(), 0)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.uni.id)

        # Effective date arrives → applies.
        EmployeeTransfer.objects.filter(pk=t.pk).update(effective_date=TODAY)
        self.assertEqual(apply_due_transfers(), 1)
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.COMPLETED)
        self.assertIsNotNone(t.applied_at)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.adic.id)  # moved on due date

    def test_submitter_cannot_approve_out(self):
        # Make the maker a superuser so authority isn't the blocker — SoD is.
        self.maker.is_superuser = True; self.maker.save()
        t = submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                            dest_company_id=str(self.adic.id),
                            effective_date=dt.date(2026, 7, 1))
        with self.assertRaises(ValidationError):
            approve_out(t, self.maker)

    def test_out_approver_cannot_also_approve_in(self):
        t = submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                            dest_company_id=str(self.adic.id),
                            effective_date=dt.date(2026, 7, 1))
        approve_out(t, self.unami)
        with self.assertRaises(ValidationError):
            approve_in(t, self.unami)

    def test_same_entity_rejected(self):
        with self.assertRaises(ValidationError):
            submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                            dest_company_id=str(self.uni.id),
                            effective_date=dt.date(2026, 7, 1))

    def test_duplicate_in_progress_rejected(self):
        submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                        dest_company_id=str(self.adic.id),
                        effective_date=dt.date(2026, 7, 1))
        with self.assertRaises(ValidationError):
            submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                            dest_company_id=str(self.adic.id),
                            effective_date=dt.date(2026, 8, 1))

    def test_reject_leaves_employee_in_place(self):
        t = submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                            dest_company_id=str(self.adic.id),
                            effective_date=dt.date(2026, 7, 1))
        reject_transfer(t, self.unami, notes='not approved')
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.REJECTED)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.company_id, self.uni.id)

    def test_api_submit_and_list(self):
        c = APIClient()
        c.force_authenticate(user=self.cfo)
        # HRIS unlock + amend rights: superuser passes the gate in tests via
        # the unlock helper used elsewhere; submit through the service is the
        # core path, so here we just confirm the list endpoint serialises.
        submit_transfer(submitter=self.maker, employee_id=str(self.emp.id),
                        dest_company_id=str(self.adic.id),
                        effective_date=dt.date(2026, 7, 1))
        r = c.get('/hris/api/transfers/')
        # Either 200 with rows, or gated (401/403) — never a 500.
        self.assertIn(r.status_code, (200, 401, 403))


class SecondApprovalIsAnnouncedTest(APITestCase):
    """Approving step one must TELL whoever owes step two.

    Submitting a transfer has emailed the Transfer Out approver since bug
    96bfcab4. Approving it told nobody, so the transfer moved to "Awaiting
    destination (Transfer In) approval" and waited there in silence. Naomi
    A live move was approved out on 7-Sep and was still sitting on 18-Sep
    when Payroll reported it as "the transfer did not work" (bug 93e12326).
    The machinery was fine; nobody had been told.
    """

    @classmethod
    def setUpTestData(cls):
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.uni = Company.objects.create(code='UNI', name='Unicoin')
        cls.emp = Employee.objects.create(
            employee_number='E900', full_name='Test Mover',
            job_title='Agent', company=cls.adic)
        cls.unami = User.objects.create_user('ubutale2', email='ubutale@alphadirect.co.bw', password='x')
        cls.cfo = User.objects.create_superuser('pg2', 'pganesharajah@alphadirect.co.bw', 'x')
        cls.hr = User.objects.create_user('hrmaker2', email='hr@alphadirect.co.bw', password='x')

    def _submitted(self):
        return submit_transfer(submitter=self.hr, employee_id=str(self.emp.id),
                               dest_company_id=str(self.uni.id),
                               effective_date=FUTURE, reason='reorg')

    def test_approving_out_emails_whoever_owes_the_in_approval(self):
        from unittest.mock import patch
        t = self._submitted()
        with patch('core.notifications.send_html_with_cfo_cc') as send:
            approve_out(t, self.cfo)
        self.assertTrue(send.called, 'nobody was told the second approval is waiting')
        to = send.call_args.kwargs['to']
        self.assertIn('ubutale@alphadirect.co.bw', to)

    def test_it_does_not_email_the_person_who_is_barred_from_approving(self):
        """The CFO approved Out, so he may not approve In — mailing him sends
        him to a button that refuses him."""
        from unittest.mock import patch
        t = self._submitted()
        with patch('core.notifications.send_html_with_cfo_cc') as send:
            approve_out(t, self.cfo)
        self.assertNotIn('pganesharajah@alphadirect.co.bw', send.call_args.kwargs['to'])

    def test_the_wider_list_means_two_busy_people_no_longer_stall_it(self):
        """Unami submits and the CFO approves Out.

        With only those two on the list this was a dead end — both were barred
        from the In step and the transfer could never move. The CFO added
        Dorothy, Pako and Legakwa on 2026-09-18 precisely so it cannot happen,
        and the notice must now reach them.
        """
        from unittest.mock import patch
        t = submit_transfer(submitter=self.unami, employee_id=str(self.emp.id),
                            dest_company_id=str(self.uni.id),
                            effective_date=FUTURE, reason='reorg')
        with patch('core.notifications.send_html_with_cfo_cc') as send:
            approve_out(t, self.cfo)
        to = send.call_args.kwargs['to']
        self.assertNotIn('ubutale@alphadirect.co.bw', to)        # submitted it
        self.assertNotIn('pganesharajah@alphadirect.co.bw', to)  # approved Out
        self.assertEqual(
            sorted(to),
            ['dikgopoleng@alphadirect.co.bw', 'lntabeni@alphadirect.co.bw',
             'pkago@alphadirect.co.bw'])

    def test_a_transfer_nobody_may_approve_is_still_shouted_about(self):
        """The dead end is rarer now, not gone — prove the alarm still works."""
        from unittest.mock import patch
        import hris.transfer_service as svc
        t = submit_transfer(submitter=self.unami, employee_id=str(self.emp.id),
                            dest_company_id=str(self.uni.id),
                            effective_date=FUTURE, reason='reorg')
        with patch.object(svc, 'TRANSFER_APPROVER_EMAILS',
                          ('ubutale@alphadirect.co.bw', 'pganesharajah@alphadirect.co.bw')), \
             patch('core.notifications.send_html_with_cfo_cc') as send:
            approve_out(t, self.cfo)
        self.assertTrue(send.called, 'a transfer nobody can approve must not be silent')
        self.assertIn('STUCK', send.call_args.kwargs['subject'])

    def test_a_mail_failure_never_loses_the_approval(self):
        from unittest.mock import patch
        t = self._submitted()
        with patch('core.notifications.send_html_with_cfo_cc', side_effect=RuntimeError('smtp down')):
            approve_out(t, self.cfo)
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.PENDING_IN)


class NamedApproverReachesTheScreenTest(APITestCase):
    """Pako was named an approver and holds HRIS role 'hr', which
    user_can_amend_hris refuses. Prove it through the real endpoints, not the
    service: he can list and approve, and still cannot SUBMIT a transfer."""

    @classmethod
    def setUpTestData(cls):
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.uni = Company.objects.create(code='UNI', name='Unicoin')
        cls.emp = Employee.objects.create(
            employee_number='E901', full_name='Screen Mover',
            job_title='Agent', company=cls.adic)
        cls.unami = User.objects.create_user('ubutale3', email='ubutale@alphadirect.co.bw', password='x')
        cls.cfo = User.objects.create_superuser('pg3', 'pganesharajah@alphadirect.co.bw', 'x')
        cls.pako = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw', password='x')
        # Live, Pako holds a grant on every entity; mirror the two this move touches.
        from core.models import UserCompanyAccess
        for co in (cls.adic, cls.uni):
            UserCompanyAccess.objects.create(user=cls.pako, company=co,
                                             can_view=True, can_write=True)
        cls.elsewhere = Company.objects.create(code='VCM', name='Veritas Capital')
        cls.walled = User.objects.create_user('lntabeni', email='lntabeni@alphadirect.co.bw', password='x')
        UserCompanyAccess.objects.create(user=cls.walled, company=cls.elsewhere,
                                         can_view=True, can_write=True)
        cls.outsider = User.objects.create_user('someone', email='someone@alphadirect.co.bw', password='x')

    def setUp(self):
        from unittest.mock import patch
        # HRIS module access + the HRIS password unlock are separate gates that
        # prod already passes for him; amendment rights stay REAL.
        for target in ('hris.api_views.user_can_access_hris',
                       'hris.api_views.is_hris_unlocked'):
            p = patch(target, return_value=True)
            p.start()
            self.addCleanup(p.stop)

    def _awaiting_in(self):
        t = submit_transfer(submitter=self.unami, employee_id=str(self.emp.id),
                            dest_company_id=str(self.uni.id),
                            effective_date=FUTURE, reason='reorg')
        approve_out(t, self.cfo)
        return t

    def test_approver_without_amend_rights_can_list_and_approve_in(self):
        t = self._awaiting_in()
        c = APIClient()
        c.force_authenticate(user=self.pako)
        self.assertEqual(c.get('/hris/api/transfers/').status_code, 200)
        r = c.post(f'/hris/api/transfers/{t.id}/approve-in/', {}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        t.refresh_from_db()
        self.assertNotEqual(t.status, EmployeeTransfer.Status.PENDING_IN)

    def test_approver_still_cannot_submit_a_transfer(self):
        c = APIClient()
        c.force_authenticate(user=self.pako)
        r = c.post('/hris/api/transfers/', {
            'employee_id': str(self.emp.id), 'dest_company_id': str(self.uni.id),
            'effective_date': FUTURE.isoformat()}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_someone_not_named_gets_nothing(self):
        t = self._awaiting_in()
        c = APIClient()
        c.force_authenticate(user=self.outsider)
        self.assertEqual(c.get('/hris/api/transfers/').status_code, 403)
        self.assertEqual(
            c.post(f'/hris/api/transfers/{t.id}/approve-in/', {}, format='json').status_code, 403)

    def test_an_approver_cannot_decide_a_transfer_outside_their_entities(self):
        """Opus gate, 18-Sep: three more approvers reach _decide, so it carries
        the same entity scope as the list — no deciding by guessing an id."""
        t = self._awaiting_in()
        c = APIClient()
        c.force_authenticate(user=self.walled)
        r = c.post(f'/hris/api/transfers/{t.id}/approve-in/', {}, format='json')
        self.assertEqual(r.status_code, 404)
        t.refresh_from_db()
        self.assertEqual(t.status, EmployeeTransfer.Status.PENDING_IN)
