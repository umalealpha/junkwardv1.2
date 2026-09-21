"""Unit tests for the "upload FNB list" reconcile engine (CFO directive 2026-09-06).

Pure functions, no Django / no PDF lib — the text parser is tested against the exact
FNB "Batch Payments" export text, and the matcher against representative requests.
"""
import unittest

from fnb.fnb_list_reconcile import _entries_from_text, request_in_list

# The exact shape pdfplumber extracts from the FNB export: one line per entry,
# "<name> <date> <amount> <status>", name first (verified on the real PDF 2026-09-06).
SAMPLE = """Batch Payments
API Batches
Name Details Total Status
ALEX FORBES PENSION 000064 (O) 2026/09/02 24,482.00 Authorisation Requested
Same Day
Data Cloud (Propriet 000108 (O) 2026/09/05 7,245.77 Authorisation Requested
Same Day
G2099000001 TEST MOTORS 2026/09/04 376,477.01 Authorisation Requested
Same Day
MIS2099000001/Test Claimant 2026/09/05 1,188.00 Authorisation Requested
Same Day
"""


class ParseTests(unittest.TestCase):
    def test_parses_entries_with_amount_and_name(self):
        e = _entries_from_text(SAMPLE)
        self.assertEqual(len(e), 4)
        by_amt = {x['amount']: x for x in e}
        self.assertIn('24482.00', by_amt)
        self.assertEqual(by_amt['24482.00']['name'], 'ALEX FORBES PENSION 000064 (O)')
        self.assertEqual(by_amt['24482.00']['onum'], '64')            # (O) number extracted, leading zeros stripped
        self.assertEqual(by_amt['7245.77']['onum'], '108')
        self.assertIsNone(by_amt['376477.01']['onum'])              # manual entry, no (O)

    def test_empty_or_garbage_raises(self):
        with self.assertRaises(ValueError):
            _entries_from_text('')
        with self.assertRaises(ValueError):
            _entries_from_text('just some words\nno amounts here')


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.entries = _entries_from_text(SAMPLE)

    def test_o_batch_stays_by_omni_number(self):
        # An Omni-loaded request whose batch id carries 000064 is still on the list.
        md = {'total': '24482.00', 'batch_key': 'ALEX FORBES PENSION 000064 (O)',
              'payee': '', 'bank_our_reference': '', 'bank_narration': '',
              'subject': '', 'line_items': []}
        self.assertTrue(request_in_list(md, self.entries))

    def test_o_batch_matches_even_if_name_truncated_differently(self):
        # FNB truncates the payee; matching on the unique 000108 must still hold.
        md = {'total': '7245.77',
              'batch_key': 'Data Cloud (Proprietary) Ltd 000108 (O)',  # fuller name
              'payee': 'Data Cloud', 'bank_our_reference': '', 'bank_narration': '',
              'subject': '', 'line_items': []}
        self.assertTrue(request_in_list(md, self.entries))

    def test_manual_entry_matches_on_amount_plus_reference(self):
        # No (O) batch; matched by amount + a name/reference overlap.
        md = {'total': '1188.00', 'batch_key': '',
              'payee': 'Test Claimant', 'bank_our_reference': '',
              'bank_narration': '', 'subject': 'MIS2099000001 claim', 'line_items': []}
        self.assertTrue(request_in_list(md, self.entries))

    def test_not_on_list_is_false(self):
        md = {'total': '999.99', 'batch_key': 'Someone Else 000999 (O)',
              'payee': 'Someone Else', 'bank_our_reference': '', 'bank_narration': '',
              'subject': '', 'line_items': []}
        self.assertFalse(request_in_list(md, self.entries))

    def test_same_amount_different_payee_is_not_a_match(self):
        # A request sharing an amount with a list entry but a different payee must
        # NOT be treated as on the list (amount alone never matches).
        md = {'total': '376477.01', 'batch_key': '',
              'payee': 'Totally Different Vendor', 'bank_our_reference': '',
              'bank_narration': '', 'subject': 'unrelated', 'line_items': []}
        self.assertFalse(request_in_list(md, self.entries))


if __name__ == '__main__':
    unittest.main()
