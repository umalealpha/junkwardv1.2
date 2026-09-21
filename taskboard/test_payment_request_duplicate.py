"""taskboard/test_payment_request_duplicate.py — Copy / duplicate a payment
request (B7, CFO Build Spec, requested by Bokani Makosha).

"From the existing payment register ... it opens a NEW request in Draft with
a newly generated reference. Nothing is submitted at that point."

What these tests pin:
  * The copy is ALWAYS a draft with its own new reference, whatever the
    source's own status (paid, rejected, cancelled, with the CFO — anything).
  * Nothing that would make the copy look already-decided, already-paid, or
    already flagged survives the copy: no approval, no payment reference, no
    proof of payment, no exception/committee record, no cancel record, no
    duplicate-override record, no stamped AI summary / rendered table / POP
    heading, no inherited preparer/verifier names.
  * The person who copies becomes its creator — never the original raiser —
    so the existing maker-checker rule (an approver may never be the creator)
    still holds on the copy exactly as it would on a hand-typed request.
  * PAY-DUP-01 still fires when the copy is itself submitted — a copy is
    never a way around the duplicate-payment control.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from banking.models import BankAccount
from core.models import Company, Currency, OmniTask
from fnb.models import FNBBatchSubmission
from ledger.models import Account
from taskboard.dropbox_views import duplicate_as_draft
from taskboard.models import PaymentRequest


class _DuplicateBase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_user(
            'pganesharajah', 'pganesharajah@alphadirect.co.bw', 'x', is_superuser=True)
        cls.kago = User.objects.create_user('ktshutlhedi', 'ktshutlhedi@alphadirect.co.bw', 'x')
        cls.original_raiser = User.objects.create_user('lthebe', 'lthebe@alphadirect.co.bw', 'x')
        cls.copier = User.objects.create_user('bmakosha', 'bmakosha@alphadirect.co.bw', 'x')
        cls.outsider = User.objects.create_user('nobody', 'nobody@alphadirect.co.bw', 'x')

        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='PRDUP', defaults={'name': 'Payment-Duplicate Test Co',
                                    'base_currency': cls.bwp})
        cls.gl = Account.objects.create(
            code='PRDUP-BANK', name='Duplicate-test bank', account_type='asset',
            currency_code=cls.bwp, is_bank_account=True, owner_company=cls.company)
        cls.bank_source = BankAccount.objects.create(
            gl_account=cls.gl, bank_name='FNB', account_name='Test current',
            account_number='9' * 11, currency_code_id='BWP')

    def _fnb_batch(self, key='dup1'):
        return FNBBatchSubmission.objects.create(
            idempotency_key=f'ALPHA-EFT-DUP-{key}', source_account=self.bank_source,
            payment_count=1, total_amount_bwp=Decimal('987.47'),
            currency_code='BWP', status=FNBBatchSubmission.Status.SETTLED,
            fnb_reference=f'fnb-ref-{key}')

    def _fully_loaded_source(self, *, ref='PAY/ADIC/2026/08/07/0004'):
        """A request that has been through the ENTIRE lifecycle: signed off,
        raised as an exception, cleared, loaded to FNB and paid — every field
        this feature must NOT carry forward is deliberately filled here.
        """
        now = timezone.now()
        task = OmniTask.objects.create(
            assigner=self.kago, assignee=self.cfo,
            title=f'Payment authorisation — {ref}',
            status=OmniTask.Status.DONE, source='payment_request',
        )
        batch = self._fnb_batch()
        return PaymentRequest.objects.create(
            ref=ref, entity=self.company.name,
            category=PaymentRequest.Category.OTHER,
            currency='BWP', subject='Instant Insurance — repair invoice',
            payee='Instant Insurance Repairers',
            processing_method=PaymentRequest.ProcessingMethod.BULK,
            line_items=[{
                'description': 'Panel repair invoice INV-777', 'amount': '987.47',
                'gl_code': '5100', 'ref': 'INV-777', 'invoice_number': 'INV-777',
                # Per-line state from the SOURCE's own lifecycle — must not
                # survive onto the copy.
                'line_status': 'approved',
                'discount_checked': True,
            }],
            total=Decimal('987.47'),
            account_name='Instant Insurance Repairers', account_number='62001234567',
            bank_name='FNB Botswana', branch_code='282267', account_type='CACC',
            bank_narration='Repair invoice INV-777', bank_our_reference='PAY-0004',
            bank_payment_type='repair',
            opening_balance=Decimal('500000.00'),
            due_date=now.date(), payment_date=now.date(),
            created_by=self.original_raiser,
            status=PaymentRequest.Status.PAID,
            loaded_off_window=True,
            first_approver=self.kago, first_approved_at=now,
            decision_notes='Signed off — looked correct on the day.',
            exception_control='PAY-DUP-01',
            exception_reason='a possible duplicate of an earlier request',
            exception_raised_at=now,
            exception_decision='approve', exception_decided_at=now,
            exception_cleared_by=self.cfo, exception_cleared_at=now,
            duplicate_override_reason='Genuinely a separate invoice.',
            duplicate_override_category=PaymentRequest.OverrideCategory.SEPARATE,
            duplicate_override_approved_by=self.kago, duplicate_override_approved_at=now,
            duplicate_matches=[{'ref': 'PAY/ADIC/2026/07/28/0001', 'amount': '987.47'}],
            payment=None,     # a real payments.Payment needs a heavier fixture;
                              # fnb_batch/fnb_loaded_at below stand in as this
                              # request's "payment reference" / "proof of payment".
            fnb_batch=batch, fnb_loaded_at=now,
            pop_subject='Instant Insurance — repair invoice',
            summary='AI-written covering summary of the original request.',
            formatted_html='<table>the rendered authorisation pack</table>',
            inputter='Legakwa Thebe', verifier='Kago Tshutlhedi',
            bank_change_reason='Payee moved banks in July, confirmed by phone.',
            early_payment_reason='Settled early for the 2% discount.',
            funds_already_moved=True,
            draft_source_file='original-invoice.pdf',
            draft_needs_check=['payee'],
            draft_read_amount=Decimal('987.47'),
        )


class DuplicateAsDraftUnitTests(_DuplicateBase):
    """Exercises taskboard.dropbox_views.duplicate_as_draft() directly — the
    RED-PROVE target: a copy of an APPROVED, PAID request comes out as a
    draft with no approval and no payment reference."""

    def test_copy_of_an_approved_paid_request_is_a_clean_draft(self):
        source = self._fully_loaded_source()
        copy = duplicate_as_draft(source, self.copier)

        # The headline claim, in the fewest possible assertions.
        self.assertEqual(copy.status, PaymentRequest.Status.DRAFT)
        self.assertNotEqual(copy.ref, source.ref)
        self.assertIsNone(copy.first_approver_id)
        self.assertIsNone(copy.first_approved_at)
        self.assertIsNone(copy.payment_id)
        self.assertIsNone(copy.fnb_batch_id)
        self.assertIsNone(copy.fnb_loaded_at)

        # The source itself must be completely untouched.
        source.refresh_from_db()
        self.assertEqual(source.status, PaymentRequest.Status.PAID)
        self.assertEqual(source.ref, 'PAY/ADIC/2026/08/07/0004')

    def test_every_approval_payment_and_flag_field_is_cleared(self):
        source = self._fully_loaded_source()
        copy = duplicate_as_draft(source, self.copier)

        self.assertEqual(copy.graphite_ref, '')
        self.assertIsNone(copy.task_id)
        self.assertIsNone(copy.payment_id)
        self.assertIsNone(copy.fnb_batch_id)
        self.assertIsNone(copy.fnb_loaded_at)
        self.assertEqual(copy.fnb_load_error, '')
        self.assertIsNone(copy.first_approver_id)
        self.assertIsNone(copy.first_approved_at)
        self.assertIsNone(copy.rejected_by_id)
        self.assertIsNone(copy.rejected_at)
        self.assertEqual(copy.decision_notes, '')
        self.assertEqual(copy.exception_control, '')
        self.assertEqual(copy.exception_reason, '')
        self.assertIsNone(copy.exception_raised_at)
        self.assertEqual(copy.exception_decision, '')
        self.assertIsNone(copy.exception_decided_at)
        self.assertIsNone(copy.exception_cleared_by_id)
        self.assertIsNone(copy.exception_cleared_at)
        self.assertEqual(copy.cancelled_reason, '')
        self.assertIsNone(copy.cancelled_by_id)
        self.assertIsNone(copy.cancelled_at)
        self.assertEqual(copy.duplicate_override_reason, '')
        self.assertEqual(copy.duplicate_override_category, '')
        self.assertIsNone(copy.duplicate_override_evidence_id)
        self.assertIsNone(copy.duplicate_override_approved_by_id)
        self.assertIsNone(copy.duplicate_override_approved_at)
        self.assertEqual(copy.duplicate_matches, [])
        self.assertEqual(copy.pop_subject, '')
        self.assertEqual(copy.summary, '')
        self.assertEqual(copy.formatted_html, '')
        self.assertEqual(copy.inputter, '')
        self.assertEqual(copy.verifier, '')
        self.assertEqual(copy.bank_change_reason, '')
        self.assertEqual(copy.early_payment_reason, '')
        self.assertFalse(copy.funds_already_moved)
        self.assertEqual(copy.draft_source_file, '')
        self.assertEqual(copy.draft_needs_check, [])
        self.assertIsNone(copy.draft_read_amount)
        self.assertFalse(copy.loaded_off_window)
        # Per-line decision state is stripped too — including the line-level
        # twin of the header declarations above (Fable 5.1 audit): a supplier
        # line's "early-settlement discount checked" tick belongs to the
        # SOURCE's own invoice, not the copy's.
        self.assertNotIn('line_status', copy.line_items[0])
        self.assertNotIn('cancelled', copy.line_items[0])
        self.assertNotIn('discount_checked', copy.line_items[0])
        # Stale source-day facts (Fable 5.1 audit) — none of these are shown
        # on a draft or forwarded by submit_draft, so carrying them was dead
        # data at best.
        self.assertEqual(copy.bank_narration, '')
        self.assertEqual(copy.bank_our_reference, '')
        self.assertIsNone(copy.opening_balance)
        self.assertIsNone(copy.due_date)
        self.assertIsNone(copy.payment_date)

    def test_content_fields_carry_forward_editable(self):
        source = self._fully_loaded_source()
        copy = duplicate_as_draft(source, self.copier)

        self.assertEqual(copy.entity, source.entity)
        self.assertEqual(copy.category, source.category)
        self.assertEqual(copy.currency, source.currency)
        self.assertEqual(copy.subject, source.subject)
        self.assertEqual(copy.payee, source.payee)
        self.assertEqual(copy.account_name, source.account_name)
        self.assertEqual(copy.account_number, source.account_number)
        self.assertEqual(copy.bank_name, source.bank_name)
        self.assertEqual(copy.branch_code, source.branch_code)
        self.assertEqual(copy.processing_method, source.processing_method)
        self.assertEqual(copy.bank_payment_type, source.bank_payment_type)
        self.assertEqual(copy.line_items[0]['description'], source.line_items[0]['description'])

    def test_the_copy_arrives_with_no_amount_and_no_invoice_number(self):
        """What the Copy button has always PROMISED the raiser, now true.

        The tooltip and the success notice both say the copy opens with a
        "blank amount and invoice number". _copy_line_items kept every key, so
        the draft actually opened pre-filled with the source's figure while the
        screen said it was empty — the paid-twice shape (Manus 2026-08-10), and
        the opposite of what the CFO chose on 2026-09-15 ("copy everything
        EXCEPT the amounts").
        """
        source = self._fully_loaded_source()
        copy = duplicate_as_draft(source, self.copier)

        line = copy.line_items[0]
        for blanked in ('amount', 'invoice_number', 'invoice_date', 'due_date'):
            self.assertNotIn(blanked, line,
                             f'{blanked} describes the SOURCE invoice and must '
                             f'not be carried onto the copy')
        # And what genuinely repeats is still there, so the raiser is not
        # retyping a supplier line from scratch.
        self.assertEqual(line['description'], source.line_items[0]['description'])

    def test_creator_is_the_copier_never_the_original_raiser(self):
        source = self._fully_loaded_source()
        copy = duplicate_as_draft(source, self.copier)
        self.assertEqual(copy.created_by_id, self.copier.id)
        self.assertNotEqual(copy.created_by_id, source.created_by_id)

    def test_any_source_status_may_be_copied(self):
        for status in (PaymentRequest.Status.REJECTED, PaymentRequest.Status.CANCELLED,
                       PaymentRequest.Status.PENDING_CFO, PaymentRequest.Status.DRAFT):
            source = PaymentRequest.objects.create(
                ref=f'PAY/ADIC/2026/08/09/{status[:4].upper()}', entity=self.company.name,
                subject='x', payee='y', status=status, created_by=self.original_raiser,
                line_items=[{'description': 'x', 'amount': '10.00'}])
            copy = duplicate_as_draft(source, self.copier)
            self.assertEqual(copy.status, PaymentRequest.Status.DRAFT)


class DuplicateEndpointTests(_DuplicateBase):
    def setUp(self):
        self.url = lambda pk: reverse('v1-payment-request-duplicate', args=[pk])

    def test_endpoint_creates_a_new_draft_owned_by_the_caller(self):
        # The copier here is the CFO (unconditional view access, no settings
        # override needed) — someone who can see the request on the register
        # but is NOT the request's own raiser.
        source = self._fully_loaded_source()
        self.client.force_authenticate(self.cfo)
        r = self.client.post(self.url(source.id))
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body['status'], PaymentRequest.Status.DRAFT)
        self.assertNotEqual(body['ref'], source.ref)
        copy = PaymentRequest.objects.get(id=body['id'])
        self.assertEqual(copy.created_by_id, self.cfo.id)
        self.assertNotEqual(copy.created_by_id, source.created_by_id)
        self.assertIsNone(copy.first_approver_id)
        self.assertIsNone(copy.payment_id)

    def test_someone_with_no_view_access_may_not_duplicate(self):
        source = self._fully_loaded_source()
        self.client.force_authenticate(self.outsider)
        r = self.client.post(self.url(source.id))
        self.assertEqual(r.status_code, 403)

    def test_unknown_request_is_404(self):
        import uuid
        self.client.force_authenticate(self.copier)
        r = self.client.post(self.url(uuid.uuid4()))
        self.assertEqual(r.status_code, 404)


@override_settings(PAYMENT_FIRST_APPROVER_EMAILS=[
    'ktshutlhedi@alphadirect.co.bw', 'bmakosha@alphadirect.co.bw'])
class DuplicateControlsStillFireTests(_DuplicateBase):
    """The copy is never a way around a control — it is submitted through the
    exact same create endpoint as a hand-typed request."""

    def test_processing_method_survives_submit_not_silently_flipped_to_bulk(self):
        # Fable 5.1 audit: submit_draft used to forward only a fixed set of
        # fields, so a copy of an INDIVIDUAL request (one payment per invoice)
        # was silently submitted as BULK — the create endpoint defaults
        # processing_method to Bulk when the key is missing from the payload.
        # Two payable lines, so the Bulk/Individual choice is a real decision
        # (with a single line the two are the same one payment and the create
        # endpoint always forces Bulk regardless of what is asked for).
        source = PaymentRequest.objects.create(
            ref='PAY/ADIC/2026/08/09/IND1', entity=self.company.name,
            category=PaymentRequest.Category.OTHER, currency='BWP',
            subject='Two supplier invoices', payee='Two Invoice Supplier',
            processing_method=PaymentRequest.ProcessingMethod.INDIVIDUAL,
            line_items=[
                {'description': 'Invoice A', 'amount': '30.00', 'invoice_number': 'INV-A'},
                {'description': 'Invoice B', 'amount': '20.00', 'invoice_number': 'INV-B'},
            ],
            total=Decimal('50.00'), account_name='x', account_number='1',
            bank_name='FNB', created_by=self.original_raiser,
            status=PaymentRequest.Status.PAID,
        )
        self.client.force_authenticate(self.copier)
        dup_resp = self.client.post(reverse('v1-payment-request-duplicate', args=[source.id]))
        self.assertEqual(dup_resp.status_code, 201, dup_resp.content)
        draft_id = dup_resp.json()['id']

        # The copy now arrives with no amount and no invoice number (the fix
        # to _copy_line_items), so the raiser keys this month's figures —
        # which is the whole point. Two lines are kept so the Bulk/Individual
        # choice stays a real decision.
        self.client.patch(
            reverse('v1-payment-request-draft-detail', args=[draft_id]),
            {'line_items': [{'description': 'Invoice A', 'amount': '30.00', 'invoice_number': 'INV-A2'},
                            {'description': 'Invoice B', 'amount': '20.00', 'invoice_number': 'INV-B2'}]}, format='json')

        submit_resp = self.client.post(
            reverse('v1-payment-request-draft-submit', args=[draft_id]),
            {'new_payee_confirmed': True}, format='json')
        self.assertEqual(submit_resp.status_code, 201, submit_resp.content)
        new_pr = PaymentRequest.objects.get(id=submit_resp.json()['id'])
        self.assertEqual(new_pr.processing_method, PaymentRequest.ProcessingMethod.INDIVIDUAL)

    def test_pay_dup_01_still_fires_when_the_copy_is_submitted(self):
        # The source's own paid line is what the copy will clash against —
        # same reference, same amount — proving PAY-DUP-01 does not get
        # bypassed just because the request arrived via "duplicate".
        source = self._fully_loaded_source()
        self.client.force_authenticate(self.copier)

        dup_resp = self.client.post(reverse('v1-payment-request-duplicate', args=[source.id]))
        self.assertEqual(dup_resp.status_code, 201, dup_resp.content)
        draft_id = dup_resp.json()['id']

        # The copy now arrives with no amount and no invoice number (the fix
        # to _copy_line_items), so the raiser keys this month's figures —
        # which is the whole point. Here the raiser deliberately re-keys the
        # SOURCE's own invoice and figure, which is precisely the mistake
        # PAY-DUP-01 exists to catch.
        self.client.patch(
            reverse('v1-payment-request-draft-detail', args=[draft_id]),
            {'line_items': [{'description': 'Panel repair invoice INV-777', 'amount': '987.47',
                            'invoice_number': 'INV-777', 'ref': 'INV-777'}]}, format='json')

        submit_resp = self.client.post(
            reverse('v1-payment-request-draft-submit', args=[draft_id]),
            {'new_payee_confirmed': True}, format='json')
        self.assertEqual(submit_resp.status_code, 201, submit_resp.content)
        body = submit_resp.json()
        self.assertEqual(body['status'], PaymentRequest.Status.EXCEPTION)
        self.assertEqual(body['exception']['control'], 'PAY-DUP-01')

    def test_maker_checker_still_blocks_the_copier_approving_their_own_copy(self):
        # The copier becomes the new request's creator, so the existing rule
        # (an approver may never be the request's own creator) must still
        # refuse them at sign-off — exactly as it would on a hand-typed one.
        source = self._fully_loaded_source()
        self.client.force_authenticate(self.copier)
        dup_resp = self.client.post(reverse('v1-payment-request-duplicate', args=[source.id]))
        draft_id = dup_resp.json()['id']
        # Give it a fresh, non-clashing line so this test isolates maker-checker
        # from PAY-DUP-01.
        self.client.patch(
            reverse('v1-payment-request-draft-detail', args=[draft_id]),
            {'line_items': [{'description': 'A brand new invoice', 'amount': '55.00',
                            'invoice_number': 'INV-NEW-1'}]}, format='json')
        submit_resp = self.client.post(
            reverse('v1-payment-request-draft-submit', args=[draft_id]),
            {'new_payee_confirmed': True}, format='json')
        self.assertEqual(submit_resp.status_code, 201, submit_resp.content)
        new_ref_id = submit_resp.json()['id']

        # The copier (== created_by, and also a finance approver here) tries
        # to sign off the very request they just duplicated.
        decide_url = reverse('v1-payment-request-decide', args=[new_ref_id])
        r = self.client.post(decide_url, {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertIn('segregation of duties', r.json()['detail'])


@override_settings(PAYMENT_FIRST_APPROVER_EMAILS=[
    'ktshutlhedi@alphadirect.co.bw', 'bmakosha@alphadirect.co.bw'])
class ExcessRefundCopyClaimNumberTests(_DuplicateBase):
    """PAY-REFUND-03 on the copy path (CFO 2026-09-14).

    An excess only exists because of a claim, so a refund of one must name
    that claim. A copy that kept the SOURCE's claim number would look
    already verified when it is not — so, exactly like original_payment_ref,
    it is deliberately NOT carried, and the draft says so on its own.

    Same PAYMENT_FIRST_APPROVER_EMAILS override as DuplicateControlsStillFireTests
    above — self.copier must be able to VIEW the source to duplicate it
    (_can_view_request), which is a separate question from whether they may
    approve it.
    """

    def _excess_refund_source(self, *, ref='PAY/ADIC/2026/08/09/EXC1'):
        return PaymentRequest.objects.create(
            ref=ref, entity=self.company.name,
            category=PaymentRequest.Category.EXCESS_REFUND, currency='BWP',
            subject='Excess refund', payee='A Policyholder',
            original_payment_ref='RCPT-778',
            line_items=[{'description': 'Excess refund', 'amount': '1500.00',
                        'claim_number': 'G2026004287'}],
            total=Decimal('1500.00'), account_name='A Policyholder',
            account_number='1', bank_name='FNB',
            created_by=self.original_raiser, status=PaymentRequest.Status.PAID,
        )

    def test_the_claim_number_does_not_carry_and_is_flagged(self):
        source = self._excess_refund_source()
        copy = duplicate_as_draft(source, self.copier)
        self.assertNotIn('claim_number', copy.line_items[0])
        self.assertIn('claim_number', copy.draft_needs_check)
        # original_payment_ref was already known not to carry — both must be
        # asked for again, not just the newer one.
        self.assertEqual(copy.original_payment_ref, '')

    def test_a_non_excess_refund_copy_is_not_flagged_for_a_claim_number(self):
        """An erroneous payment has no claim behind it — a gate on a document
        that cannot exist never opens."""
        source = self._excess_refund_source(ref='PAY/ADIC/2026/08/09/EXC2')
        source.category = PaymentRequest.Category.OTHER
        source.save(update_fields=['category'])
        copy = duplicate_as_draft(source, self.copier)
        self.assertNotIn('claim_number', copy.draft_needs_check)
        # A non-refund copy still carries whatever claim number was on the
        # source line — only the excess-refund category strips it.
        self.assertEqual(copy.line_items[0].get('claim_number'), 'G2026004287')

    def test_submitting_the_copy_without_a_claim_number_is_refused(self):
        """The gate on the create endpoint (PAY-REFUND-03) still fires on
        submit even though the copy's draft never enforces it itself."""
        source = self._excess_refund_source(ref='PAY/ADIC/2026/08/09/EXC3')
        self.client.force_authenticate(self.copier)
        dup_resp = self.client.post(reverse('v1-payment-request-duplicate', args=[source.id]))
        self.assertEqual(dup_resp.status_code, 201, dup_resp.content)
        draft_id = dup_resp.json()['id']
        # The copy arrives with no amount (the _copy_line_items fix), so key a
        # figure — otherwise the submit is refused for a zero total and this
        # test would never reach the gate it is actually about.
        self.client.patch(
            reverse('v1-payment-request-draft-detail', args=[draft_id]),
            {'original_payment_ref': 'RCPT-999',
             'line_items': [{'description': 'Excess refund', 'amount': '250.00'}]},
            format='json')

        submit_resp = self.client.post(
            reverse('v1-payment-request-draft-submit', args=[draft_id]),
            {'new_payee_confirmed': True}, format='json')
        self.assertEqual(submit_resp.status_code, 400, submit_resp.content)
        self.assertEqual(submit_resp.json()['control'], 'PAY-REFUND-03')
