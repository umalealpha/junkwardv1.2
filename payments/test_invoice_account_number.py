"""payments/test_invoice_account_number.py — reading the account number without the model.

Proven on prod 2026-09-02, twice, on two differently-worded invoices: the
reasoning model returns `"account_number": null` while correctly returning the
branch code from the very next line. The digits ARE in the text sent to it — the
model declines to echo a bank account number.

That field is also the one this repo already decided must NOT depend on a model
(taskboard/payee_bank_history.py: "the field that decides who receives the
money — the one place in Omni where a plausible invention is most expensive").
So it is read deterministically from the text we already hold.

The rules that must never regress — all of them are "be certain or say nothing",
because a WRONG account number is far worse than an empty box:
  - a labelled account number is read;
  - a branch / sort / swift code is NEVER read as the account;
  - a bare unlabelled number is NEVER read;
  - two different labelled candidates return nothing, not a guess;
  - it only ever ADDS what the model left empty, never overrides it.

Run: manage.py test payments.test_invoice_account_number
"""
from unittest import mock

from django.test import SimpleTestCase

import payments.invoice_read as invoice_read
from payments.invoice_read import _account_number_from_text, read_invoice

REAL = """KAGISO SUPPLIES (PTY) LTD
TAX INVOICE
Invoice No: KS-9931
TOTAL DUE: BWP 3,200.00
BANKING DETAILS
Bank Name: Absa Bank Botswana
Bank Account Number: 4055512345
Branch Code: 290167
"""


class AccountNumberFromTextTests(SimpleTestCase):
    def test_reads_a_labelled_account_number(self):
        self.assertEqual(_account_number_from_text(REAL), '4055512345')

    def test_the_branch_code_is_never_read_as_the_account(self):
        """The two sit on adjacent lines and are the classic swap."""
        self.assertNotEqual(_account_number_from_text(REAL), '290167')

    def test_a_branch_only_invoice_yields_nothing(self):
        self.assertIsNone(_account_number_from_text(
            'Bank: FNB\nBranch Code: 281267\nSwift: FIRNBWGX\n'))

    def test_common_label_spellings(self):
        for label in ('Account Number:', 'Account No:', 'A/C No.',
                      'Acct #', 'BANK ACCOUNT NUMBER'):
            with self.subTest(label=label):
                self.assertEqual(
                    _account_number_from_text(f'{label} 62445566778'),
                    '62445566778')

    def test_spaces_and_dashes_are_normalised(self):
        self.assertEqual(
            _account_number_from_text('Account Number: 6244-5566-778'),
            '62445566778')

    # ── the refusals ────────────────────────────────────────────────────────
    def test_a_bare_unlabelled_number_is_ignored(self):
        """An invoice is full of numbers. Only a labelled one is an account."""
        self.assertIsNone(_account_number_from_text(
            'INVOICE\n62445566778\nTotal 3200.00\n'))

    def test_two_different_candidates_return_nothing(self):
        """Better an empty box the operator fills than a confident wrong one."""
        self.assertIsNone(_account_number_from_text(
            'Account Number: 4055512345\nAccount Number: 9988776655\n'))

    def test_the_same_number_repeated_is_still_read(self):
        self.assertEqual(_account_number_from_text(
            'Account Number: 4055512345\nAcct No: 4055512345\n'), '4055512345')

    def test_too_short_is_not_an_account_number(self):
        self.assertIsNone(_account_number_from_text('Account No: 1234'))

    def test_empty_and_none_are_safe(self):
        self.assertIsNone(_account_number_from_text(''))
        self.assertIsNone(_account_number_from_text(None))

    # ── the wrong-but-confident breaks Fable proved (2026-09-02) ─────────────
    # Each of these returned a confident WRONG number before the fix. A wrong
    # account number is the one outcome this whole helper exists to prevent, so
    # the required behaviour is None (an empty box the operator fills), never a
    # guess.
    def test_a_facultative_reference_is_not_an_account(self):
        """'FAC No. 20250012' at an insurer that pays FAC invoices — 'ac' inside
        FAC must not fire the label."""
        self.assertIsNone(_account_number_from_text('FAC No. 20250012'))

    def test_ac_inside_a_word_is_not_an_account(self):
        for s in ('AC 8945612 COMPRESSOR UNIT', 'HVAC 20250901',
                  'Contact 71234567 for queries'):
            with self.subTest(s=s):
                self.assertIsNone(_account_number_from_text(s))

    def test_a_standalone_ac_needs_a_suffix(self):
        """'AC 8945612' (a part number) must NOT read; 'A/C No 8945612' may."""
        self.assertIsNone(_account_number_from_text('AC 8945612'))
        self.assertEqual(_account_number_from_text('A/C No 8945612'), '8945612')

    def test_a_customer_account_after_a_branch_word_is_not_taken(self):
        """A utility bill: the customer ref is labelled 'Account No', the real
        bank line is a branch/swift line. The bank line's number must not be
        read (branch word before it), leaving only the customer ref — which,
        being the sole survivor, WOULD be wrong, so the presence of the branch
        line must knock the whole read out. Required result: None."""
        util = ('BOTSWANA POWER CORPORATION\n'
                'Customer Account No: 100234567\n'
                'Pay at any bank to: BPC\n'
                'Branch Account Number 62998877665 Swift FIRNBWGX\n')
        self.assertIsNone(_account_number_from_text(util))

    def test_a_space_thousands_amount_is_not_an_account(self):
        """BWP amounts are written '123 456.78'. A label nearby must not turn
        the amount into an account."""
        self.assertIsNone(_account_number_from_text('Account: 123 456.78'))
        self.assertIsNone(_account_number_from_text('A/C - 1 234 567.89'))

    def test_two_numbers_jammed_together_are_dropped(self):
        """Account + an unlabelled branch in the next column read as one run."""
        self.assertIsNone(
            _account_number_from_text('Account No: 62123456 282867'))

    def test_the_normal_single_line_footer_still_reads(self):
        """The commonest real layout — bank, account and branch on one line —
        must still yield the account (regression against over-aggressive skip)."""
        self.assertEqual(
            _account_number_from_text(
                'Bank: FNB  Account No: 62123456789  Branch: 282867'),
            '62123456789')


class ReadInvoiceFillsAccountNumberTests(SimpleTestCase):
    """The whole read_invoice() path, with the model stubbed to do exactly what
    it does on prod — return account_number=null. This is the test that goes red
    if the deterministic fallback is ever unwired (the unit tests above would
    not notice that; they call the helper directly)."""

    MODEL_REPLY = {
        'total_amount': '3200.00', 'account_number': None,
        'bank_name': 'Absa Bank Botswana', 'branch_code': '290167',
        'invoice_number': 'KS-9931', 'payee_name': 'KAGISO SUPPLIES (PTY) LTD',
    }

    def test_account_number_is_filled_from_text_when_the_model_omits_it(self):
        with mock.patch('core.doc_parse.parse',
                               return_value=mock.Mock(text=REAL)), \
             mock.patch.object(invoice_read, '_extract_from_text',
                               return_value=dict(self.MODEL_REPLY)):
            out = read_invoice(b'ignored', mime='application/pdf', filename='i.pdf')
        self.assertTrue(out['ok'])
        self.assertEqual(out['fields']['account_number'], '4055512345')
        # and it must not have disturbed what the model DID read
        self.assertEqual(out['fields']['branch_code'], '290167')

    def test_a_placeholder_echo_does_not_blank_the_account(self):
        """With redaction on, the model sees [NUMBER-REDACTED] and may echo it.
        That must not survive as the account — the local read wins."""
        reply = dict(self.MODEL_REPLY, account_number='[NUMBER-REDACTED]')
        with mock.patch('core.doc_parse.parse',
                        return_value=mock.Mock(text=REAL)), \
             mock.patch.object(invoice_read, '_extract_from_text',
                               return_value=reply):
            out = read_invoice(b'ignored', mime='application/pdf', filename='i.pdf')
        self.assertEqual(out['fields']['account_number'], '4055512345')

    def test_a_real_model_account_survives_when_the_local_read_finds_none(self):
        """Defensive branch: text the local reader can't parse a label out of,
        but the model returned real digits (e.g. a rare short account it saw
        because is_safe_for_ai only redacts 9+ digits). Keep the model value —
        don't blank a real answer."""
        no_label = 'ACME LTD\nPay us\nTotal 3200.00\n'   # no account label
        reply = dict(self.MODEL_REPLY, account_number='80012345')
        with mock.patch('core.doc_parse.parse',
                        return_value=mock.Mock(text=no_label)), \
             mock.patch.object(invoice_read, '_extract_from_text',
                               return_value=reply):
            out = read_invoice(b'ignored', mime='application/pdf', filename='i.pdf')
        self.assertEqual(out['fields']['account_number'], '80012345')


class PiiFirewallTests(SimpleTestCase):
    """CFO 2026-09-02: the account number must never reach an external engine in
    the clear. read_invoice redacts with the REAL is_safe_for_ai() (not a stub)
    before sending, so these tests break if the redaction is ever removed."""

    REASONING = ('{"total_amount":"3200.00","account_number":null,'
                 '"bank_name":"Absa Bank Botswana","branch_code":"290167",'
                 '"invoice_number":"KS-9931","payee_name":"KAGISO SUPPLIES"}')

    def test_the_account_number_is_redacted_before_it_reaches_the_model(self):
        seen = {}

        def _spy(prompt, **kwargs):
            seen['prompt'] = prompt
            return self.REASONING

        with mock.patch('core.doc_parse.parse',
                        return_value=mock.Mock(text=REAL)), \
             mock.patch('core.ai_assist.reasoning_complete', side_effect=_spy):
            read_invoice(b'x', mime='application/pdf', filename='i.pdf')

        # The real account digits must NOT appear in what was sent to the model…
        self.assertNotIn('4055512345', seen['prompt'])
        # …and the redaction marker must be there in their place.
        self.assertIn('[NUMBER-REDACTED]', seen['prompt'])

    def test_read_invoice_still_fills_the_account_despite_redaction(self):
        """End to end: the model only ever sees redacted text, yet the account
        comes back filled — because it is read from the original local text."""
        with mock.patch('core.doc_parse.parse',
                        return_value=mock.Mock(text=REAL)), \
             mock.patch('core.ai_assist.reasoning_complete',
                        return_value=self.REASONING):
            out = read_invoice(b'x', mime='application/pdf', filename='i.pdf')
        self.assertTrue(out['ok'])
        self.assertEqual(out['fields']['account_number'], '4055512345')
        self.assertEqual(out['fields']['branch_code'], '290167')

    def test_a_firewall_refused_document_with_a_readable_account_stays_local(self):
        """A number-dense doc the firewall REFUSES, but with a labelled account:
        the account is read locally and neither engine is called."""
        dense = ('STATEMENT\n' + '\n'.join(f'9988776600{i} 1122334400{i}'
                                            for i in range(12))
                 + '\nBank Account Number: 4055512345\n')
        from core.ai_assist import is_safe_for_ai
        self.assertFalse(is_safe_for_ai(dense).safe)   # precondition

        with mock.patch('core.doc_parse.parse',
                        return_value=mock.Mock(text=dense)), \
             mock.patch('core.ai_assist.reasoning_complete') as reasoning, \
             mock.patch.object(invoice_read, '_extract_from_image') as vision:
            out = read_invoice(b'x', mime='application/pdf', filename='i.pdf')

        reasoning.assert_not_called()
        vision.assert_not_called()
        self.assertEqual(out['fields']['account_number'], '4055512345')

    def test_a_refused_document_is_never_sent_to_vision_even_with_no_account(self):
        """THE leak Fable proved: a firewall-refused doc whose local read finds
        NO account must STILL never reach the vision model — its text was blocked
        for a reason, so its pixels must not leak either. This is the test that
        goes red if the `not text_refused` guard on the vision tier is removed."""
        # Number-dense (firewall refuses) AND no labelled account (local read
        # returns None) → without the guard, read_invoice would ship the image.
        dense_no_acct = ('STATEMENT\n' + '\n'.join(f'9988776600{i} 1122334400{i}'
                                                    for i in range(12)) + '\n')
        from core.ai_assist import is_safe_for_ai
        self.assertFalse(is_safe_for_ai(dense_no_acct).safe)   # precondition
        self.assertIsNone(_account_number_from_text(dense_no_acct))  # precondition

        # An IMAGE file, so the vision path is genuinely reachable (image_bytes =
        # the file itself). The ONLY thing that can stop the vision call here is
        # the text_refused guard — so removing the guard makes this test go red.
        with mock.patch('core.doc_parse.parse',
                        return_value=mock.Mock(text=dense_no_acct)), \
             mock.patch('core.ai_assist.reasoning_complete') as reasoning, \
             mock.patch.object(invoice_read, '_extract_from_image',
                               return_value={'account_number': '9999999999'}) as vision:
            out = read_invoice(b'\x89PNG-fake-image-bytes', mime='image/png',
                               filename='statement.png')

        reasoning.assert_not_called()
        vision.assert_not_called()             # the image must NOT leak
        self.assertFalse(out['ok'])            # nothing read — operator types it

    def test_a_redaction_placeholder_never_prefills_a_text_field(self):
        """Fable 2026-09-02: the model may echo [NUMBER-REDACTED] where a long
        invoice ref was redacted. That literal tag must never land in a form
        box — _clean drops it."""
        reply = ('{"total_amount":"3200.00","account_number":null,'
                 '"bank_name":"Absa Bank Botswana","branch_code":"290167",'
                 '"invoice_number":"[NUMBER-REDACTED]","payee_name":"KAGISO SUPPLIES"}')
        with mock.patch('core.doc_parse.parse',
                        return_value=mock.Mock(text=REAL)), \
             mock.patch('core.ai_assist.reasoning_complete', return_value=reply):
            out = read_invoice(b'x', mime='application/pdf', filename='i.pdf')
        self.assertIsNone(out['fields']['invoice_number'])
