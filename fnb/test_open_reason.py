"""Unit tests for open_reason() — the plain-English "why is this payment still
open?" label shown on the payment queue (CFO 2026-09-05; hardened per Fable
2026-09-06).

Pure function, no Django — mirrors fnb/email_reconcile.py's own design so they run
under pytest anywhere (Windows sqlite can't build the test DB).
"""
import unittest

from fnb.email_reconcile import open_reason


class OpenReasonTests(unittest.TestCase):
    def test_failed_is_reject_and_shows_reason(self):
        text, tone = open_reason(batch_status='failed', has_batch=True,
                                 failure_reason='AC08: BRANCH CODE IS INVALID OR MISSING')
        self.assertEqual(tone, 'reject')
        self.assertIn('Rejected by FNB', text)
        self.assertIn('AC08', text)          # the bank's reason is surfaced
        self.assertIn('reload', text.lower())

    def test_unknown_is_check_verify_with_fnb(self):
        text, tone = open_reason(batch_status='unknown', has_batch=True)
        self.assertEqual(tone, 'check')
        self.assertIn('verify with fnb', text.lower())

    def test_settled_is_paid(self):
        # GREEN "paid" is earned ONLY by the bank API marking the batch settled.
        text, tone = open_reason(batch_status='settled', has_batch=True)
        self.assertEqual(tone, 'paid')
        self.assertIn('closing', text.lower())

    def test_submitted_waits_for_fnb_authorisation(self):
        text, tone = open_reason(batch_status='submitted', has_batch=True)
        self.assertEqual(tone, 'wait')
        self.assertIn('authorisation', text.lower())
        self.assertIn('fnb', text.lower())

    def test_acknowledged_also_waits(self):
        _, tone = open_reason(batch_status='acknowledged', has_batch=True)
        self.assertEqual(tone, 'wait')

    def test_no_batch_is_check(self):
        text, tone = open_reason(batch_status='', has_batch=False)
        self.assertEqual(tone, 'check')
        self.assertIn('not loaded', text.lower())

    def test_exception_is_flagged(self):
        _, tone = open_reason(batch_status='', has_batch=False, is_exception=True)
        self.assertEqual(tone, 'exception')

    # --- the priority rules that actually matter -------------------------------

    def test_failed_beats_a_matching_paid_email(self):
        # A rejected batch must read "rejected", never "paid"/"check", even if a
        # stray paid email matches the amount — you must fix and reload.
        _, tone = open_reason(batch_status='failed', has_batch=True,
                              email_confidence='confident')
        self.assertEqual(tone, 'reject')

    def test_email_confident_is_check_not_paid(self):
        # A reference/batch overlap from catchup_matches is NOT proof of payment for
        # THIS request (no terminal-sibling guard) — so it must say "check", never
        # paint green "paid". Green comes only from the bank API (test_settled_is_paid).
        text, tone = open_reason(batch_status='submitted', has_batch=True,
                                 email_confidence='confident')
        self.assertEqual(tone, 'check')
        self.assertIn('check before', text.lower())

    def test_last_months_email_never_reads_paid(self):
        # Fable F2: a recurring same-amount payment's OLD "Fully Processed" email
        # must never paint this month's open request green. Confident-but-not-settled
        # is 'check', never 'paid'.
        _, tone = open_reason(batch_status='submitted', has_batch=True,
                              email_confidence='confident')
        self.assertNotEqual(tone, 'paid')

    def test_amount_only_review_is_check(self):
        # THE ALEX FORBES CASE (P613,885.92): batch still 'submitted' but a paid
        # email matches the amount only. Warn "check", NOT the calm "waiting", or the
        # CFO could authorise the Omni batch and pay it a SECOND time.
        text, tone = open_reason(batch_status='submitted', has_batch=True,
                                 email_confidence='review')
        self.assertEqual(tone, 'check')
        self.assertIn('exact amount', text.lower())
        self.assertIn('check before', text.lower())

    def test_ambiguous_email_is_also_check(self):
        _, tone = open_reason(batch_status='submitted', has_batch=True,
                              email_confidence='ambiguous')
        self.assertEqual(tone, 'check')

    def test_unavailable_flags_on_waiting_row(self):
        # Fable F1: when the bank-email read failed this load, a "waiting" row must
        # ADMIT it could not check — never fall back silently to the calm "waiting",
        # which could hide an already-paid payment.
        text, tone = open_reason(batch_status='submitted', has_batch=True,
                                 email_confidence='unavailable')
        self.assertEqual(tone, 'wait')
        self.assertIn('unavailable', text.lower())


if __name__ == '__main__':
    unittest.main()
