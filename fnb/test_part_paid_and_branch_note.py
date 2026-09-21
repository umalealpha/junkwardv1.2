"""CFO master M4 (18-Sep-2026): part-settled split requests and the advisory
branch-code note. Both are pure functions, so no database is needed."""
from django.test import SimpleTestCase

from fnb.destination_bank import branch_code_advisory
from fnb.email_reconcile import open_reason


class PartPaidSplitRequestTests(SimpleTestCase):
    def test_settled_and_rejected_together_is_part_paid_not_reload(self):
        text, tone = open_reason(batch_status='failed', has_batch=True,
                                 batch_statuses=['settled', 'failed', 'settled'])
        self.assertIn('Part-paid: 2 of 3', text)
        self.assertIn('Do NOT reload the paid lines', text)
        self.assertEqual(tone, 'check')

    def test_fk_settled_does_not_hide_a_rejected_sibling(self):
        text, _ = open_reason(batch_status='settled', has_batch=True,
                              batch_statuses=['settled', 'failed'])
        self.assertNotIn('closing shortly', text)
        self.assertIn('Part-paid', text)

    def test_an_unconfirmed_sibling_counts_as_not_paid(self):
        text, tone = open_reason(batch_status='settled', has_batch=True,
                                 batch_statuses=['settled', 'unknown'])
        self.assertIn('1 of 2', text)
        self.assertEqual(tone, 'check')

    def test_settled_and_still_waiting_says_so(self):
        text, tone = open_reason(batch_status='submitted', has_batch=True,
                                 batch_statuses=['settled', 'submitted'])
        self.assertIn('the rest are still with FNB', text)
        self.assertEqual(tone, 'wait')

    def test_a_rejected_line_is_not_hidden_behind_a_waiting_sibling(self):
        """Opus gate: FK on the waiting line, the other line rejected."""
        text, tone = open_reason(batch_status='submitted', has_batch=True,
                                 batch_statuses=['submitted', 'failed'])
        self.assertIn('1 of 2 bank lines rejected', text)
        self.assertEqual(tone, 'reject')

    def test_a_cancelled_batch_never_left_and_is_not_counted(self):
        text, tone = open_reason(batch_status='failed', has_batch=True,
                                 batch_statuses=['failed', 'cancelled'])
        self.assertTrue(text.startswith('Rejected by FNB'))
        self.assertEqual(tone, 'reject')

    def test_a_single_batch_behaves_exactly_as_before(self):
        self.assertEqual(
            open_reason(batch_status='settled', has_batch=True, batch_statuses=['settled']),
            open_reason(batch_status='settled', has_batch=True))
        self.assertEqual(
            open_reason(batch_status='failed', failure_reason='AC08', has_batch=True,
                        batch_statuses=['failed']),
            open_reason(batch_status='failed', failure_reason='AC08', has_batch=True))


class BranchCodeAdvisoryTests(SimpleTestCase):
    def test_right_shape_unknown_prefix_gets_a_note(self):
        note = branch_code_advisory('Stanbic Bank', '771234')
        self.assertIn('"771234"', note)
        self.assertIn('not a hold', note)

    def test_known_botswana_prefix_gets_nothing(self):
        self.assertEqual(branch_code_advisory('FNB Botswana', '281267'), '')
        self.assertEqual(branch_code_advisory('Stanbic', '060601'), '')

    def test_wrong_shape_is_the_committee_path_not_this_note(self):
        self.assertEqual(branch_code_advisory('Absa', '2901'), '')
        self.assertEqual(branch_code_advisory('Absa', '29-0167'), '')

    def test_blank_gets_nothing(self):
        self.assertEqual(branch_code_advisory('FNB Botswana', ''), '')

    def test_a_foreign_bank_is_not_flagged(self):
        self.assertEqual(branch_code_advisory('FNB South Africa', '250655'), '')
        self.assertEqual(branch_code_advisory('Standard Bank Namibia', '082772'), '')
