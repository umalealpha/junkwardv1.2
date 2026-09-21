"""taskboard/test_bank_cross_check.py — the bank cross-check as a HARD gate.

Finance spec 2026-09-08: "The 'Beneficiary Bank Account' panel … already flags
first-time payees with no previous account on record. Right now that flag is
informational only. Change it to a hard gate: when it's showing, the 'Verified
by' field … becomes required before the request can proceed, and the bank
details entered there must be confirmed against the beneficiary details on the
attached supporting document by Finance — not by the original preparer. If the
two don't match, block submission … For payees that already have a verified
account on record, any change to Bank/Branch code/Account number on this form
should re-trigger the same first-time-payee verification flow rather than
silently overwriting the stored details."

These are the rules that must never regress:
  - a change to the BANK, the BRANCH CODE or the ACCOUNT NUMBER of a payee whose
    account Omni already holds re-triggers the verification flow. Not just the
    account number — the account number alone was the old test;
  - a respelling is not a change: 'FNB Botswana' and 'fnb  botswana' are one
    bank, on the same normaliser the payee names use;
  - sign-off does not pass until (a) somebody is NAMED as verifier, (b) there is
    a supporting document to check against, and (c) Finance positively confirms
    the details match it. All three, and each is refused separately so the
    approver is told which one is missing;
  - the confirmation comes from FINANCE, NOT THE PREPARER: the finance approver
    can never sign off a request they raised, and a preparer naming THEMSELVES
    as verifier is refused outright;
  - both confirmations are written on the record. A tick that leaves no trace is
    not a control;
  - entering the payment is never blocked — the hold is at sign-off (CFO
    2026-09-02: "we are not blocking people from entering the payments").

SCOPE, stated plainly: the sign-off gate fires on CHANGED details, not on every
first-ever payee. A brand-new payee is already stopped at creation by
PAY-BANK-03. Widening it to first-ever payees rewrites the sign-off path for
every new supplier, which is the CFO's call — see the handover.

No real payee or staff names — fake suppliers only.

Run: manage.py test taskboard.test_bank_cross_check
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from taskboard.models import PaymentRequest, PaymentRequestAttachment
from taskboard.payee_bank_history import bank_details_changed
from taskboard.test_helpers import seed_adic

OLD_ACCT = '62011110000'
NEW_ACCT = '62099998888'


class BankDetailsChangedTests(TestCase):
    """The deterministic helper: which of the three fields moved."""

    def setUp(self):
        seed_adic()
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/01/0001', subject='First invoice',
            payee='Nonesuch Motors', account_name='Nonesuch Motors',
            account_number=OLD_ACCT, bank_name='FNB Botswana',
            branch_code='281267', total=Decimal('1000.00'),
            status=PaymentRequest.Status.PAID, line_items=[])

    def test_no_history_means_nothing_to_compare(self):
        """A payee we have never paid is first_payment_warning's business."""
        self.assertIsNone(bank_details_changed('Nobody Ever Paid This Name',
                                               account_number=NEW_ACCT))

    def test_the_same_details_are_not_a_change(self):
        self.assertIsNone(bank_details_changed(
            'Nonesuch Motors', bank_name='FNB Botswana', branch_code='281267',
            account_number=OLD_ACCT))

    def test_a_changed_account_number_is_a_change(self):
        w = bank_details_changed('Nonesuch Motors', bank_name='FNB Botswana',
                                 branch_code='281267', account_number=NEW_ACCT)
        self.assertEqual(w['fields'], ['account number'])
        self.assertEqual(w['control'], 'PAY-BANK-04')

    def test_a_changed_bank_is_a_change(self):
        """The old control compared the account number ONLY, so moving a payee
        to another bank passed unchallenged."""
        w = bank_details_changed('Nonesuch Motors', bank_name='Absa Botswana',
                                 branch_code='281267', account_number=OLD_ACCT)
        self.assertEqual(w['fields'], ['bank'])

    def test_a_changed_branch_code_is_a_change(self):
        w = bank_details_changed('Nonesuch Motors', bank_name='FNB Botswana',
                                 branch_code='999999', account_number=OLD_ACCT)
        self.assertEqual(w['fields'], ['branch code'])

    def test_two_changed_fields_are_both_named(self):
        w = bank_details_changed('Nonesuch Motors', bank_name='Absa Botswana',
                                 branch_code='999999', account_number=OLD_ACCT)
        self.assertEqual(w['fields'], ['bank', 'branch code'])
        self.assertIn('bank and the branch code', w['detail'])

    def test_a_respelling_is_not_a_change(self):
        """Otherwise every typist's spacing would read as a bank move."""
        self.assertIsNone(bank_details_changed(
            'Nonesuch Motors', bank_name='fnb  botswana', branch_code='281267',
            account_number=OLD_ACCT))

    def test_a_blank_field_is_not_read_as_a_change(self):
        """A blank branch on an FNB payee is a legitimate omission — FNB falls
        back to the FNB-to-FNB branch — not a change to something else."""
        self.assertIsNone(bank_details_changed(
            'Nonesuch Motors', bank_name='', branch_code='',
            account_number=OLD_ACCT))

    def test_spaces_and_dashes_in_the_account_are_ignored(self):
        self.assertIsNone(bank_details_changed(
            'Nonesuch Motors', account_number='6201 1110-000'))

    def test_the_message_says_where_the_stored_details_came_from(self):
        w = bank_details_changed('Nonesuch Motors', bank_name='Absa Botswana')
        self.assertIn('PAY/ADIC/2026/08/01/0001', w['detail'])
        self.assertIn('supporting document', w['detail'])


class VerifierMayNotBeThePreparerTests(TestCase):
    """"by Finance — not by the original preparer.\""""

    def setUp(self):
        self.me = User.objects.create_user('lthebe', password='x',
                                           first_name='Lorato', last_name='Thebe')
        User.objects.create_user('kago', email='ktshutlhedi@alphadirect.co.bw',
                                 password='x', first_name='Kago')
        self.client.force_login(self.me)
        self.url = reverse('v1-payment-requests')
        seed_adic()
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/01/0001', subject='First invoice',
            payee='Nonesuch Motors', account_name='Nonesuch Motors',
            account_number=OLD_ACCT, bank_name='FNB Botswana',
            total=Decimal('1000.00'), status=PaymentRequest.Status.PAID,
            line_items=[])

    def post(self, **over):
        body = {
            'subject': 'Nonesuch Motors — repair',
            'category': PaymentRequest.Category.OTHER,
            'line_items': [{'description': 'Repair', 'amount': '2500.00'}],
            'payee': 'Nonesuch Motors', 'account_name': 'Nonesuch Motors',
            'bank_name': 'Absa Botswana', 'branch_code': '999999',
            'account_number': OLD_ACCT,
            'bank_change_reason': 'Supplier moved bank; confirmed by phone.',
        }
        body.update(over)
        return self.client.post(self.url, body, content_type='application/json')

    def test_the_preparer_cannot_name_themselves_as_verifier(self):
        """No blocker (CFO 2026-09-09): naming yourself as verifier no longer
        refuses the pack — it goes to the committee for the independent check."""
        r = self.post(verifier='Lorato Thebe')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-04')
        self.assertTrue(PaymentRequest.objects.filter(
            subject='Nonesuch Motors — repair').exists())

    def test_the_check_ignores_spelling_and_punctuation(self):
        """'lorato  thebe' is the same person as 'Lorato Thebe' — still
        caught, and still routed to the committee rather than refused."""
        r = self.post(verifier='lorato  thebe')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-04')

    def test_naming_somebody_else_is_accepted(self):
        r = self.post(verifier='Kago Tshutlhedi')
        self.assertEqual(r.status_code, 201, r.content)

    def test_entering_the_payment_is_never_blocked_by_a_missing_verifier(self):
        """CFO 2026-09-02: "we are not blocking people from entering the
        payments". A missing verifier is held at sign-off, not here."""
        r = self.post()
        self.assertEqual(r.status_code, 201, r.content)

    def test_a_first_ever_payee_also_flags_a_self_named_verifier(self):
        """The flag showing is the trigger, and it shows for a first-ever payee
        too — a brand-new supplier account verified by the person who typed it
        is the exact case the spec rules out. Still never refused: it goes to
        the committee like every other judgement gate (CFO 2026-09-09)."""
        r = self.post(payee='Nonesuch Stationers', account_name='Nonesuch Stationers',
                      bank_name='FNB', branch_code='', account_number='62055551111',
                      verifier='Lorato Thebe', new_payee_confirmed=True)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(r.json()['exception']['control'], 'PAY-BANK-04')

    def test_a_payee_with_no_flag_showing_is_not_touched(self):
        """Paying a known payee into the SAME details is ordinary business —
        the cross-check must add no friction there at all."""
        r = self.post(bank_name='FNB Botswana', branch_code='',
                      account_number=OLD_ACCT, verifier='Lorato Thebe')
        self.assertEqual(r.status_code, 201, r.content)


class SignOffIsTheHardGateTests(TestCase):
    """Three separate refusals: no verifier, no document, no confirmation."""

    def setUp(self):
        seed_adic()
        self.clerk = User.objects.create_user('lthebe', password='x')
        self.approver = User.objects.create_user(
            'kago', email='ktshutlhedi@alphadirect.co.bw', password='x')
        User.objects.create_user('pganesharajah',
                                 email='pganesharajah@alphadirect.co.bw')
        # History: this payee was paid into OLD_ACCT at FNB.
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/01/0001', subject='First invoice',
            payee='Nonesuch Motors', account_name='Nonesuch Motors',
            account_number=OLD_ACCT, bank_name='FNB Botswana',
            total=Decimal('1000.00'), status=PaymentRequest.Status.PAID,
            line_items=[])
        # The request under test: same payee, DIFFERENT bank.
        self.pr = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/08/0002', subject='Second invoice',
            payee='Nonesuch Motors', account_name='Nonesuch Motors',
            account_number=OLD_ACCT, bank_name='Absa Botswana',
            total=Decimal('2500.00'), created_by=self.clerk,
            status=PaymentRequest.Status.PENDING_FINANCE, line_items=[])
        self.decide = reverse('v1-payment-request-decide', args=[self.pr.id])

    def _attach(self):
        return PaymentRequestAttachment.objects.create(
            request=self.pr, original_name='letterhead.pdf',
            file=SimpleUploadedFile('letterhead.pdf', b'%PDF-1.4 test'))

    def _approve(self, **body):
        self.client.force_login(self.approver)
        return self.client.post(self.decide, {'decision': 'approve', **body},
                                content_type='application/json')

    def test_no_verifier_named_holds_the_sign_off(self):
        r = self._approve()
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-BANK-04')
        self.assertTrue(r.json()['needs_verifier'])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PaymentRequest.Status.PENDING_FINANCE)

    def test_no_supporting_document_holds_the_sign_off(self):
        """A confirmation with nothing to check against is a tick over nothing."""
        self.pr.verifier = 'Kago Tshutlhedi'
        self.pr.save(update_fields=['verifier'])
        r = self._approve(bank_doc_ack=True)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-BANK-DOC')
        self.assertTrue(r.json()['needs_document'])

    def test_a_document_without_the_confirmation_holds_the_sign_off(self):
        self.pr.verifier = 'Kago Tshutlhedi'
        self.pr.save(update_fields=['verifier'])
        self._attach()
        r = self._approve()
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-BANK-DOC')
        self.assertFalse(r.json()['needs_document'])
        self.assertIn('reject the request', r.json()['detail'])

    def test_all_three_together_let_it_through(self):
        self.pr.verifier = 'Kago Tshutlhedi'
        self.pr.save(update_fields=['verifier'])
        self._attach()
        r = self._approve(bank_doc_ack=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PaymentRequest.Status.PENDING_CFO)

    def test_the_confirmation_is_written_on_the_record(self):
        self.pr.verifier = 'Kago Tshutlhedi'
        self.pr.save(update_fields=['verifier'])
        self._attach()
        self._approve(bank_doc_ack=True)
        self.pr.refresh_from_db()
        self.assertIn('PAY-BANK-DOC', self.pr.decision_notes)
        self.assertIn('bank', self.pr.decision_notes)

    def test_the_string_false_is_not_a_confirmation(self):
        """bool("false") is True in Python — a naive check disables the gate."""
        self.pr.verifier = 'Kago Tshutlhedi'
        self.pr.save(update_fields=['verifier'])
        self._attach()
        r = self._approve(bank_doc_ack='false')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['control'], 'PAY-BANK-DOC')

    def test_the_preparer_can_never_be_the_one_confirming(self):
        """Segregation of duties makes "Finance, not the preparer" structural:
        the raiser cannot sign off their own request at all."""
        first = User.objects.create_user('legakwa',
                                         email='lntabeni@alphadirect.co.bw',
                                         password='x')
        self.pr.created_by = first
        self.pr.verifier = 'Kago Tshutlhedi'
        self.pr.save(update_fields=['created_by', 'verifier'])
        self._attach()
        self.client.force_login(first)
        r = self.client.post(self.decide,
                             {'decision': 'approve', 'bank_doc_ack': True},
                             content_type='application/json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertIn('segregation of duties', r.json()['detail'])

    def test_an_unchanged_payee_signs_off_with_no_extra_friction(self):
        """Only a change gates. Otherwise every routine repeat payment would
        need a document re-checked."""
        # A separate payee, so this reads against its OWN history rather than
        # against the changed-bank request under test above.
        PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/02/0010', subject='Stationery, July',
            payee='Nonesuch Stationers', account_name='Nonesuch Stationers',
            account_number='62055551111', bank_name='FNB Botswana',
            total=Decimal('300.00'), status=PaymentRequest.Status.PAID,
            line_items=[])
        same = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/09/08/0003', subject='Stationery, August',
            payee='Nonesuch Stationers', account_name='Nonesuch Stationers',
            account_number='62055551111', bank_name='FNB Botswana',
            total=Decimal('300.00'), created_by=self.clerk,
            status=PaymentRequest.Status.PENDING_FINANCE, line_items=[])
        self.client.force_login(self.approver)
        r = self.client.post(
            reverse('v1-payment-request-decide', args=[same.id]),
            {'decision': 'approve'}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)

    def test_rejecting_never_needs_the_confirmation(self):
        """If the details do not match, rejecting is the whole point — it must
        not be gated behind confirming they DO match."""
        self.client.force_login(self.approver)
        r = self.client.post(
            self.decide,
            {'decision': 'reject',
             'notes': 'Account on the request does not match the letterhead.'},
            content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, PaymentRequest.Status.REJECTED)

    def test_the_detail_endpoint_tells_the_screen_what_changed(self):
        self.client.force_login(self.approver)
        d = self.client.get(reverse('v1-payment-request-detail',
                                    args=[self.pr.id])).json()
        self.assertEqual(d['bank_details_change']['fields'], ['bank'])
