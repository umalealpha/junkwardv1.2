"""Unit tests for the FNB email auto-reconcile parser + matcher.

Pure functions, no DB — safe to run anywhere (incl. Windows sqlite).
"""
import unittest
from decimal import Decimal

from fnb.email_reconcile import parse_result_email, match_paid_email

# A real FNB result email body (verified 2026-08-23), minus the CAUTION banner.
UNICOIN_BODY = (
    "FNB:-) The OnceOff Payment Unicoin-AD Transportation2108 to the total value "
    "of BWP2800.00 has been processed and is now in a status of Fully Processed. "
    "Support no. 2934371"
)
KOREAN_BODY = (
    "FNB:-) The OnceOff Payment G2026004624 KOREAN AUTO to the total value of "
    "BWP12004.34 has been processed and is now in a status of Fully Processed."
)
# Same wording FNB uses for a not-yet-paid state (Authorised is NOT paid).
AUTHORISED_BODY = (
    "FNB:-) The OnceOff Payment Some Payee 999 to the total value of BWP500.00 "
    "has been processed and is now in a status of Authorised."
)


class ParseTests(unittest.TestCase):
    def test_fully_processed_is_paid(self):
        p = parse_result_email(UNICOIN_BODY)
        self.assertIsNotNone(p)
        self.assertTrue(p['paid'])
        self.assertEqual(p['amount'], Decimal('2800.00'))
        self.assertIn('Unicoin', p['ref'])

    def test_amount_with_thousands_separator(self):
        p = parse_result_email(KOREAN_BODY)
        self.assertEqual(p['amount'], Decimal('12004.34'))
        self.assertTrue(p['paid'])

    def test_authorised_is_not_paid(self):
        p = parse_result_email(AUTHORISED_BODY)
        self.assertIsNotNone(p)
        self.assertFalse(p['paid'])            # Authorised != paid

    def test_unrelated_email_is_none(self):
        self.assertIsNone(parse_result_email("Your statement is ready to view."))
        self.assertIsNone(parse_result_email(""))


def _req(**kw):
    base = {'id': 1, 'ref': 'PAY/ADIC/2026/08/22/0001', 'total': '2800.00',
            'bank_our_reference': 'Unicoin-AD Transportation2108',
            'bank_narration': '', 'payee': 'Unicoin', 'subject': 'Unicoin invoice',
            'line_items': []}
    base.update(kw)
    return base


class MatchTests(unittest.TestCase):
    def test_exact_amount_and_reference_closes(self):
        parsed = parse_result_email(UNICOIN_BODY)
        d = match_paid_email(parsed, [_req()])
        self.assertEqual(d['action'], 'close')
        self.assertEqual(d['request_ref'], 'PAY/ADIC/2026/08/22/0001')

    def test_amount_only_never_closes(self):
        # SAME amount, but the reference names a totally different payee → no close.
        # This is the money-safety guard: amount alone must never be enough.
        parsed = parse_result_email(UNICOIN_BODY)
        other = _req(id=2, ref='PAY/ADIC/2026/08/22/0002',
                     bank_our_reference='Grand RE Radical Investments',
                     payee='Grand RE', subject='Grand RE')
        d = match_paid_email(parsed, [other])
        self.assertEqual(d['action'], 'none')

    def test_two_requests_only_one_reference_matches(self):
        parsed = parse_result_email(UNICOIN_BODY)
        good = _req()
        decoy = _req(id=2, ref='PAY/ADIC/2026/08/22/0002',
                     bank_our_reference='Grand RE Radical Investments',
                     payee='Grand RE', subject='Grand RE')
        d = match_paid_email(parsed, [decoy, good])
        self.assertEqual(d['action'], 'close')
        self.assertEqual(d['request_ref'], 'PAY/ADIC/2026/08/22/0001')

    def test_shared_amount_and_reference_is_ambiguous(self):
        parsed = parse_result_email(UNICOIN_BODY)
        a = _req(id=1, ref='A')
        b = _req(id=2, ref='B')      # identical amount + reference → cannot decide
        d = match_paid_email(parsed, [a, b])
        self.assertEqual(d['action'], 'ambiguous')

    def test_matches_on_batch_id_when_reference_absent(self):
        # Real case (GRAND RE): the request stored no payee/reference, but the FNB
        # email's reference is the unique EFT batch id Omni loaded it under.
        body = ("FNB:-) The OnceOff Payment ALPHA-EFT-20260821-09da76bc3db645f8 to "
                "the total value of BWP30459.11 has been processed and is now in a "
                "status of Fully Processed.")
        parsed = parse_result_email(body)
        req = _req(id=9, ref='PAY/ADIC/2026/08/21/0002', total='30459.11',
                   bank_our_reference='', payee='', subject='',
                   batch_key='ALPHA-EFT-20260821-09da76bc3db645f8')
        d = match_paid_email(parsed, [req])
        self.assertEqual(d['action'], 'close')
        self.assertEqual(d['request_ref'], 'PAY/ADIC/2026/08/21/0002')

    def test_matches_on_a_short_real_batch_id(self):
        # Production 14-Sep-2026: the real idempotency_key format is short
        # ('GRAND RE 177 (O)', 'SCANIA 195 (O)') — not the long fake
        # 'ALPHA-EFT-...' example above. The >=12-char length guard in
        # _batch_matches was rejecting an EXACT match on these because the
        # normalised key was only 10-11 characters, leaving live payments
        # stuck open forever despite FNB confirming them by email.
        body = ("FNB:-) The OnceOff Payment GRAND RE 177 (O) to "
                "the total value of BWP1279.86 has been processed and is now in a "
                "status of Fully Processed.")
        parsed = parse_result_email(body)
        req = _req(id=10, ref='PAY/ADIC/2026/09/11/0003', total='1279.86',
                   bank_our_reference='', payee='', subject='',
                   batch_key='GRAND RE 177 (O)')
        d = match_paid_email(parsed, [req])
        self.assertEqual(d['action'], 'close')
        self.assertEqual(d['request_ref'], 'PAY/ADIC/2026/09/11/0003')

    def test_terminal_match_blocks_closing_a_sibling(self):
        # Bug 1 fix: the email is re-read over ~36h. Once the right request is PAID,
        # a re-read must NOT fall through and close an open same-vendor/same-amount
        # sibling (recurring vendor payments). A terminal match => already reconciled.
        parsed = parse_result_email(UNICOIN_BODY)
        already_paid = _req(id=1, ref='PAY/ADIC/2026/08/22/0001', is_open=False)
        open_sibling = _req(id=2, ref='PAY/ADIC/2026/08/29/0007', is_open=True)
        d = match_paid_email(parsed, [already_paid, open_sibling])
        self.assertEqual(d['action'], 'none')

    def test_not_paid_email_does_nothing(self):
        parsed = parse_result_email(AUTHORISED_BODY)
        d = match_paid_email(parsed, [_req(total='500.00',
                                           bank_our_reference='Some Payee 999',
                                           payee='Some Payee')])
        self.assertEqual(d['action'], 'none')


if __name__ == '__main__':
    unittest.main()
