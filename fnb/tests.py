"""fnb/tests.py — placeholder smoke tests."""

from django.test import TestCase


class FNBSmokeTests(TestCase):
    def test_module_imports(self):
        from fnb import client, statements, payments, beneficiaries, webhooks
        self.assertTrue(hasattr(client, 'FNBClient'))
        self.assertTrue(hasattr(statements, 'pull_statement'))
        self.assertTrue(hasattr(payments, 'submit_eft_batch'))
        self.assertTrue(hasattr(beneficiaries, 'sync_beneficiary'))
        self.assertTrue(hasattr(webhooks, 'dispatch_webhook'))


class FNBCharacterSetTests(TestCase):
    """RR10 INVALID CHARACTER SET — the real 2026-08-19 production reject.

    FNB rejected batch ALPHA-EFT-20260819-5c20911cd0b847a0 (groupStatus RJCT,
    transactionStatus VALIDATION_FAILED, reason RR10) because
    remittanceInformationUnstructured read `One-off — PAYEE NAME`: the em dash
    is outside the set in RMB spec V-03 §2.5.
    """

    def test_em_dash_is_folded_to_hyphen(self):
        from fnb.payments import fnb_text
        self.assertEqual(fnb_text('One-off — PAYEE NAME'), 'One-off - PAYEE NAME')

    def test_typographic_and_accented_characters_are_folded(self):
        from fnb.payments import fnb_text
        cases = {
            'en–dash': 'en-dash',
            'minus−sign': 'minus-sign',
            'curly’s': "curly's",
            '“quoted”': '"quoted"',
            'André René': 'Andre Rene',
            'tab\tsep': 'tab sep',
            'nbsp gap': 'nbsp gap',
            'ellipsis…': 'ellipsis...',
        }
        for raw, want in cases.items():
            self.assertEqual(fnb_text(raw), want, msg=f'input {raw!r}')

    def test_spec_permitted_punctuation_survives_untouched(self):
        """§2.5 is WIDER than SWIFT-x: & @ # % * $ [ ] _ etc are all legal and
        must NOT be rewritten — doing so would corrupt real vendor names."""
        from fnb.payments import fnb_text, FNB_PERMITTED_PUNCTUATION
        keep = 'ABCxyz012' + FNB_PERMITTED_PUNCTUATION
        self.assertEqual(fnb_text(keep), keep.strip())
        self.assertEqual(fnb_text('Smith & Sons (Pty) Ltd'), 'Smith & Sons (Pty) Ltd')
        self.assertEqual(fnb_text('INV#77/2026 100% $ [ref] a_b'),
                         'INV#77/2026 100% $ [ref] a_b')

    def test_characters_outside_the_spec_are_dropped(self):
        from fnb.payments import fnb_text
        self.assertEqual(fnb_text('PAY €100'), 'PAY 100')
        self.assertEqual(fnb_text('emoji \U0001f600 here'), 'emoji here')
        # ` { | } ~ are printable ASCII but NOT in §2.5
        self.assertEqual(fnb_text('a`b{c}d|e~f'), 'abcdef')

    def test_limit_truncates_after_folding(self):
        from fnb.payments import fnb_text
        self.assertEqual(fnb_text('ABCDEFGHIJ', limit=4), 'ABCD')
        # fold first, then cut — cutting first would strand a mid-substitution
        self.assertEqual(fnb_text('André', limit=4), 'Andr')

    def test_a_name_entirely_outside_the_set_folds_to_empty(self):
        """Premise of the post-fold fallback in build_batch_payload: a name in
        a non-Latin script folds to '', so an `or` on the RAW value never fires
        and a mandatory field would be sent empty."""
        from fnb.payments import fnb_text
        self.assertEqual(fnb_text('\u0418\u0432\u0430\u043d\u043e\u0432'), '')

    def test_permitted_set_matches_the_spec_exactly(self):
        """Pin the set to RMB spec V-03 2.5 — no extras, no omissions, so a
        later edit cannot quietly widen or narrow what we send the bank."""
        import string
        from fnb.payments import FNB_ALLOWED_CHARS
        spec = set(string.ascii_letters + string.digits + ' !"#$%&\'()*+,-./:;<=>?@[\\]^_')
        self.assertEqual(FNB_ALLOWED_CHARS, spec)
        self.assertEqual(
            sorted(set(string.printable[:95]) - FNB_ALLOWED_CHARS),
            ['`', '{', '|', '}', '~'],
        )

    def test_none_and_blank_are_safe(self):
        from fnb.payments import fnb_text
        self.assertEqual(fnb_text(None), '')
        self.assertEqual(fnb_text('   '), '')

    def test_payload_guard_rejects_disallowed_character(self):
        """A field added later without folding must be caught BEFORE the POST."""
        from django.core.exceptions import ValidationError
        from fnb.payments import assert_fnb_charset
        assert_fnb_charset({
            'groupHeader': {'messageId': 'ALPHA-EFT-20260819-abc'},
            'paymentInformation': [{'debtor': {'name': 'Alpha Direct Insurance'}}],
        })
        with self.assertRaises(ValidationError) as ctx:
            assert_fnb_charset({'paymentInformation': [
                {'creditTransferTransactionInformation': [
                    {'remittanceInformationUnstructured': 'One-off — PAYEE NAME'}]}]})
        msg = str(ctx.exception)
        self.assertIn('remittanceInformationUnstructured', msg)
        self.assertIn('U+2014', msg)
        # The value is a payee name / remittance narrative and this message
        # reaches API responses and logs — it must never carry the value.
        self.assertNotIn('PAYEE NAME', msg)
        self.assertNotIn('One-off', msg)

    def test_payload_guard_allows_an_email_address(self):
        """'@' '.' '-' '_' are all in §2.5, so a remittance email is fine."""
        from fnb.payments import assert_fnb_charset
        assert_fnb_charset({'paymentInformation': [
            {'creditTransferTransactionInformation': [
                {'remittanceLocationMethod': 'EMAL',
                 'remittanceLocationElectronicAddress': 'ap@vendor.co.bw'}]}]})


class PollFNBBatchesCommandTests(TestCase):
    """The sweep that turns a silent RJCT into a visible failure.

    Before 2026-08-20 nothing called refresh_batch_status outside the operator's
    Refresh button, so FNB's reject of the 19-Aug batch sat unread while the
    screen still said "Submitted to FNB".
    """

    @classmethod
    def setUpTestData(cls):
        # Build the account outright. Reaching for BankAccount.objects.first()
        # made both tests skip on a clean database, so the suite reported green
        # over a command with no executed coverage — and a skipped test can
        # never go red. Pattern copied from
        # banking/test_statement_date_timezone.py.
        from banking.models import BankAccount
        from core.models import Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        gl = Account.objects.create(
            code='1118', name='Test FNB batch poll clearing',
            account_type='asset', sub_type='test', is_active=True,
            is_bank_account=True)
        cls.account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Test current',
            account_number='000000002', currency_code_id='BWP')

    def _batch(self, status, ref='R1G66R-ref-1'):
        from fnb.models import FNBBatchSubmission
        return FNBBatchSubmission.objects.create(
            idempotency_key=f'ALPHA-EFT-TEST-{status}-{ref}',
            source_account=self.account, payment_count=1, total_amount_bwp=10,
            currency_code='BWP', status=status, fnb_reference=ref,
        )

    def test_in_flight_batches_are_polled_and_terminal_ones_are_not(self):
        from unittest import mock
        from django.core.management import call_command
        from io import StringIO
        from fnb.models import FNBBatchSubmission as B

        submitted = self._batch(B.Status.SUBMITTED, 'ref-submitted')
        self._batch(B.Status.SETTLED, 'ref-settled')       # terminal, skip
        self._batch(B.Status.FAILED, 'ref-failed')         # terminal, skip
        no_ref = self._batch(B.Status.SUBMITTED, 'ref-none')
        B.objects.filter(pk=no_ref.pk).update(fnb_reference='')  # nothing to poll

        polled = []

        def fake_refresh(batch, **kw):
            polled.append(batch.idempotency_key)
            B.objects.filter(pk=batch.pk).update(
                status=B.Status.FAILED,
                failure_reason='RR10: INVALID CHARACTER SET')
            return {}

        out = StringIO()
        with mock.patch('fnb.management.commands.poll_fnb_batches'
                        '.refresh_batch_status', side_effect=fake_refresh):
            call_command('poll_fnb_batches', stdout=out, stderr=StringIO())

        self.assertEqual(polled, [submitted.idempotency_key])
        submitted.refresh_from_db()
        self.assertEqual(submitted.status, B.Status.FAILED)
        text = out.getvalue()
        self.assertIn('1 checked', text)
        self.assertIn('1 changed', text)
        self.assertIn('RR10', text)

    def test_one_failing_poll_does_not_stop_the_sweep(self):
        from unittest import mock
        from django.core.management import call_command
        from io import StringIO
        from fnb.models import FNBBatchSubmission as B

        first = self._batch(B.Status.SUBMITTED, 'ref-boom')
        second = self._batch(B.Status.SUBMITTED, 'ref-ok')
        # created_at ordering decides which is hit first
        seen = []

        def flaky(batch, **kw):
            seen.append(batch.idempotency_key)
            if batch.pk == first.pk:
                raise RuntimeError('gateway timeout')
            return {}

        out, err = StringIO(), StringIO()
        with mock.patch('fnb.management.commands.poll_fnb_batches'
                        '.refresh_batch_status', side_effect=flaky):
            call_command('poll_fnb_batches', stdout=out, stderr=err)

        self.assertIn(second.idempotency_key, seen)
        self.assertIn('1 errored', out.getvalue())
        self.assertIn('gateway timeout', err.getvalue())


    def test_a_425_means_not_processed_yet_and_is_neither_an_error_nor_a_stop(self):
        """FNB answers `425 Too Early` for a batch it has not processed yet
        (usually: still awaiting authorisation on the bank). Before 2026-09-04
        the sweep logged each one as a failure — "38 checked, 36 errored" every
        five minutes — which read like an outage and hid the one real error."""
        from unittest import mock
        from django.core.management import call_command
        from io import StringIO
        from fnb.client import FNBAPIError
        from fnb.models import FNBBatchSubmission as B

        first = self._batch(B.Status.SUBMITTED, 'ref-processed-1')
        waiting = self._batch(B.Status.SUBMITTED, 'ref-awaiting-auth')
        last = self._batch(B.Status.SUBMITTED, 'ref-processed-2')
        seen = []

        def fnb(batch, **kw):
            seen.append(batch.idempotency_key)
            if batch.pk == waiting.pk:
                raise FNBAPIError(425, '{"code":"Too Early. Retry-After 120 seconds"}')
            return {}

        out, err = StringIO(), StringIO()
        with mock.patch('fnb.management.commands.poll_fnb_batches'
                        '.refresh_batch_status', side_effect=fnb):
            call_command('poll_fnb_batches', stdout=out, stderr=err)

        # every batch was still polled, in order, with no pause or early stop
        self.assertEqual(seen, [first.idempotency_key, waiting.idempotency_key,
                                last.idempotency_key])
        text = out.getvalue()
        self.assertIn('3 checked', text)
        self.assertIn('0 errored', text)
        self.assertIn('1 not yet processed by FNB', text)
        self.assertEqual(err.getvalue(), '')     # a 425 is not written as a failure


class RejectCodeExplanationTests(TestCase):
    """Plain English for the bank's reject codes.

    `RR10` alone took a spec PDF to decode, and it is one of about a hundred
    codes. The table answers the ones we meet; AI covers the rest and is
    labelled as unverified. Neither decides whether a payment may be sent.
    """

    def test_a_reason_that_is_not_code_shaped_never_reaches_the_model(self):
        """`reason` is bank-controlled as well — shape-gate it (Fable F4)."""
        from unittest import mock
        from fnb.reject_codes import explain_reason
        with mock.patch('core.ai_assist.reasoning_complete') as ai:
            out = explain_reason('payee Testcorp Holdings was not found')
        ai.assert_not_called()
        self.assertEqual(out['source'], 'none')

    def test_an_ai_answer_is_cached_so_the_sweep_does_not_re_ask(self):
        """This runs every 5 minutes for up to 30 days on an ACKNOWLEDGED
        batch; re-running the engine chain each time would flood the AI log and
        make failure_reason flap (Fable F1)."""
        from unittest import mock
        from django.core.cache import cache
        from fnb.reject_codes import explain_reason
        cache.delete('fnb_reject_ai:ZZ94')
        reply = 'MEANING: Something the table does not know.\nACTION: Ask FNB.'
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value=reply) as ai:
            first = explain_reason('ZZ94')
            second = explain_reason('ZZ94')
        self.assertEqual(ai.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(second['source'], 'ai')

    def test_ag01_does_not_claim_a_side(self):
        """Asserting the bank's fault when it may be ours is the misdirection
        this table exists to prevent (Fable F2)."""
        from fnb.reject_codes import explain_reason
        out = explain_reason('AG01', use_ai=False)
        self.assertEqual(out['ours'], 'unknown')

    def test_known_code_comes_from_the_table_without_calling_ai(self):
        from unittest import mock
        from fnb.reject_codes import explain_reason
        with mock.patch('core.ai_assist.reasoning_complete') as ai:
            out = explain_reason('RR10')
        ai.assert_not_called()
        self.assertEqual(out['source'], 'table')
        self.assertEqual(out['ours'], 'yes')
        self.assertIn('character', out['plain'].lower())
        self.assertTrue(out['action'])

    def test_unknown_code_falls_back_to_ai_and_is_labelled(self):
        from unittest import mock
        from fnb.reject_codes import describe_rejection
        reply = 'MEANING: The bank could not read the payment file.\nACTION: Ask FNB to resend the report.'
        with mock.patch('core.ai_assist.reasoning_complete', return_value=reply) as ai:
            text = describe_rejection('RJCT', [{'reason': 'ZZ99',
                                                'additionalInformation': 'someField'}])
        ai.assert_called_once()
        self.assertIn('could not read the payment file', text)
        self.assertIn('AI reading', text)          # never passed off as verified
        self.assertIn('No money moved', text)      # group status still explained

    @staticmethod
    def _sent_text(ai_mock):
        parts = [str(a) for a in ai_mock.call_args.args]
        parts += [f'{k}={v}' for k, v in (ai_mock.call_args.kwargs or {}).items()]
        return ' '.join(parts)

    def test_the_prompt_carries_only_the_code_and_the_field_name(self):
        """AD-POL-AI-GOV-001 — nothing but the code and the field name goes out.

        Asserted positively (what IS in the prompt) rather than by listing
        values we hope are absent, so a value the code starts forwarding later
        cannot slip past a stale blocklist.
        """
        from unittest import mock
        from fnb.reject_codes import explain_reason
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value='MEANING: x\nACTION: y') as ai:
            explain_reason('ZZ98', field='creditorAccount')
        sent = self._sent_text(ai)
        self.assertIn('ZZ98', sent)
        self.assertIn('creditorAccount', sent)
        # No digit run long enough to be an account number.
        import re
        self.assertEqual(re.findall(r'\d{6,}', sent), [])

    def test_the_banks_free_text_is_never_forwarded_to_the_model(self):
        """additionalInformation is the bank's own free text. Nothing stops FNB
        putting a payee detail in it, so it is DROPPED unless it is a field name
        we recognise — not redacted and sent (/fabe OpenAI C5)."""
        from unittest import mock
        from fnb.reject_codes import explain_reason, field_hint
        # Built, not written as a literal: an account-shaped digit run in source
        # trips the repo's own PII tripwire, correctly — it cannot tell a fake
        # from a real one and should not have to.
        fake_acct = '0' * 11
        planted = f'account {fake_acct} for Testcorp Holdings'
        self.assertEqual(field_hint(planted), '')
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value='MEANING: x\nACTION: y') as ai:
            explain_reason('ZZ96', field=planted)
        sent = self._sent_text(ai)
        self.assertNotIn(fake_acct, sent)
        self.assertNotIn('Testcorp', sent)
        self.assertIn('ZZ96', sent)

    def test_a_recognised_field_name_is_still_passed_as_a_hint(self):
        from fnb.reject_codes import field_hint
        self.assertEqual(field_hint('creditorAccount'), 'creditorAccount')
        self.assertEqual(field_hint(' endToEndId '), 'endToEndId')
        self.assertEqual(field_hint('INVALID CHARACTER SET'), '')

    def test_ai_failure_still_leaves_the_reject_readable(self):
        """Omni must keep working when the AI box is down."""
        from unittest import mock
        from fnb.reject_codes import describe_rejection, explain_reason
        with mock.patch('core.ai_assist.reasoning_complete',
                        side_effect=RuntimeError('engine down')):
            self.assertEqual(explain_reason('ZZ97')['source'], 'none')
            text = describe_rejection('RJCT', [{'reason': 'ZZ97'}])
        self.assertIn('ZZ97', text)
        self.assertIn('No money moved', text)
        self.assertIn('ask fnb', text.lower())

    def test_the_real_2026_08_19_reject_reads_as_plain_english(self):
        from fnb.reject_codes import describe_rejection
        text = describe_rejection(
            'RJCT', [{'reason': 'RR10', 'additionalInformation': 'INVALID CHARACTER SET'}],
            use_ai=False)
        self.assertIn('No money moved', text)
        self.assertIn('long dash', text)
        self.assertIn('ours to fix', text)
        self.assertNotIn('[AI reading', text)      # came from the table

    def test_acwc_is_explained_rather_than_unknown(self):
        from fnb.reject_codes import describe_rejection
        text = describe_rejection('ACWC', [], use_ai=False)
        self.assertIn('changed something', text)
        self.assertIn('WILL go', text)


class BatchStatusMappingTests(TestCase):
    """groupStatus -> our status. ACWC was previously unhandled and fell through
    to "leave the status as-is", so an accepted-with-change batch sat in
    SUBMITTED for ever while the money moved."""

    @classmethod
    def setUpTestData(cls):
        from banking.models import BankAccount
        from core.models import Currency
        from ledger.models import Account
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        gl = Account.objects.create(
            code='1117', name='Test FNB mapping clearing', account_type='asset',
            sub_type='test', is_active=True, is_bank_account=True)
        cls.account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Test current',
            account_number='000000003', currency_code_id='BWP')

    def _batch(self, ref):
        from fnb.models import FNBBatchSubmission
        return FNBBatchSubmission.objects.create(
            idempotency_key=f'ALPHA-EFT-TEST-MAP-{ref}',
            source_account=self.account, payment_count=1, total_amount_bwp=10,
            currency_code='BWP', status=FNBBatchSubmission.Status.SUBMITTED,
            fnb_reference=ref,
        )

    def _refresh(self, batch, body):
        from unittest import mock
        from fnb.payments import refresh_batch_status

        class _Resp:
            def __init__(self, j):
                self.json = j
                self.status_code = 200

        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.get.return_value = _Resp(body)
            refresh_batch_status(batch)
        batch.refresh_from_db()
        return batch

    def test_acwc_moves_the_batch_off_submitted(self):
        from fnb.models import FNBBatchSubmission as B
        b = self._refresh(self._batch('ref-acwc'), {'groupStatus': 'ACWC'})
        self.assertEqual(b.status, B.Status.ACKNOWLEDGED)
        self.assertIn('changed something', b.failure_reason)

    def test_rjct_records_the_plain_english_reason(self):
        from fnb.models import FNBBatchSubmission as B
        b = self._refresh(self._batch('ref-rjct'), {
            'groupStatus': 'RJCT',
            'originalPaymentInformation': [{
                'statusReasonInformation': [],
                'transactionInfoAndStatus': [{
                    'originalEndToEndId': 'PAY-OUT-TEST-1',
                    'transactionStatus': 'VALIDATION_FAILED',
                    'statusReasonInformation': [
                        {'reason': 'RR10',
                         'additionalInformation': 'INVALID CHARACTER SET'}],
                }],
            }],
        })
        self.assertEqual(b.status, B.Status.FAILED)
        self.assertIn('RR10', b.failure_reason)          # raw code kept
        self.assertIn('long dash', b.failure_reason)     # and explained
        self.assertIn('ours to fix', b.failure_reason)

    def test_settled_is_not_decorated_with_a_reject_explanation(self):
        from fnb.models import FNBBatchSubmission as B
        b = self._refresh(self._batch('ref-acsc'), {'groupStatus': 'ACSC'})
        self.assertEqual(b.status, B.Status.SETTLED)
        self.assertEqual(b.failure_reason, '')

    def test_settled_synonyms_are_not_decorated_either(self):
        """COMPLETED / SETTLED / PROCESSED also map to SETTLED, so they must
        not pick up reject-flavoured text or fire an AI call.

        The raw code the bank sent is still recorded — that join is
        pre-existing behaviour and is deliberately left alone. What must not
        happen is a settled batch being described as a rejection.
        """
        from unittest import mock
        from fnb.models import FNBBatchSubmission as B
        for i, status in enumerate(('COMPLETED', 'SETTLED', 'PROCESSED')):
            with mock.patch('core.ai_assist.reasoning_complete') as ai:
                b = self._refresh(self._batch(f'ref-syn-{i}'), {
                    'groupStatus': status,
                    'statusReasonInformation': [{'reason': 'ZZ95'}],
                })
            self.assertEqual(b.status, B.Status.SETTLED, msg=status)
            ai.assert_not_called()
            # no explanation appended: no separator, no reject wording
            self.assertNotIn('—', b.failure_reason, msg=status)
            self.assertNotIn('ask FNB', b.failure_reason, msg=status)
            self.assertNotIn('no explanation available', b.failure_reason,
                             msg=status)


class _PaymentFixture(TestCase):
    """Shared, real fixtures. Payment.bank_account is a ledger Account, NOT a
    banking.BankAccount — getting that wrong is what broke the first cut of
    these tests."""

    @classmethod
    def setUpTestData(cls):
        from datetime import date
        from django.contrib.auth.models import User
        from billing.models import Contact
        from core.models import Company, Currency
        from ledger.models import Account
        from banking.models import BankAccount

        cls.bwp, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        cls.company, _ = Company.objects.get_or_create(
            code='FNBG', defaults={'name': 'FNB Guard Test Co',
                                   'base_currency': cls.bwp})
        # Superuser, because the preview endpoint is gated on
        # CanViewFinancials. Adjust the fixture to satisfy the gate; never
        # loosen the gate to satisfy the fixture.
        cls.staff = User.objects.create_superuser(
            'fnbguardclerk', 'fnbguardclerk@example.com', 'x')
        # A maker title as well: editing a payment goes through
        # PaymentViewSet._require_maker, and a superuser with NO profile row at
        # all is still refused (get_user_profile returns None).
        cls.staff_profile = cls._maker_profile(cls.staff)
        # Releasing to the bank now requires someone other than the creator
        # (CFO 2026-08-20), so the fixture carries a second person.
        cls.releaser = User.objects.create_superuser(
            'fnbguardreleaser', 'fnbguardreleaser@example.com', 'x')
        cls._maker_profile(cls.releaser)
        cls.gl_bank, _ = Account.objects.get_or_create(
            code='FNBG-BANK',
            defaults={'name': 'FNB Guard Test Bank', 'account_type': 'asset',
                      'currency_code': cls.bwp, 'is_bank_account': True})
        cls.payee = Contact.objects.create(
            contact_type=Contact.ContactType.VENDOR, name='Guard Test Supplier',
            currency_code=cls.bwp, company=cls.company)
        cls.source_account = BankAccount.objects.create(
            gl_account=cls.gl_bank, bank_name='FNB',
            account_name='Guard test current', account_number='000000004',
            currency_code_id='BWP')
        cls.today = date(2026, 8, 20)

    @staticmethod
    def _maker_profile(user):
        from core.models import UserProfile
        prof, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER,
                      'is_active': True})
        if prof.title != UserProfile.Title.FINANCIAL_CONTROLLER:
            prof.title = UserProfile.Title.FINANCIAL_CONTROLLER
            prof.is_active = True
            prof.save()
        return prof

    def _draft_payment(self, amount, **extra):
        """A DRAFT payment. Editing is draft-only in Omni ('Only draft payments
        can be edited'), so this is where a narration actually gets typed —
        while the payment is being raised, before the approvers see it."""
        from decimal import Decimal
        from payments.models import Payment
        return Payment.objects.create(
            payment_type=Payment.PaymentType.SENT,
            contact=self.payee, company=self.company,
            bank_account=self.gl_bank, payment_date=self.today,
            currency_code=self.bwp, amount=Decimal(amount),
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            status=Payment.Status.DRAFT, is_once_off=True,
            payee_name='Guard Test Supplier', payee_account_number='1234567',
            payee_branch_code='293567', created_by=self.staff, **extra)

    def _payment(self, amount, approval, **extra):
        from decimal import Decimal
        from payments.models import Payment
        return Payment.objects.create(
            payment_type=Payment.PaymentType.SENT,
            contact=self.payee, company=self.company,
            bank_account=self.gl_bank, payment_date=self.today,
            currency_code=self.bwp, amount=Decimal(amount),
            payment_method=Payment.PaymentMethod.BANK_TRANSFER,
            status=Payment.Status.CONFIRMED, approval_status=approval,
            is_once_off=True, payee_name='Guard Test Supplier',
            payee_account_number='1234567', payee_branch_code='293567',
            created_by=self.staff, **extra)


class BatchCeilingTests(_PaymentFixture):
    """Per-batch ceiling, enforced in submit_eft_batch.

    All four paths to the bank (EFT submit view, quick transfer, customer
    refunds, payroll disbursement) go through that function, so a cap on the
    view alone would leave three doors open.
    """

    def test_unset_falls_back_to_the_documented_default(self):
        from django.test import override_settings
        from fnb.payments import _batch_ceiling, FNB_BATCH_MAX_BWP_DEFAULT
        for blank in (None, ''):
            with override_settings(FNB_BATCH_MAX_BWP=blank):
                self.assertEqual(_batch_ceiling(), FNB_BATCH_MAX_BWP_DEFAULT)

    def test_a_malformed_or_zero_limit_refuses_rather_than_guessing(self):
        """A control on money must not substitute a number nobody chose."""
        from django.core.exceptions import ValidationError
        from django.test import override_settings
        from fnb.payments import _batch_ceiling
        for bad in ('not a number', '0', '-5'):
            with override_settings(FNB_BATCH_MAX_BWP=bad):
                with self.assertRaises(ValidationError, msg=bad):
                    _batch_ceiling()

    def test_a_valid_limit_is_read(self):
        from decimal import Decimal
        from django.test import override_settings
        from fnb.payments import _batch_ceiling
        with override_settings(FNB_BATCH_MAX_BWP='500'):
            self.assertEqual(_batch_ceiling(), Decimal('500'))

    def test_over_the_ceiling_is_refused_before_a_row_or_a_post(self):
        from unittest import mock
        from django.core.exceptions import ValidationError
        from django.test import override_settings
        from fnb.models import FNBBatchSubmission
        from fnb.payments import submit_eft_batch
        from payments.models import Payment

        p = self._payment('750', Payment.ApprovalStatus.APPROVED)
        before = FNBBatchSubmission.objects.count()
        with override_settings(FNB_BATCH_MAX_BWP='500'), \
                mock.patch('fnb.payments.FNBClient') as client:
            with self.assertRaises(ValidationError) as ctx:
                submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                 source_account=self.source_account,
                                 user=self.releaser)
            client.assert_not_called()                      # never reached the bank
        self.assertIn('over the per-batch limit', str(ctx.exception))
        self.assertEqual(FNBBatchSubmission.objects.count(), before)   # no row
        p.refresh_from_db()
        self.assertIsNone(p.bank_submitted_at)              # payment not stamped

    def test_within_the_ceiling_proceeds_to_the_bank(self):
        """The ceiling must not block a legitimate batch — otherwise the test
        above would pass with the guard nailed permanently shut."""
        from unittest import mock
        from django.test import override_settings
        from fnb.payments import submit_eft_batch
        from payments.models import Payment

        p = self._payment('250', Payment.ApprovalStatus.APPROVED)

        class _Resp:
            json = {'instructionId': 'R1G66R-test-ok'}
            status_code = 200

        with override_settings(FNB_BATCH_MAX_BWP='500'), \
                mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.return_value = _Resp()
            batch = submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                     source_account=self.source_account,
                                     user=self.releaser)
        self.assertEqual(batch.fnb_reference, 'R1G66R-test-ok')


class SubmitApprovalGateTests(_PaymentFixture):
    """A payment still awaiting approval must not be sendable to the bank.

    The view excluded only REJECTED, which also admitted PENDING — a payment
    sitting in the maker-checker queue. Exercised end-to-end through the real
    view, not by inspecting its source.
    """

    def _submit(self, payment):
        from unittest import mock
        from rest_framework.test import APIClient
        client = APIClient()
        # A different person releases than the one who created the payment.
        client.force_authenticate(user=self.releaser)
        with mock.patch('fnb.api_views._can_manage_fnb', return_value=True), \
                mock.patch('fnb.payments.FNBClient') as fnb:
            fnb.return_value.post.return_value = type(
                '_R', (), {'json': {'instructionId': 'R1G66R-gate'},
                           'status_code': 200})()
            resp = client.post('/api/v1/fnb/submit-batch/', {
                'payment_ids': [str(payment.pk)],
                'source_account_id': str(self.source_account.pk),
            }, format='json')
        return resp, fnb

    def test_a_pending_payment_is_refused_and_never_reaches_the_bank(self):
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.PENDING)
        resp, fnb = self._submit(p)
        self.assertEqual(resp.status_code, 400, msg=str(resp.data))
        self.assertIn('awaiting approval', str(resp.data))
        fnb.assert_not_called()
        p.refresh_from_db()
        self.assertIsNone(p.bank_submitted_at)

    def test_a_rejected_payment_is_refused(self):
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.REJECTED)
        resp, _ = self._submit(p)
        self.assertEqual(resp.status_code, 400, msg=str(resp.data))

    def test_an_approved_payment_goes_through(self):
        """Pins that the gate admits what it should — without this, nailing the
        door shut would pass the two tests above."""
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        resp, _ = self._submit(p)
        self.assertEqual(resp.status_code, 200, msg=str(resp.data))
        self.assertTrue(resp.data.get('success'))
        p.refresh_from_db()
        self.assertIsNotNone(p.bank_submitted_at)

    def test_not_required_goes_through(self):
        """The legacy pay-run path confirms under the dual-auth threshold."""
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.NOT_REQUIRED)
        resp, _ = self._submit(p)
        self.assertEqual(resp.status_code, 200, msg=str(resp.data))


class BankFacingOverrideTests(_PaymentFixture):
    """The three strings the CFO wants to control (2026-08-20).

    Until now the beneficiary name, our reference and the narration were all
    derived silently, and the derivation produced things nobody chose — the P10
    test went to the bank reading "One-off - <payee>". Blank must keep the old
    behaviour exactly; set must win; and either way the value is folded to
    FNB's character set so an operator cannot type a batch into a reject.
    """

    def _tx(self, payment):
        from fnb.payments import build_batch_payload
        from payments.models import Payment
        payload = build_batch_payload(
            Payment.objects.filter(pk=payment.pk),
            source_account=self.source_account,
            idempotency_key='ALPHA-EFT-TEST-NARR',
        )
        return payload['paymentInformation'][0]['creditTransferTransactionInformation'][0]

    def _mk(self, **over):
        """Create with the overrides already set. A CONFIRMED payment refuses
        save() ('is confirmed and cannot be modified'), so they cannot be
        attached afterwards."""
        from payments.models import Payment
        return self._payment('10', Payment.ApprovalStatus.APPROVED, **over)

    def test_blank_overrides_keep_the_previous_derivation(self):
        # The Omni-origin marker (CFO 2026-08-22) is now always appended to the
        # reference; beneficiary and narration are unchanged.
        p = self._mk()
        tx = self._tx(p)
        # endToEndId LEADS WITH THE PAYEE (CFO 2026-08-21) and carries the
        # Omni-origin marker (CFO 2026-08-22) — see PayeeLedReferenceTests below.
        self.assertTrue(tx['endToEndId'].startswith('Guard Test Supplier'),
                        tx['endToEndId'])
        self.assertTrue(tx['endToEndId'].endswith(' (O)'), tx['endToEndId'])
        self.assertEqual(tx['creditor']['name'], 'Guard Test Supplier')
        # description blank, so it falls through to reference, then the number
        self.assertTrue(tx['remittanceInformationUnstructured'])

    def test_each_override_is_used_when_set(self):
        p = self._mk(bank_beneficiary_name='ABC Traders Pty Ltd',
                     bank_our_reference='INV-4471',
                     bank_narration='Alpha Direct claim 2026-004801')
        tx = self._tx(p)
        self.assertEqual(tx['creditor']['name'], 'ABC Traders Pty Ltd')
        # Our reference carries the Omni-origin marker (CFO 2026-08-22).
        self.assertEqual(tx['endToEndId'], 'INV-4471 (O)')
        self.assertEqual(tx['remittanceInformationUnstructured'],
                         'Alpha Direct claim 2026-004801')

    def test_an_override_is_still_folded_to_the_permitted_set(self):
        """An operator pasting from Word must not be able to cause an RR10."""
        p = self._mk(bank_narration='Claim — André’s panel “final”')
        tx = self._tx(p)
        self.assertEqual(tx['remittanceInformationUnstructured'],
                         'Claim - Andre\'s panel "final"')

    def test_an_override_that_folds_away_falls_back_rather_than_going_empty(self):
        p = self._mk(bank_narration='Иванов')
        tx = self._tx(p)
        self.assertEqual(tx['remittanceInformationUnstructured'], p.payment_number)

    def test_our_reference_is_capped_at_the_iso_limit(self):
        """endToEndId is 35 characters in ISO 20022. The column is 35 too, so
        the database refuses anything longer before it can reach the bank; the
        fold caps as well, for a value arriving by any other route."""
        from fnb.payments import fnb_text
        p = self._mk(bank_our_reference='X' * 35)
        self.assertLessEqual(len(self._tx(p)['endToEndId']), 35)
        self.assertEqual(len(fnb_text('X' * 60, 35)), 35)

    def test_our_reference_carries_the_omni_origin_marker(self):
        """CFO 2026-08-22: every payment loaded through Omni is stamped so it can
        be told apart from one keyed straight into FNB Online Banking. Remove the
        marker in bank_view_of and this goes red."""
        from fnb.payments import OMNI_MARKER
        p = self._mk(bank_our_reference='INV-4471')
        self.assertEqual(self._tx(p)['endToEndId'], f'INV-4471 {OMNI_MARKER}')

    def test_the_marker_survives_even_a_max_length_reference(self):
        """The marker must ALWAYS be present AND the whole string stay within the
        35-char endToEndId — the base is truncated to make room, never the mark."""
        from fnb.payments import OMNI_MARKER
        e2e = self._tx(self._mk(bank_our_reference='X' * 35))['endToEndId']
        self.assertLessEqual(len(e2e), 35)
        self.assertTrue(e2e.endswith(OMNI_MARKER))

    def test_pop_email_defaults_to_accounts_and_rides_in_the_payload(self):
        """CFO 2026-08-22 (raised by Tlamelo): FNB emails the POP to this
        address. It defaults to Accounts and travels as the EMAL remittance
        location on every transaction."""
        from payments.models import default_pop_email
        p = self._mk()
        self.assertEqual(p.remittance_email, default_pop_email())
        tx = self._tx(p)
        self.assertEqual(tx.get('remittanceLocationMethod'), 'EMAL')
        self.assertEqual(tx.get('remittanceLocationElectronicAddress'),
                         default_pop_email())

    def test_a_typed_pop_email_overrides_the_default(self):
        p = self._mk(remittance_email='vendor.ap@example.com')
        tx = self._tx(p)
        self.assertEqual(tx.get('remittanceLocationElectronicAddress'),
                         'vendor.ap@example.com')

    def test_the_preview_matches_what_is_actually_sent(self):
        """The screen must never show something different from the payload
        (H74). Same helper, so they cannot drift."""
        from fnb.payments import bank_view_of
        p = self._mk(bank_beneficiary_name='ABC Traders',
                     bank_our_reference='INV-9',
                     bank_narration='Panel repair — job 12')
        view = bank_view_of(p)
        tx = self._tx(p)
        self.assertEqual(view['beneficiary_name'], tx['creditor']['name'])
        self.assertEqual(view['our_reference'], tx['endToEndId'])
        self.assertEqual(view['narration'],
                         tx['remittanceInformationUnstructured'])

    def test_the_preview_endpoint_needs_no_saved_payment(self):
        """The once-off form previews before the payment exists."""
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff)
        r = client.post('/api/v1/fnb/bank-view-preview/', {
            'payee_name': 'ABC Traders',
            'bank_narration': 'Claim — job 12',
        }, format='json')
        self.assertEqual(r.status_code, 200, msg=str(r.data))
        self.assertEqual(r.data['beneficiary_name'], 'ABC Traders')
        self.assertEqual(r.data['narration'], 'Claim - job 12')

    def test_the_preview_endpoint_reflects_unsaved_edits_to_a_real_payment(self):
        from rest_framework.test import APIClient
        p = self._mk(bank_narration='saved value')
        client = APIClient()
        client.force_authenticate(user=self.staff)
        r = client.post('/api/v1/fnb/bank-view-preview/', {
            'payment_id': str(p.pk),
            'bank_narration': 'what the operator is typing now',
        }, format='json')
        self.assertEqual(r.status_code, 200, msg=str(r.data))
        self.assertEqual(r.data['narration'], 'what the operator is typing now')
        p.refresh_from_db()
        self.assertEqual(p.bank_narration, 'saved value')   # preview must not save


class PreviewMatchesTheCreateTests(TestCase):
    """The preview panel says "Exactly what FNB will receive". It must be true
    in the case that motivated the whole feature: BOTH the reference and the
    narration left blank, where the once-off create quietly substitutes
    "One-off - <payee>" — the string the CFO objected to.
    """

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User
        cls.staff = User.objects.create_superuser('narrpreview', 'n@x.co', 'x')

    def _preview(self, **body):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff)
        r = client.post('/api/v1/fnb/bank-view-preview/', body, format='json')
        self.assertEqual(r.status_code, 200, msg=str(r.data))
        return r.data

    def test_both_blank_shows_the_string_the_create_will_really_use(self):
        data = self._preview(payee_name='ABC Traders', reference='',
                             bank_narration='')
        # Not the stand-in payment number, and not silence: the actual default.
        self.assertEqual(data['narration'], 'One-off - ABC Traders')
        self.assertEqual(data['beneficiary_name'], 'ABC Traders')

    def test_a_typed_reference_is_what_shows(self):
        data = self._preview(payee_name='ABC Traders', reference='INV 5512',
                             bank_narration='')
        self.assertEqual(data['narration'], 'INV 5512')

    def test_a_typed_narration_beats_the_reference(self):
        data = self._preview(payee_name='ABC Traders', reference='INV 5512',
                             bank_narration='Claim 2026-004801')
        self.assertEqual(data['narration'], 'Claim 2026-004801')

    def test_the_preview_reference_shows_the_payee_not_a_standin_number(self):
        """Before the payment is saved there is no payment number, so the old
        reference was blank. Since 2026-08-21 the reference LEADS with the payee
        name (payee_led_reference), which is accurate on a stand-in — it is what
        FNB will see. What must still NEVER appear is a stand-in payment number
        dressed up as the real one."""
        data = self._preview(payee_name='ABC Traders')
        self.assertTrue(data['our_reference'].startswith('ABC Traders'),
                        data['our_reference'])
        self.assertNotIn('PAY-OUT', data['our_reference'])

    def test_a_typed_our_reference_does_show(self):
        # It carries the Omni-origin marker, same as the real payload (CFO 2026-08-22).
        data = self._preview(payee_name='ABC Traders', bank_our_reference='INV-9')
        self.assertEqual(data['our_reference'], 'INV-9 (O)')

    def test_an_empty_form_shows_no_fake_draft_number_anywhere(self):
        """With nothing typed, the narration falls back to the draft payment
        number — it must be blanked, and the internal draft placeholder must not
        leak into any field. Red if the preview only strips the draft number from
        our_reference and leaves it in narration."""
        data = self._preview()
        self.assertEqual(data['narration'], '')
        self.assertNotIn('PAY-OUT-NEW', data['our_reference'])
        self.assertNotIn('PAY-OUT-NEW', data['narration'])


class PreviewScopingTests(_PaymentFixture):
    """A beneficiary name is exactly what entity scoping exists to keep inside
    its own company. The endpoint was IsAuthenticated-only with no clamp."""

    def test_a_scoped_user_cannot_read_another_entitys_payment(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        from core.models import Company, UserCompanyAccess

        other, _ = Company.objects.get_or_create(
            code='OTHR', defaults={'name': 'Other Co',
                                   'base_currency': self.bwp})
        outsider = User.objects.create_user('narroutsider', password='x')
        outsider.is_staff = True
        outsider.save()
        UserCompanyAccess.objects.create(user=outsider, company=other)

        p = self._payment('10', __import__('payments.models', fromlist=['Payment'])
                          .Payment.ApprovalStatus.APPROVED)

        client = APIClient()
        client.force_authenticate(user=outsider)
        r = client.post('/api/v1/fnb/bank-view-preview/',
                        {'payment_id': str(p.pk)}, format='json')
        self.assertIn(r.status_code, (403, 404), msg=str(getattr(r, 'data', '')))

    def test_an_unscoped_finance_user_can_read_it(self):
        from rest_framework.test import APIClient
        from payments.models import Payment
        p = self._payment('10', Payment.ApprovalStatus.APPROVED,
                          bank_beneficiary_name='ABC Traders')
        client = APIClient()
        client.force_authenticate(user=self.staff)
        r = client.post('/api/v1/fnb/bank-view-preview/',
                        {'payment_id': str(p.pk)}, format='json')
        self.assertEqual(r.status_code, 200, msg=str(r.data))
        self.assertEqual(r.data['beneficiary_name'], 'ABC Traders')


class SecondPersonReleaseTests(_PaymentFixture):
    """CFO 2026-08-20: whoever prepares a payment may not be the one who sends
    it to the bank. Until now one finance leader could commit a pay-run AND
    release it; the invoice approval sits upstream and signs nothing about the
    batch itself."""

    def test_the_creator_cannot_release_their_own_payment(self):
        from unittest import mock
        from django.core.exceptions import ValidationError
        from fnb.models import FNBBatchSubmission
        from fnb.payments import submit_eft_batch
        from payments.models import Payment

        p = self._payment('100', Payment.ApprovalStatus.APPROVED)   # created_by=self.staff
        before = FNBBatchSubmission.objects.count()
        with mock.patch('fnb.payments.FNBClient') as client:
            with self.assertRaises(ValidationError) as ctx:
                submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                 source_account=self.source_account,
                                 user=self.staff)
            client.assert_not_called()
        msg = str(ctx.exception)
        self.assertIn('someone else has to release', msg)
        self.assertIn(p.payment_number, msg)          # says WHICH payment
        self.assertEqual(FNBBatchSubmission.objects.count(), before)
        p.refresh_from_db()
        self.assertIsNone(p.bank_submitted_at)

    def test_a_different_person_may_release_it(self):
        """Without this, nailing the door shut would pass the test above."""
        from unittest import mock
        from django.contrib.auth.models import User
        from fnb.payments import submit_eft_batch
        from payments.models import Payment

        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        releaser = User.objects.create_superuser('releaser2', 'r2@x.co', 'x')

        class _Resp:
            json = {'instructionId': 'R1G66R-second-person'}
            status_code = 200

        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.return_value = _Resp()
            batch = submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                     source_account=self.source_account,
                                     user=releaser)
        self.assertEqual(batch.fnb_reference, 'R1G66R-second-person')

    def test_no_releasing_user_is_refused_not_skipped(self):
        """A control that waves through when it lacks information is not a
        control (/fabe: OpenAI flagged both bypasses)."""
        from django.core.exceptions import ValidationError
        from fnb.payments import _assert_release_is_a_second_person
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        with self.assertRaises(ValidationError) as ctx:
            _assert_release_is_a_second_person([p], None)
        self.assertIn('named person', str(ctx.exception))

    def test_a_payment_with_no_creator_is_refused_by_default(self):
        from django.core.exceptions import ValidationError
        from fnb.payments import _assert_release_is_a_second_person

        class _Transient:
            payment_number = 'QT-1'
            created_by_id = None

        with self.assertRaises(ValidationError) as ctx:
            _assert_release_is_a_second_person([_Transient()], self.staff)
        self.assertIn('nobody recorded', str(ctx.exception))

    def test_a_caller_may_declare_it_has_no_creator_to_compare(self):
        """Quick transfer and payroll build transient payees. The exemption is
        explicit at those call sites, so it is greppable rather than an accident
        of a missing attribute."""
        from fnb.payments import _assert_release_is_a_second_person

        class _Transient:
            payment_number = 'QT-1'
            created_by_id = None

        # must not raise
        _assert_release_is_a_second_person([_Transient()], self.staff,
                                           allow_single_person=True)

    def test_the_exemption_does_not_excuse_a_real_self_release(self):
        """allow_single_person must not become a blanket bypass: a payment that
        DOES name its creator is still checked."""
        from django.core.exceptions import ValidationError
        from fnb.payments import _assert_release_is_a_second_person
        from payments.models import Payment
        p = self._payment('100', Payment.ApprovalStatus.APPROVED)
        with self.assertRaises(ValidationError):
            _assert_release_is_a_second_person([p], self.staff,
                                               allow_single_person=True)


class SettlementStampTests(_PaymentFixture):
    """A batch showing `settled` had no settlement date: refresh_batch_status
    wrote the status and never the timestamp (CFO 2026-08-20, 'just the
    settlement date')."""

    def _batch(self, ref):
        from fnb.models import FNBBatchSubmission
        return FNBBatchSubmission.objects.create(
            idempotency_key=f'ALPHA-EFT-TEST-STAMP-{ref}',
            source_account=self.source_account, payment_count=1,
            total_amount_bwp=10, currency_code='BWP',
            status=FNBBatchSubmission.Status.SUBMITTED, fnb_reference=ref)

    def _refresh(self, batch, group_status):
        from unittest import mock
        from fnb.payments import refresh_batch_status

        class _Resp:
            json = {'groupStatus': group_status}
            status_code = 200

        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.get.return_value = _Resp()
            refresh_batch_status(batch)
        batch.refresh_from_db()
        return batch

    def test_settling_records_when(self):
        from fnb.models import FNBBatchSubmission as B
        b = self._batch('ref-stamp-acsc')
        self.assertIsNone(b.settled_at)
        b = self._refresh(b, 'ACSC')
        self.assertEqual(b.status, B.Status.SETTLED)
        self.assertIsNotNone(b.settled_at)

    def test_acknowledging_records_when(self):
        from fnb.models import FNBBatchSubmission as B
        b = self._refresh(self._batch('ref-stamp-acwc'), 'ACWC')
        self.assertEqual(b.status, B.Status.ACKNOWLEDGED)
        self.assertIsNotNone(b.acknowledged_at)

    def test_a_later_poll_does_not_move_the_settlement_date(self):
        """Otherwise the column silently means 'last polled', not 'settled'."""
        b = self._refresh(self._batch('ref-stamp-again'), 'ACSC')
        first = b.settled_at
        b = self._refresh(b, 'ACSC')
        self.assertEqual(b.settled_at, first)

    def test_a_batch_that_has_not_settled_has_no_date(self):
        b = self._refresh(self._batch('ref-stamp-rjct'), 'RJCT')
        self.assertIsNone(b.settled_at)


class NarrationEditableOnARealPaymentTests(_PaymentFixture):
    """The three fields were exposed for reading AND listed read-only, so they
    could not be set on anything but a once-off — which would have made the
    CFO's "narration typed by hand" impossible for a supplier run.

    Editing is draft-only in Omni, so the narration is typed while the payment
    is being raised. That is the right place for it: the words the bank will see
    are then part of what the approvers approve.
    """

    def _patch(self, payment, body):
        from rest_framework.test import APIClient
        client = APIClient(SERVER_NAME='testserver')
        client.force_authenticate(user=self.staff)
        return client.patch(f'/api/v1/payments/{payment.pk}/', body, format='json')

    def test_finance_can_set_the_narration_while_raising_the_payment(self):
        p = self._draft_payment('100')
        r = self._patch(p, {'bank_narration': 'Invoice 5512 - panel repair',
                            'bank_beneficiary_name': 'ABC Traders (Pty) Ltd',
                            'bank_our_reference': 'INV-5512'})
        self.assertEqual(r.status_code, 200, msg=str(r.data))
        p.refresh_from_db()
        self.assertEqual(p.bank_narration, 'Invoice 5512 - panel repair')
        self.assertEqual(p.bank_beneficiary_name, 'ABC Traders (Pty) Ltd')
        self.assertEqual(p.bank_our_reference, 'INV-5512')

    def test_the_payee_bank_details_stay_read_only(self):
        """Those CAN redirect money, so they must remain unsettable here even
        on a draft."""
        p = self._draft_payment('100')
        r = self._patch(p, {'payee_account_number': '9999999'})
        self.assertEqual(r.status_code, 200, msg=str(r.data))
        p.refresh_from_db()
        self.assertEqual(p.payee_account_number, '1234567')   # unchanged

    def test_it_cannot_be_rewritten_once_the_bank_has_it(self):
        """DRF runs validate() before update(), so this guard fires BEFORE the
        general draft-only rule and is live code on the real HTTP path — not
        defence in depth behind it. It carries the specific message too: not
        "drafts only" but "the bank already has this wording".
        """
        from django.utils import timezone
        from payments.models import Payment
        from payments.serializers import PaymentDetailSerializer
        p = self._draft_payment('100', bank_narration='what was actually sent')
        Payment.objects.filter(pk=p.pk).update(bank_submitted_at=timezone.now())
        p.refresh_from_db()
        ser = PaymentDetailSerializer(
            instance=p, data={'bank_narration': 'a nicer story afterwards'},
            partial=True)
        self.assertFalse(ser.is_valid())
        self.assertIn('already been sent', str(ser.errors))

    def test_a_draft_that_has_not_been_sent_is_still_editable(self):
        """So the guard above cannot be nailed permanently shut."""
        from payments.serializers import PaymentDetailSerializer
        p = self._draft_payment('100')
        ser = PaymentDetailSerializer(
            instance=p, data={'bank_narration': 'fine to set'}, partial=True)
        self.assertTrue(ser.is_valid(), msg=str(ser.errors))


class BatchCeilingValueTests(TestCase):
    def test_the_default_is_the_cfos_two_million(self):
        from decimal import Decimal
        from fnb.payments import FNB_BATCH_MAX_BWP_DEFAULT
        self.assertEqual(FNB_BATCH_MAX_BWP_DEFAULT, Decimal('2000000'))


class PayeeLedReferenceTests(_PaymentFixture):
    """The FNB authorisation screen must say WHO is being paid.

    CFO 2026-08-21: a payment to Grand RE - Radical Investments (P30,459.11) sat
    in FNB Online Banking pending authorisation and the Own Reference column
    read `PAY-OUT-2026-000044`. He could not tell who it was for without opening
    each payment, which is slow and risks authorising the wrong one.

    So `our_reference` (which is the transaction's endToEndId) now leads with the
    payee name. It still carries the payment number's tail, because endToEndId is
    how a pain.002 reject maps back to ONE payment — the lesson from the RR10
    batch. A pure name would make two payments to the same vendor
    indistinguishable in a status report.
    """

    def _tx(self, payment):
        from fnb.payments import build_batch_payload
        from payments.models import Payment
        payload = build_batch_payload(
            Payment.objects.filter(pk=payment.pk),
            source_account=self.source_account,
            idempotency_key='ALPHA-EFT-TEST-REF',
        )
        return payload['paymentInformation'][0]['creditTransferTransactionInformation'][0]

    def _mk(self, **over):
        from payments.models import Payment
        return self._payment('10', Payment.ApprovalStatus.APPROVED, **over)

    def test_the_reference_leads_with_the_payee_name(self):
        p = self._mk()
        ref = self._tx(p)['endToEndId']
        self.assertTrue(ref.startswith('Guard Test Supplier'), ref)
        self.assertNotEqual(ref, p.payment_number)

    def test_the_reference_still_identifies_the_one_payment(self):
        """Two payments to the SAME payee must not collide — endToEndId is how
        a reject is traced back to a single payment."""
        a, b = self._mk(), self._mk()
        ra = self._tx(a)['endToEndId']
        rb = self._tx(b)['endToEndId']
        self.assertNotEqual(a.payment_number, b.payment_number)
        self.assertNotEqual(ra, rb, f'both payments got {ra!r}')

    def test_the_reference_fits_the_iso_limit(self):
        # bank_beneficiary_name is what creditor_name_for reads first for a
        # once-off; _payment already hard-sets payee_name, so drive the long
        # name through the override.
        p = self._mk(bank_beneficiary_name='X' * 80)
        self.assertLessEqual(len(self._tx(p)['endToEndId']), 35)

    def test_an_explicit_override_still_wins(self):
        # The typed override wins over the payee-led default; the Omni marker is
        # still appended (CFO 2026-08-22).
        p = self._mk(bank_our_reference='INV-4471')
        self.assertEqual(self._tx(p)['endToEndId'], 'INV-4471 (O)')

    def test_a_name_outside_the_charset_falls_back_to_the_number(self):
        """A non-Latin payee folds to nothing; a mandatory field must never go
        out empty, so the number keeps it traceable."""
        p = self._mk(bank_beneficiary_name='\u0418\u0432\u0430\u043d\u043e\u0432')
        ref = self._tx(p)['endToEndId']
        self.assertTrue(ref)
        raw = p.payment_number.rsplit('-', 1)[-1]
        self.assertIn(raw.lstrip("0") or "0", ref)

    def test_the_preview_screen_shows_the_same_reference(self):
        """H74 — the screen and the payload come from one helper."""
        from fnb.payments import bank_view_of
        p = self._mk()
        self.assertEqual(bank_view_of(p)['our_reference'],
                         self._tx(p)['endToEndId'])

    def test_same_sequence_different_year_stay_distinct(self):
        """The sequence resets each year, so the tail must carry the year or
        two January payments to one payee collide (Fable, 2026-08-21)."""
        from payments.models import Payment
        a, b = self._mk(), self._mk()
        Payment.objects.filter(pk=a.pk).update(payment_number='PAY-OUT-2025-000044')
        Payment.objects.filter(pk=b.pk).update(payment_number='PAY-OUT-2026-000044')
        a.refresh_from_db(); b.refresh_from_db()
        self.assertNotEqual(self._tx(a)['endToEndId'], self._tx(b)['endToEndId'])

    def test_the_batch_key_is_the_readable_omni_reference(self):
        """The batch messageId (paymentInformationId / FNB idempotency key) now
        reads as the Omni payment reference, not an opaque uuid.

        CFO directive 2026-08-24 (given three times, naming the exact format:
        payee + reference sequence + `(O)`) CONSCIOUSLY supersedes the 22-Aug
        engineering decline that kept this an opaque uuid. It is money-safe, and
        the old decline's rationale no longer holds:
          (a) retries reuse the STORED batch.idempotency_key, not a fresh call —
              so per-batch FNB dedupe on a retry is unchanged;
          (b) the PENDING row persists before the POST (two-phase, payments.py
              "Phase 1"), so a resubmit of the same set always sees the prior row
              and mints a fresh `-N` key — the 16-Jul duplicate-reject door stays
              closed;
          (c) a concurrent duplicate dies on the `unique=True` constraint BEFORE
              the POST — closing a double-pay window the uuid scheme left open
              (two distinct uuids = two debits).
        """
        from fnb.payments import _next_idempotency_key, OMNI_MARKER
        from fnb.models import FNBBatchSubmission
        p = self._mk()
        raw = p.payment_number.rsplit('-', 1)[-1]
        seq = raw.lstrip("0") or "0"
        key = _next_idempotency_key([p])
        # Fails on the old ALPHA-EFT-<uuid> scheme (payee-led, ends in marker).
        self.assertEqual(key, f'Guard Test Supplier {seq} {OMNI_MARKER}')
        self.assertLessEqual(len(key), 35)
        # Resubmit of the SAME payment set must NOT reuse the key once a row
        # holds it — a duplicate messageId is what FNB rejected on 16-Jul-2026.
        FNBBatchSubmission.objects.create(
            idempotency_key=key, source_account=self.source_account,
            payment_count=1, total_amount_bwp=p.amount, currency_code='BWP',
            status=FNBBatchSubmission.Status.PENDING, submitted_by=self.staff)
        self.assertNotEqual(_next_idempotency_key([p]), key)

    def test_the_batch_key_shape_for_many_and_none(self):
        from fnb.payments import _next_idempotency_key, OMNI_MARKER, FNB_ALLOWED_CHARS
        ps = [self._mk() for _ in range(3)]
        raw = ps[0].payment_number.rsplit('-', 1)[-1]
        seq = raw.lstrip("0") or "0"
        key = _next_idempotency_key(ps)
        self.assertIn('EFT 3 payments', key)
        self.assertIn(seq, key)
        self.assertLessEqual(len(key), 35)
        self.assertTrue(set(key) <= FNB_ALLOWED_CHARS)
        # no payments at all still yields a safe, marked key
        self.assertEqual(_next_idempotency_key([]), f'EFT batch {OMNI_MARKER}')

    def test_the_batch_key_stays_within_the_iso_limit_for_a_long_payee(self):
        from fnb.payments import _next_idempotency_key
        p = self._mk()
        p.payee_name = 'X' * 80    # creditor_name_for reads payee_name for a once-off
        self.assertLessEqual(len(_next_idempotency_key([p])), 35)


class BrokerCommissionBatchReferenceTests(_PaymentFixture):
    """The FNB list must name the BROKER, not two letters of a label.

    CFO amendment 2026-09-08: broker commissions were reaching FNB as
    `BROKER COMMISSION AS 000124 (O)`, `BROKER COMMISSION AU 000114 (O)` and
    `BROKER COMMISION DYN 000119 (O)`. None of `AS`/`AU`/`DYN` is a broker code
    — each of those three prefixes is exactly 20 characters, the budget
    `_batch_reference` gave the name, so all that survived of the broker was the
    first letters after the words "BROKER COMMISSION". (The misspelt
    "COMMISION" is one character shorter, which is the only reason that row kept
    a third letter — the spelling lives in the free text, not in our code.)

    WHERE THE NAME LIVES, checked against production on 2026-09-08: in the free
    text `bank_narration`, beside the month — 'BROKER COMMISSION SPECTRUM AUG26'.
    Every one of these payments is booked against the shared 'Ad-hoc / One-off
    Payee' contact, and `contact_type` on production only holds 'vendor' or
    'customer' — there is no 'broker' value. So the label in the narration is
    the discriminator; keying off the contact record would have shipped a fix
    that never fired. The narrations below are the real production strings.
    """

    #: Verbatim from production, 2026-09-08 (payments PAY/ADIC/2026/09/08/*).
    REAL_NARRATIONS = (
        ('BROKER COMMISSION SPECTRUM AUG26',       'Spectrum'.upper()),
        ('BROKER COMMISSION ASSURE WEALTH AUG26',  'ASSURE WEALTH'),
        ('BROKER COMMISSION LETSEMA AUG26',        'LETSEMA'),
        ('BROKER COMMISSION BOC AUG26',            'BOC'),
        ('BROKER COMMISSION CIB AUG26',            'CIB'),
        ('BROKER COMMISSION RADICAL AUG26',        'RADICAL'),
        ('BROKER COMMISION DYNAMIC AUG26',         'DYNAMIC'),
        # This one puts the month BEFORE the name.
        ('BROKER COMMISSION AUG26 FINSEF',         'FINSEF'),
    )

    def _broker_payment(self, narration='BROKER COMMISSION SPECTRUM AUG26',
                        seq='000124'):
        """A CONFIRMED broker payment carrying a real production narration.

        Set with .update() rather than save(): Payment.save() recomputes WHT and
        the journal, which this reference has nothing to do with. `payee_name`
        stays 'Guard Test Supplier' on purpose — the broker's name must come out
        of the NARRATION, which is the only place production keeps it.
        """
        from payments.models import Payment
        p = self._payment('10', Payment.ApprovalStatus.APPROVED)
        Payment.objects.filter(pk=p.pk).update(
            bank_narration=narration, payment_number=f'PAY-OUT-2026-{seq}')
        p.refresh_from_db()
        return p

    def test_the_broker_is_spelled_out_on_the_fnb_list(self):
        """The exact shape the CFO asked for. Fails on the old scheme, which
        emitted the 20-char free-text stub instead of the broker's name."""
        from fnb.payments import _next_idempotency_key
        p = self._broker_payment()
        self.assertEqual(_next_idempotency_key([p]),
                         'BKR COMM SPECTRUM 124 (O)')

    def test_every_real_production_narration_names_its_broker(self):
        """The eight live rows, including the misspelling and the one whose
        month comes before the name. Each must show the broker and no month."""
        from fnb.payments import _next_idempotency_key
        for i, (narration, expected) in enumerate(self.REAL_NARRATIONS):
            p = self._broker_payment(narration=narration, seq=f'0003{i:02d}')
            key = _next_idempotency_key([p])
            self.assertTrue(key.startswith(f'BKR COMM {expected} '),
                            f'{narration!r} -> {key!r}')
            self.assertNotIn('AUG', key.replace('BKR COMM ', ''),
                             f'month survived: {narration!r} -> {key!r}')
            self.assertTrue(key.endswith(f'3{i:02d} (O)'), key)
            self.assertLessEqual(len(key), 35, key)

    def test_the_long_label_and_its_misspelling_are_gone(self):
        """`BKR COMM` replaces the long label on this path, so neither spelling
        of "COMMISSION" can reach FNB from it again."""
        from fnb.payments import _next_idempotency_key
        for narration, _ in self.REAL_NARRATIONS[:2] + (
                ('BROKER COMMISION DYNAMIC AUG26', ''),):
            key = _next_idempotency_key([self._broker_payment(
                narration=narration, seq=str(4000 + len(narration)))])
            self.assertNotIn('COMMISSION', key)
            self.assertNotIn('COMMISION', key)
            self.assertTrue(key.startswith('BKR COMM '), key)

    def test_a_long_broker_name_is_cut_but_the_number_and_marker_survive(self):
        """Only the NAME may lose characters — the number and `(O)` are what the
        FNB email auto-reconcile matches on."""
        from fnb.payments import _next_idempotency_key
        p = self._broker_payment(
            narration='BROKER COMMISSION BOTSHABELO INSURANCE BROKERS AND '
                      'ADVISORS AUG26',
            seq='000114')
        key = _next_idempotency_key([p])
        self.assertEqual(len(key), 35, key)          # uses the budget, never over
        self.assertTrue(key.startswith('BKR COMM '), key)
        self.assertTrue(key.endswith(' 114 (O)'), key)

    def test_an_awkward_name_folds_and_never_ends_on_punctuation(self):
        """Extra whitespace and characters FNB rejects are folded away, and a
        cut must not leave a dangling separator that reads as a typo."""
        from fnb.payments import _next_idempotency_key, FNB_ALLOWED_CHARS
        p = self._broker_payment(
            narration='BROKER COMMISSION   Dynamix   Risk — Co.  AUG26',
            seq='000119')
        key = _next_idempotency_key([p])
        # The em dash folds to '-' (RR10 guard) and the runs of spaces collapse.
        self.assertTrue(key.startswith('BKR COMM Dynamix Risk'), key)
        self.assertTrue(key.endswith(' 119 (O)'), key)
        self.assertTrue(set(key) <= FNB_ALLOWED_CHARS, key)

    def test_the_thirty_five_char_cap_holds_for_any_broker_name(self):
        """Regression on the hard FNB limit — the cap is never exceeded."""
        from fnb.payments import _next_idempotency_key
        for i, name in enumerate(
                # 100 X's, not more: bank_narration itself is capped at 140.
                ('X' * 100, 'SPECTRUM', 'A B C D E F G H I J K L M N O P',
                 'BOTSHABELO INSURANCE BROKERS AND ADVISORS PTY LTD')):
            p = self._broker_payment(
                narration=f'BROKER COMMISSION {name} AUG26', seq=f'00020{i}')
            key = _next_idempotency_key([p])
            self.assertLessEqual(len(key), 35, f'{name} -> {key}')
            self.assertTrue(key.endswith('(O)'), key)

    def test_a_non_broker_payee_is_untouched(self):
        """Suppliers, claims and refunds keep the payee-led scheme — this
        amendment is scoped to broker commissions."""
        from fnb.payments import _next_idempotency_key
        from payments.models import Payment
        p = self._payment('10', Payment.ApprovalStatus.APPROVED)
        raw = p.payment_number.rsplit('-', 1)[-1]
        seq = raw.lstrip("0") or "0"
        self.assertEqual(_next_idempotency_key([p]),
                         f'Guard Test Supplier {seq} (O)')

    def test_a_narration_that_merely_mentions_a_broker_is_not_rewritten(self):
        """The label must be the START of the narration. A supplier invoice that
        happens to mention the word keeps its own payee-led reference."""
        from fnb.payments import _next_idempotency_key
        p = self._broker_payment(
            narration='REIMBURSEMENT RE BROKER COMMISSION QUERY', seq='000555')
        key = _next_idempotency_key([p])
        self.assertFalse(key.startswith('BKR COMM'), key)
        self.assertTrue(key.startswith('Guard Test Supplier'), key)


class IndeterminateSubmitKeepsItsLineageTests(_PaymentFixture):
    """A submit that never comes back must still remember whose payment it was.

    `fnb_autoload` stamps `payment_request` on its batches only AFTER every
    one of them returns, and `PaymentRequest.fnb_batch` is written only on the
    success path. So on an indeterminate submit — timeout or 5xx, the one
    outcome where the money MAY have moved — BOTH links stayed NULL.

    That is not a bookkeeping detail. The bank-balances screen counts an
    UNKNOWN batch as money on its way out, and excludes its request from the
    Omni bucket so the same payment is not subtracted twice. With neither link
    written, the request was counted in both, which is the double count that
    overstated "going out" by P486,835.61 on 20-Sep-2026.

    `submit_eft_batch` now takes `payment_request` and writes it in phase 1,
    at creation, before the POST — so the link survives any outcome.
    """

    def test_a_timeout_leaves_the_batch_pointing_at_its_request(self):
        from unittest import mock
        from decimal import Decimal
        from fnb.client import FNBAPIError
        from fnb.models import FNBBatchSubmission
        from fnb.payments import submit_eft_batch
        from payments.models import Payment
        from taskboard.models import PaymentRequest

        pr = PaymentRequest.objects.create(
            ref='PAY/LINEAGE/0001', total=Decimal('250'), status='pending_cfo',
            subject='t', payee='Test Payee', currency='BWP', category='claim',
            created_by=self.releaser,
        )
        p = self._payment('250', Payment.ApprovalStatus.APPROVED)

        with mock.patch('fnb.payments.FNBClient') as client:
            # 504 — `_indeterminate` treats >=500 as "the money MAY have
            # moved", which is the branch that flips the batch to UNKNOWN.
            client.return_value.post.side_effect = FNBAPIError(504, 'gateway timeout')
            with self.assertRaises(FNBAPIError):
                submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                 source_account=self.source_account,
                                 user=self.releaser, payment_request=pr)

        batch = FNBBatchSubmission.objects.get(payments=p)
        self.assertEqual(batch.status, FNBBatchSubmission.Status.UNKNOWN)
        pr.refresh_from_db()
        self.assertIsNone(pr.fnb_batch_id)                 # forward link: never written
        self.assertEqual(batch.payment_request_id, pr.id)  # reverse link: survived

    def test_a_clean_submit_also_carries_it(self):
        """The other direction — otherwise the stamp could be wired to fire
        only on the failure path and this suite would still be green."""
        from decimal import Decimal
        from unittest import mock
        from fnb.models import FNBBatchSubmission
        from fnb.payments import submit_eft_batch
        from payments.models import Payment
        from taskboard.models import PaymentRequest

        pr = PaymentRequest.objects.create(
            ref='PAY/LINEAGE/0002', total=Decimal('250'), status='pending_cfo',
            subject='t', payee='Test Payee', currency='BWP', category='claim',
            created_by=self.releaser,
        )
        p = self._payment('250', Payment.ApprovalStatus.APPROVED)

        class _Resp:
            json = {'instructionId': 'R1G66R-lineage-ok'}
            status_code = 200

        with mock.patch('fnb.payments.FNBClient') as client:
            client.return_value.post.return_value = _Resp()
            batch = submit_eft_batch(Payment.objects.filter(pk=p.pk),
                                     source_account=self.source_account,
                                     user=self.releaser, payment_request=pr)

        self.assertEqual(batch.payment_request_id, pr.id)
