"""Workstream A — the Welcome page "My Requests" block (CFO board items A1-A7,
loaded 14-Sep-2026, built 15-Sep).

What these pin, and why each one exists:

* A3/A5 — refunds were the ONE workflow core/request_tracker.py never read, so a
  staff member's out-of-pocket claim was invisible on the tracker while leave,
  loans, petty cash and payments all showed. The adapter closes that.
* A4 — the security point of the item. The company-wide petty-cash register is
  scoped to the COMPANY, not the person; rendering it on a personal card would
  show one employee another employee's voucher. `/petty-cash-vouchers/mine/`
  must be created_by-scoped, and the test proves A cannot see B.
* A6 — "Needs your action" must never produce a fake all-clear. When a source
  cannot be read it is NAMED in `unavailable`, not silently dropped.
* A7 — the six standard phrases, derived from the authoritative bucket/holder
  fields and never from client-side guessing.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import Company, UserProfile
from core.request_tracker import (
    COMPLETED, REJECTED, WAITING_CFO, WAITING_FINANCE, WAITING_HR, WAITING_ME,
    WAITING_MANAGER, _standard_holder, my_pending_actions, my_requests,
)
from hris.expense_claim_models import ExpenseClaim


class WorkstreamABase(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='WLCM', name='Welcome Test Co.')
        cls.alice = User.objects.create_user('alice', 'alice@test.example', 'x')
        cls.bob = User.objects.create_user('bob', 'bob@test.example', 'x')
        cls.alice_profile = UserProfile.objects.create(user=cls.alice, is_active=True)
        cls.bob_profile = UserProfile.objects.create(user=cls.bob, is_active=True)

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _claim(self, profile, status=ExpenseClaim.Status.SUBMITTED,
               amount='250.00', reject_reason='', **kw):
        return ExpenseClaim.objects.create(
            profile=profile, expense_date=dt.date(2026, 9, 1),
            amount=Decimal(amount), description='Fuel', category='Travel',
            status=status, reject_reason=reject_reason, **kw)


class ExpenseAppearsOnTheTracker(WorkstreamABase):
    """A3 + A5."""

    def test_a_refund_now_shows_on_my_requests(self):
        self._claim(self.alice_profile)
        kinds = {r['kind'] for r in my_requests(self.alice)}
        self.assertIn('expense', kinds,
                      'A refund must appear on the tracker — A3 exists because it did not.')

    def test_a_refund_is_requester_scoped(self):
        self._claim(self.bob_profile, amount='999.00')
        rows = [r for r in my_requests(self.alice) if r['kind'] == 'expense']
        self.assertEqual(rows, [], "Alice must never see Bob's refund.")

    def test_a_returned_refund_says_what_to_correct(self):
        self._claim(self.alice_profile, status=ExpenseClaim.Status.REJECTED,
                    reject_reason='The receipt is not readable.')
        row = next(r for r in my_requests(self.alice) if r['kind'] == 'expense')
        self.assertEqual(row['bucket'], 'rejected')
        self.assertIn('receipt', row['next_label'].lower(),
                      'A rejected claim must explain what must be corrected (A3).')

    def test_a_paid_refund_shows_its_payment_reference(self):
        self._claim(self.alice_profile, status=ExpenseClaim.Status.PAID,
                    paid_reference='FNB-REF-4471')
        row = next(r for r in my_requests(self.alice) if r['kind'] == 'expense')
        self.assertIn('FNB-REF-4471', row['title'],
                      'A paid claim must show its evidence reference (A5).')


class StandardApprovalWording(WorkstreamABase):
    """A7 — exactly the six phrases the CFO asked for."""

    def test_each_desk_maps_to_its_standard_phrase(self):
        self.assertEqual(_standard_holder('pending', 'your approver'), WAITING_MANAGER)
        self.assertEqual(_standard_holder('pending', 'the CFO'), WAITING_CFO)
        self.assertEqual(_standard_holder('pending', 'a finance signatory'), WAITING_FINANCE)
        self.assertEqual(_standard_holder('pending', 'a second finance signatory'), WAITING_FINANCE)
        self.assertEqual(_standard_holder('approved', 'you (to sign)'), WAITING_ME)
        # _leave_pay's own desk names. These were missing on the first pass, so
        # every in-flight leave-encashment row showed no standard phrase at all.
        self.assertEqual(_standard_holder('pending', 'Finance (FC/FM)'), WAITING_FINANCE)
        self.assertEqual(_standard_holder('approved', 'Finance (for payment)'), WAITING_FINANCE)

    def test_hr_is_its_own_desk_and_never_reads_as_finance(self):
        # CFO decision 15-Sep-2026: the list is SEVEN, not six. Leave encashment
        # genuinely sits with HR; folding it into Finance would tell a staff
        # member Finance holds their leave pay when HR does, and send them to
        # the wrong person.
        self.assertEqual(_standard_holder('pending', 'HR'), WAITING_HR)
        self.assertEqual(_standard_holder('approved', 'HR (to disburse)'), WAITING_HR)
        self.assertNotEqual(_standard_holder('pending', 'HR'), WAITING_FINANCE)

    def test_approved_leave_reads_as_completed(self):
        # CFO decision 15-Sep-2026. Leave that is granted but not yet taken is a
        # FINISHED request — nobody is waiting on anything — so it must not sit
        # "in progress" for ever.
        #
        # The first version of this test asserted a STRING was present in the
        # source of _leave. That proved nothing: a harmless refactor would fail
        # it, and a genuinely wrong phrase for leave would pass it. This drives
        # the real path instead and asserts on what a staff member would see.
        import datetime as _dt
        from hris.models import LeaveRequest, LeaveType
        from hris.models import HRISProfile
        from payroll.models import Employee

        user = User.objects.create_user('leavetester', 'leave@example.com', 'x')
        UserProfile.objects.create(user=user, is_active=True)
        # _profile_for() resolves through payroll.Employee.user -> employee_record.
        # Set it explicitly; hoping the link exists is how this test silently
        # skipped and proved nothing.
        emp = Employee.objects.create(
            employee_number='LV001', full_name='Leave Tester',
            email='leave@example.com', status='active', user=user)
        HRISProfile.objects.create(employee=emp)
        lt, _ = LeaveType.objects.get_or_create(
            code='ANN', defaults={'name': 'Annual Leave'})

        from hris.feature_views import _profile_for
        profile = _profile_for(user)
        self.assertIsNotNone(
            profile, 'The fixture must resolve a profile — a skip here proves nothing.')

        LeaveRequest.objects.create(
            profile=profile, leave_type=lt, status='approved',
            start_date=_dt.date(2026, 10, 1), end_date=_dt.date(2026, 10, 2),
            days=2)

        row = next(r for r in my_requests(user) if r['kind'] == 'leave')
        self.assertEqual(row['bucket'], 'paid',
                         'Approved leave must land in a terminal bucket.')
        self.assertEqual(row['holder_standard'], COMPLETED,
                         'A staff member must see "Completed", not a raw status.')

    def test_a_finished_or_rejected_request_never_shows_a_stale_desk(self):
        self.assertEqual(_standard_holder('paid', 'the CFO'), COMPLETED)
        self.assertEqual(_standard_holder('rejected', 'the CFO'), REJECTED)
        self.assertEqual(_standard_holder('cancelled', 'Finance'), COMPLETED)

    def test_an_unknown_desk_falls_through_rather_than_guessing(self):
        # Allow-list, never a range: an unrecognised desk must NOT be guessed
        # into someone's queue. It returns '' and the row shows the adapter's
        # own precise words instead.
        self.assertEqual(_standard_holder('pending', 'the Minister of Finance'), '')

    def test_the_tracker_stamps_the_standard_phrase_on_every_row(self):
        self._claim(self.alice_profile, status=ExpenseClaim.Status.PENDING_CFO)
        row = next(r for r in my_requests(self.alice) if r['kind'] == 'expense')
        self.assertEqual(row['holder_standard'], WAITING_CFO)


class NeedsYourAction(WorkstreamABase):
    """A6."""

    def test_a_returned_refund_becomes_an_action_with_the_reason(self):
        self._claim(self.alice_profile, status=ExpenseClaim.Status.REJECTED,
                    reject_reason='Receipt unreadable.')
        actions = my_pending_actions(self.alice)['actions']
        self.assertTrue(any('Receipt unreadable.' in a['instruction'] for a in actions))

    def test_a_refund_on_someone_elses_desk_is_not_my_action(self):
        # Progress is not an action. Listing it trains people to ignore the list.
        self._claim(self.alice_profile, status=ExpenseClaim.Status.PENDING_CFO)
        actions = my_pending_actions(self.alice)['actions']
        self.assertEqual([a for a in actions if a['kind'] == 'expense'], [])

    def test_another_employees_problem_is_never_my_action(self):
        self._claim(self.bob_profile, status=ExpenseClaim.Status.REJECTED,
                    reject_reason='Bobs receipt.')
        actions = my_pending_actions(self.alice)['actions']
        self.assertFalse(any('Bobs receipt.' in a['instruction'] for a in actions))

    def test_every_source_reads_cleanly_on_the_happy_path(self):
        # THE GATE. Without this, any FieldError or ImportError inside an A6
        # source ships as a permanent "could not check X" and nothing goes red
        # — which is exactly what happened on the first pass of this feature:
        # LeaveReversal.profile is an HRISProfile (no `user` field), so
        # profile__user=... raised on every call and the wrapper hid it.
        self.assertEqual(my_pending_actions(self.alice)['unavailable'], [],
                         'A source is failing and the wrapper is hiding it.')

    def test_an_unreadable_source_is_named_never_silently_dropped(self):
        # The CFO called this out by name: "Missing data produces an honest
        # empty state, not a fake all-clear."
        from unittest import mock
        with mock.patch('hris.expense_claim_models.ExpenseClaim.objects.filter',
                        side_effect=RuntimeError('boom')):
            out = my_pending_actions(self.alice)
        self.assertIn('refunds', out['unavailable'],
                      'A source that failed must be NAMED, not reported as all-clear.')

    def test_an_anonymous_caller_gets_nothing(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertEqual(my_pending_actions(AnonymousUser())['actions'], [])


class PettyCashIsRequesterOnly(WorkstreamABase):
    """A4 — the acceptance test the board item asks for by name."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Give BOTH of them the SAME company access on purpose. If only Alice
        # had it, the test would pass on the company filter and prove nothing
        # about requester isolation — which is the whole point of A4.
        from core.models import UserCompanyAccess
        for u in (cls.alice, cls.bob):
            UserCompanyAccess.objects.get_or_create(
                user=u, company=cls.company, defaults={'can_view': True})

    def _voucher(self, user):
        from ledger.models import Account
        from petty_cash.models import PettyCashLocation, PettyCashVoucher
        float_acc, _ = Account.objects.get_or_create(
            code='1160', defaults={'name': 'Petty cash float',
                                   'account_type': Account.AccountType.ASSET,
                                   'sub_type': Account.SubType.CURRENT_ASSET})
        bank_acc, _ = Account.objects.get_or_create(
            code='1110', defaults={'name': 'Bank',
                                   'account_type': Account.AccountType.ASSET,
                                   'sub_type': Account.SubType.BANK})
        loc, _ = PettyCashLocation.objects.get_or_create(
            name='Head Office',
            defaults={'company': self.company,
                      'petty_cash_account': float_acc,
                      'reimbursing_bank_account': bank_acc})
        return PettyCashVoucher.objects.create(
            location=loc, created_by=user, amount=Decimal('100.00'),
            description=f'{user.username} voucher')

    def test_employee_a_cannot_see_employee_b_voucher(self):
        mine = self._voucher(self.alice)
        theirs = self._voucher(self.bob)
        r = self._client(self.alice).get('/api/v1/petty-cash-vouchers/mine/')
        self.assertEqual(r.status_code, 200, r.content)
        ids = {str(row['id']) for row in r.json()}
        self.assertIn(str(mine.id), ids)
        self.assertNotIn(str(theirs.id), ids,
                         "The requester-only endpoint leaked another employee's voucher.")

    def test_it_requires_a_login(self):
        self.assertIn(APIClient().get('/api/v1/petty-cash-vouchers/mine/').status_code,
                      (401, 403))


class WelcomeSummaryEndpoint(WorkstreamABase):
    """The block the Welcome page actually calls."""

    URL = '/api/v1/my-requests/summary/'

    def test_it_needs_a_login(self):
        self.assertIn(APIClient().get(self.URL).status_code, (401, 403))

    def test_it_returns_the_block_the_welcome_page_needs(self):
        self._claim(self.alice_profile)
        r = self._client(self.alice).get(self.URL)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        for key in ('active', 'by_kind', 'recent', 'actions',
                    'actions_unavailable', 'leave_balances', 'leave_error'):
            self.assertIn(key, body)
        self.assertEqual(body['by_kind'].get('expense'), 1)

    def test_it_never_shows_another_employees_request(self):
        self._claim(self.bob_profile, amount='777.00')
        body = self._client(self.alice).get(self.URL).json()
        self.assertEqual(body['recent'], [])
        self.assertEqual(body['active'], 0)
