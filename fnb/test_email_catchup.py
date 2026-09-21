"""Unit tests for the human-supervised catch-up matcher (CFO 2026-08-24).

Pure functions, no Django — mirrors fnb/email_reconcile.py's own design so they
run under pytest anywhere (Windows sqlite can't build the test DB).
"""
import unittest
from decimal import Decimal

from fnb.email_reconcile import catchup_matches


def _email(ref, amount, date='2026-08-20'):
    return {'ref': ref, 'amount': Decimal(str(amount)), 'paid': True, 'date': date}


class CatchupMatchesTests(unittest.TestCase):
    def test_reference_overlap_is_confident(self):
        req = {'total': Decimal('2800.00'), 'payee': 'Unicoin-AD Transportation',
               'bank_our_reference': 'Unicoin-AD Transportation 2108'}
        emails = [_email('Unicoin-AD Transportation2108', '2800.00')]
        res = catchup_matches(req, emails)
        self.assertEqual(res['confidence'], 'confident')
        self.assertEqual(len(res['matches']), 1)

    def test_batch_id_overlap_is_confident(self):
        req = {'total': Decimal('30459.11'), 'payee': '',
               'batch_key': 'ALPHA-EFT-20260821-09da76bc3db645f8'}
        emails = [_email('ALPHA-EFT-20260821-09da76bc3db645f8', '30459.11')]
        self.assertEqual(catchup_matches(req, emails)['confidence'], 'confident')

    def test_amount_only_single_is_review(self):
        # No reference/batch overlap, exactly one email at the amount -> eyeball it.
        req = {'total': Decimal('59595.67'), 'payee': '', 'bank_our_reference': ''}
        emails = [_email('SomeVendor Invoice 771', '59595.67')]
        res = catchup_matches(req, emails)
        self.assertEqual(res['confidence'], 'review')
        self.assertEqual(len(res['matches']), 1)

    def test_amount_only_multiple_is_ambiguous(self):
        req = {'total': Decimal('5000.00'), 'payee': '', 'bank_our_reference': ''}
        emails = [_email('Vendor A 1', '5000.00'), _email('Vendor B 2', '5000.00')]
        res = catchup_matches(req, emails)
        self.assertEqual(res['confidence'], 'ambiguous')
        self.assertEqual(len(res['matches']), 2)

    def test_no_amount_match_is_none(self):
        req = {'total': Decimal('1234.00'), 'payee': '', 'bank_our_reference': ''}
        emails = [_email('Whoever 9', '9999.00')]
        self.assertEqual(catchup_matches(req, emails)['confidence'], 'none')

    def test_empty_emails_is_none(self):
        req = {'total': Decimal('1234.00')}
        self.assertEqual(catchup_matches(req, [])['confidence'], 'none')

    def test_a_short_real_batch_key_is_confident_not_review(self):
        """Production 14-Sep-2026: idempotency_key is short-vendor + batch-number
        + entity-code ('SCANIA 195 (O)'), not the long 'ALPHA-EFT-...' the old
        length guard assumed. An IDENTICAL string was being rejected on length
        alone and demoted to 'review' -> never auto-closed."""
        req = {'total': Decimal('26854.97'), 'payee': '',
               'batch_key': 'SCANIA 195 (O)'}
        emails = [_email('SCANIA 195 (O)', '26854.97')]
        res = catchup_matches(req, emails)
        self.assertEqual(res['confidence'], 'confident')

    def test_a_blank_batch_key_still_never_matches(self):
        req = {'total': Decimal('100.00'), 'payee': '', 'batch_key': ''}
        emails = [_email('Some Vendor 1', '100.00')]
        # No batch_key and no reference overlap -> falls back to amount-only.
        self.assertEqual(catchup_matches(req, emails)['confidence'], 'review')

    def test_a_short_key_does_not_falsely_match_a_longer_unrelated_reference(self):
        """Panel review 2026-09-14 (L6): the fix must be an exact match, not a
        merely-shorter substring guard — otherwise a short real key could
        wrongly 'contain-match' an unrelated longer reference at the same
        amount."""
        req = {'total': Decimal('500.00'), 'payee': '', 'batch_key': 'SCANIA 1 (O)'}
        emails = [_email('SCANIA 1 (O) BUT ACTUALLY A DIFFERENT VENDOR ENTIRELY', '500.00')]
        res = catchup_matches(req, emails)
        self.assertNotEqual(res['confidence'], 'confident')

    def test_reference_wins_over_amount_ambiguity(self):
        # Two emails share the amount, but one names this request -> confident, and
        # only the named one is returned (not the unrelated same-amount sibling).
        req = {'total': Decimal('7000.00'), 'payee': 'Grand RE',
               'bank_our_reference': 'Grand RE Invoice 44'}
        emails = [_email('Grand RE Invoice 44', '7000.00'),
                  _email('Unrelated Co 88', '7000.00')]
        res = catchup_matches(req, emails)
        self.assertEqual(res['confidence'], 'confident')
        self.assertEqual(len(res['matches']), 1)
        self.assertIn('Grand RE', res['matches'][0]['ref'])


if __name__ == '__main__':
    unittest.main()
