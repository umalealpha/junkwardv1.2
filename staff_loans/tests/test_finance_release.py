"""Staff loans move from HR disbursement to Finance release (CFO, 15-Sep-2026).

His words: "the staff loan can come. I approve or Mr Arun approves, then it goes
to finance. The finance manager or financial controller, Pako, can approve it and
the payment is automatically loaded. Then a notification is sent to Human
Resources for their records."

What these pin:
  * Finance Manager / Financial Controller may release; HR may NOT any more.
  * Segregation of duties is UNCHANGED — you cannot release your own loan, and
    the person who approved it cannot also release it.
  * "Automatically loaded" means loaded into OMNI'S PAYMENT QUEUE at
    PENDING_CFO, never paid. Omni moves no money; the CFO still authorises in
    FNB with his own phone.
  * A payment request that fails to raise must NOT silently leave a disbursed
    loan with nobody paying it.
  * HR is notified, and the notification carries no bank details.
"""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, Currency, UserProfile
from ledger.models import Account, FiscalPeriod
from payroll.contract_models import EmploymentContract
from payroll.models import Employee
from staff_loans import services as svc
from staff_loans.models import StaffLoanApplication
from taskboard.models import PaymentRequest

# The real rule is a 50-word minimum motivation (staff_loans.services line 354),
# not a token sentence. A fixture that trips the validator tests nothing.
MOTIVATION = (
    'I am applying for this staff loan to cover the school fees for my two '
    'children for the coming term, together with the cost of their uniforms, '
    'textbooks and transport to school each day. The fees fall due before my '
    'next salary payment and I do not have the full amount saved. I will repay '
    'the loan from my monthly salary over the agreed period and I understand '
    'the deduction will be made automatically by payroll each month until it '
    'is settled in full.')


class FinanceReleasesTheLoanTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct')

        # An OPEN fiscal period — the issuance journal entry is dated today and
        # will not post without one.
        t = date.today()
        FiscalPeriod.objects.create(
            period_name=f'FR-{t:%Y-%m}', start_date=date(t.year, 1, 1),
            end_date=date(t.year, 12, 31), status=FiscalPeriod.Status.OPEN,
            company=cls.company)

        # The same ADIC-style accounts the resolver looks for by NAME. Inventing
        # a plausible-looking chart here is what made the first run fail with
        # "choose a valid bank or cash account".
        Account.objects.create(code='121010', name='Staff Loan',
                               account_type='asset', sub_type='current_asset', is_active=True)
        Account.objects.create(code='124004', name='Interest From Staff Loan',
                               account_type='revenue', sub_type='other_revenue', is_active=True)
        Account.objects.create(code='1110', name='FNB BWP operating account',
                               account_type='asset', sub_type='bank',
                               is_bank_account=True, is_active=True)

        cls.emp_user = User.objects.create_user('alice', email='alice@ad.co.bw', password='x')
        cls.emp = Employee.objects.create(
            employee_number='E100', full_name='Alice Molefe', email='alice@ad.co.bw',
            status='active', company=cls.company, user=cls.emp_user,
            bank_name='FNB', bank_account_no='62812345678')
        EmploymentContract.objects.create(
            employee=cls.emp, start_date=date(2025, 1, 1), end_date=None,
            basic=Decimal('8000.00'), frequency=EmploymentContract.Frequency.MONTHLY,
            status=EmploymentContract.Status.ACTIVE)

        cls.cfo_user = User.objects.create_user('pganesharajah', email='cfo@ad.co.bw', password='x')
        UserProfile.objects.create(user=cls.cfo_user, title=UserProfile.Title.CFO, is_active=True)
        cls.hr_user = User.objects.create_user('hr', email='hr@ad.co.bw', password='x')
        UserProfile.objects.create(user=cls.hr_user, title=UserProfile.Title.HR_MANAGER, is_active=True)
        cls.fin_user = User.objects.create_user('finmgr', email='ktshutlhedi@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.fin_user,
                                   title=UserProfile.Title.FINANCE_MANAGER, is_active=True)
        cls.controller = User.objects.create_user('controller', email='pkago@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.controller,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER, is_active=True)

    def _signed_loan(self):
        app = svc.create_application(
            employee=self.emp, loan_type=StaffLoanApplication.LoanType.STAFF,
            amount_requested=Decimal('5000'), term_months_requested=4,
            reason=MOTIVATION, user=self.emp_user, no_other_loans=True)
        svc.submit_application(app, self.emp_user)
        svc.cfo_decide(app, self.cfo_user, approve=True)
        svc.sign_application(app, self.emp_user,
                             signature_data_url='data:image/png;base64,AAAA',
                             signatory_full_name='Alice Molefe')
        return app

    # ── who may release ─────────────────────────────────────────────────────
    def test_the_finance_manager_may_release(self):
        app = svc.disburse(self._signed_loan(), self.fin_user, disbursement_bank_code='1110')
        self.assertEqual(app.status, StaffLoanApplication.Status.ACTIVE)

    def test_the_financial_controller_may_release(self):
        app = svc.disburse(self._signed_loan(), self.controller, disbursement_bank_code='1110')
        self.assertEqual(app.status, StaffLoanApplication.Status.ACTIVE)

    def test_a_SUPERUSER_test_account_cannot_release(self):
        """THE ONE THAT ALMOST SHIPPED BROKEN.

        The CFO told me to remove the shared "Omni QA" test login from loan
        release. My first version kept an is_superuser shortcut ahead of the
        named-list check — and that account IS a superuser on production, so it
        would have carried on releasing loans while the change reported success.
        No test could see that, because it is a fact about prod data, not code.

        Its username is also omni@alphadirect.co.bw while its email is
        pganesharajah+omniqa@alphadirect.co.bw, so an exclusion written against
        the username would have missed it too.
        """
        bot = User.objects.create_superuser(
            'omni@alphadirect.co.bw', 'pganesharajah+omniqa@alphadirect.co.bw', 'x')
        UserProfile.objects.create(user=bot,
                                   title=UserProfile.Title.FINANCE_MANAGER, is_active=True)
        self.assertFalse(svc._is_finance_approver(bot),
                         'A superuser test account must NOT be able to release a staff loan.')
        with self.assertRaises(ValidationError):
            svc.disburse(self._signed_loan(), bot, disbursement_bank_code='1110')

    def test_a_deactivated_profile_loses_release_even_if_still_on_the_list(self):
        # Offboarding that disables the profile but leaves the Django login
        # enabled must not keep release rights.
        UserProfile.objects.filter(user=self.fin_user).update(is_active=False)
        # user.profile is a CACHED relation and .update() goes round it, so the
        # in-memory object still holds the old value. Re-read the user the way a
        # real request does — a stale cache here would have made this test lie.
        from django.contrib.auth.models import User as _U
        fresh = _U.objects.get(pk=self.fin_user.pk)
        self.assertFalse(svc._is_finance_approver(fresh))
        self.assertNotIn(fresh, svc.finance_approvers())

    def test_a_finance_TITLE_alone_is_not_enough(self):
        """CFO 16-Sep-2026, after seeing what role-based let through.

        Oprah Mogomotsi's Omni title reads finance_manager but she is INTERNAL
        AUDIT — the function that checks these very controls — and "Omni QA" is
        a shared test login. Both could release a staff loan. Holding the title
        must no longer be enough on its own.
        """
        imposter = User.objects.create_user(
            'audit_person', email='someone.else@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=imposter,
                                   title=UserProfile.Title.FINANCE_MANAGER, is_active=True)
        self.assertFalse(svc._is_finance_approver(imposter),
                         'A finance TITLE alone must not grant loan release.')
        with self.assertRaises(ValidationError):
            svc.disburse(self._signed_loan(), imposter, disbursement_bank_code='1110')

    def test_the_notice_goes_only_to_people_who_can_actually_act(self):
        # finance_approvers() drives the "ready to release" email. If it drifts
        # from the permission check, we email someone who then cannot do it.
        allowed = {u.email.lower() for u in svc.finance_approvers()}
        for u in svc.finance_approvers():
            self.assertTrue(svc._is_finance_approver(u),
                            f'{u.email} is notified but cannot release.')
        self.assertNotIn('someone.else@alphadirect.co.bw', allowed)

    def test_hr_may_NO_LONGER_release(self):
        # THE CHANGE. HR used to be the only role that could do this.
        with self.assertRaises(ValidationError) as cm:
            svc.disburse(self._signed_loan(), self.hr_user, disbursement_bank_code='1110')
        self.assertIn('Finance', str(cm.exception))

    def test_segregation_of_duties_still_holds(self):
        # Finance person who is ALSO the applicant cannot release their own loan.
        UserProfile.objects.filter(user=self.emp_user).delete()
        UserProfile.objects.create(user=self.emp_user,
                                   title=UserProfile.Title.FINANCE_MANAGER, is_active=True)
        with self.assertRaises(ValidationError):
            svc.disburse(self._signed_loan(), self.emp_user, disbursement_bank_code='1110')

    # ── "automatically loaded" means into Omni's queue, never paid ───────────
    def test_releasing_loads_the_payment_into_omnis_queue(self):
        app = svc.disburse(self._signed_loan(), self.fin_user, disbursement_bank_code='1110')
        # disburse() records a payment failure rather than rolling back a posted
        # journal entry, so assert the reason FIRST — otherwise this test reports
        # a bare DoesNotExist and hides why.
        app.refresh_from_db()           # the DB row, not the object in memory
        self.assertEqual(app.payment_request_error, '')
        self.assertTrue(app.payment_request_ref)
        pr = PaymentRequest.objects.get(ref=app.payment_request_ref)
        self.assertEqual(pr.category, PaymentRequest.Category.STAFF_LOAN)
        self.assertEqual(pr.total, app.effective_amount)
        self.assertEqual(pr.payee, 'Alice Molefe')

    def test_the_payment_is_NOT_paid_it_awaits_cfo_authorisation(self):
        # Omni never moves money. If this ever lands in a paid state, money has
        # been released without the CFO's phone.
        app = svc.disburse(self._signed_loan(), self.fin_user, disbursement_bank_code='1110')
        pr = PaymentRequest.objects.get(ref=app.payment_request_ref)
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)
        self.assertNotEqual(pr.status, PaymentRequest.Status.PAID)

    def test_the_payment_carries_the_employees_own_bank_account(self):
        app = svc.disburse(self._signed_loan(), self.fin_user, disbursement_bank_code='1110')
        pr = PaymentRequest.objects.get(ref=app.payment_request_ref)
        self.assertEqual(pr.account_number, '62812345678')
        self.assertEqual(pr.bank_name, 'FNB')

    def test_a_failed_payment_request_is_recorded_not_swallowed(self):
        # The journal entry and the payroll deduction are already posted by the
        # time the payment is raised, so a failure must NOT roll the loan back —
        # but it must be visible, or a loan sits disbursed with nobody paying it.
        with mock.patch('staff_loans.payment_request.raise_staff_loan_payment_request',
                        side_effect=RuntimeError('no CFO configured')):
            app = svc.disburse(self._signed_loan(), self.fin_user, disbursement_bank_code='1110')
        app.refresh_from_db()           # the DB row, not the object in memory
        self.assertEqual(app.status, StaffLoanApplication.Status.ACTIVE)
        self.assertEqual(app.payment_request_ref, '')
        self.assertIn('no CFO configured', app.payment_request_error)

    # ── HR is told, and told only what it needs ─────────────────────────────
    def test_hr_is_notified(self):
        mail.outbox = []
        # The notice is sent on transaction.on_commit now — inside the atomic
        # block it would go out BEFORE the commit, so a rollback would leave HR
        # holding a record of a loan that never happened.
        with self.captureOnCommitCallbacks(execute=True):
            svc.disburse(self._signed_loan(), self.fin_user, disbursement_bank_code='1110')
        sent = [m for m in mail.outbox if 'Staff loan released' in m.subject]
        self.assertTrue(sent, 'HR must be told a loan was released — it is their record.')
        self.assertIn('hr@ad.co.bw', sent[0].to)

    def test_the_hr_notice_carries_no_bank_details(self):
        # HR keeps a record; it does not need the employee's account number, and
        # every extra copy of it is another place it can leak.
        mail.outbox = []
        with self.captureOnCommitCallbacks(execute=True):
            svc.disburse(self._signed_loan(), self.fin_user, disbursement_bank_code='1110')
        sent = [m for m in mail.outbox if 'Staff loan released' in m.subject]
        body = (sent[0].body or '') + ''.join(str(a) for a in sent[0].alternatives or [])
        self.assertNotIn('62812345678', body)

    def test_a_failed_hr_notification_never_undoes_the_loan(self):
        with mock.patch('staff_loans.services.notify_hr_of_disbursement',
                        side_effect=RuntimeError('mail down')):
            with self.captureOnCommitCallbacks(execute=True):
                app = svc.disburse(self._signed_loan(), self.fin_user,
                                   disbursement_bank_code='1110')
        self.assertEqual(app.status, StaffLoanApplication.Status.ACTIVE)
        self.assertIsNotNone(app.employee_loan)


class FinanceCanActuallyUseItThroughTheAPI(FinanceReleasesTheLoanTest):
    """The service layer was right and the API still gated on HR, so a Finance
    Manager's disburse POST 404'd on a queryset filtered to their own loans —
    and 46 green tests missed it because every one called svc.disburse()
    directly. Never again: this goes through the endpoint a person actually hits.
    """

    def _api(self, user):
        from rest_framework.test import APIClient
        c = APIClient()
        c.force_authenticate(user)
        return c

    def test_finance_can_see_and_release_a_loan_through_the_api(self):
        app = self._signed_loan()
        r = self._api(self.fin_user).post(
            f'/api/v1/staff-loans/{app.pk}/disburse/',
            {'disbursement_bank_code': '1110'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        app.refresh_from_db()
        self.assertEqual(app.status, StaffLoanApplication.Status.ACTIVE)

    def test_the_release_button_is_offered_to_finance_not_hr(self):
        app = self._signed_loan()
        fin = self._api(self.fin_user).get(f'/api/v1/staff-loans/{app.pk}/').json()
        self.assertTrue(fin['can_disburse'],
                        'Finance must be offered the button it is now the only role able to use.')
        hr = self._api(self.hr_user).get(f'/api/v1/staff-loans/{app.pk}/').json()
        self.assertFalse(hr['can_disburse'],
                         'HR must NOT be shown a button that now refuses them.')
