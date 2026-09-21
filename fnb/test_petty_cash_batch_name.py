"""fnb/test_petty_cash_batch_name.py — a petty-cash batch says it is petty cash.

CFO 2026-09-09, looking at the live batch `Lefika Basotli 000153 (O)` on FNB's
list: "this is petty cash reimbursement, so it should be Petty Cash - Lefika
(O), not lefika". A single payment was named after the person alone, so on the
bank list a petty-cash float was indistinguishable from any other payment to a
member of staff.

The batch that prompted it was raised under category `other` with
bank_payment_type `other`, so neither field marked it — the words were only in
the narration ('September Pettycash Reimbursement'). So the wording is the
discriminator here, the same choice already made for broker commission.

What these tests pin:
  - A single petty-cash payment reads `Petty Cash - <name> <seq>`.
  - The sequence digits stay immediately before the ` (O)` marker, because the
    FNB email auto-reconcile matches on exactly that.
  - The batch name never exceeds FNB's 35 characters, and a long name loses
    characters before the sequence does.
  - Every other payment is unchanged: an ordinary payee, and a broker
    commission, still read exactly as they did.

No real payee or staff names. Omni moves no money: this is the label on a batch
that is then authorised in the FNB app with two factors.

Run: manage.py test fnb.test_petty_cash_batch_name
"""
from django.test import SimpleTestCase

from fnb.payments import (OMNI_MARKER, _batch_reference, _compose_key,
                          _petty_cash_reference)


class _Payment:
    """The few attributes the batch namer reads. Not a DB row on purpose — the
    naming rule is pure text and must be testable without a database."""

    def __init__(self, *, payment_number='PAY-OUT-2026-000153', narration='',
                 description='', reference='', payee_name='Test Person'):
        self.payment_number = payment_number
        self.bank_narration = narration
        self.description = description
        self.reference = reference
        self.payee_name = payee_name
        self.vendor_bank_account = None
        self.contact = None


class PettyCashBatchNameTests(SimpleTestCase):

    def test_a_single_petty_cash_payment_says_petty_cash(self):
        p = _Payment(narration='September Pettycash Reimbursement',
                     payee_name='Test Person')
        self.assertEqual(_batch_reference([p]), 'Petty Cash - Test Person 153')

    def test_the_older_spelling_with_a_space_is_caught_too(self):
        p = _Payment(narration='PETTY CASH REIMBURSMENT', payee_name='Sample')
        self.assertEqual(_batch_reference([p]), 'Petty Cash - Sample 153')

    def test_the_words_are_found_on_the_description_as_well(self):
        p = _Payment(description='Petty Cash float top-up', payee_name='Sample')
        self.assertTrue(_batch_reference([p]).startswith('Petty Cash - '))

    def test_the_sequence_digits_stay_next_to_the_omni_marker(self):
        # fnb_list_reconcile matches the digits immediately before ' (O)'.
        p = _Payment(narration='September Pettycash Reimbursement',
                     payee_name='A Very Long Sample Payee Name Indeed')
        key = _compose_key(_batch_reference([p]))
        self.assertTrue(key.endswith(f'153 {OMNI_MARKER}'), key)
        self.assertLessEqual(len(key), 35)

    def test_a_long_name_falls_back_to_the_first_name_not_a_cut_word(self):
        self.assertEqual(
            _petty_cash_reference('Kealeboga Mmualefhe Sechele', '000153'),
            'Petty Cash - Kealeboga 000153')

    def test_the_label_survives_even_when_no_name_fits(self):
        ref = _petty_cash_reference('Extraordinarilylongsinglename', '000153')
        self.assertLessEqual(len(_compose_key(ref)), 35)
        self.assertIn('Petty Cash', ref)
        self.assertTrue(ref.endswith('000153'))


class EverythingElseIsUnchangedTests(SimpleTestCase):

    def test_an_ordinary_single_payment_still_reads_as_the_payee(self):
        p = _Payment(narration='Invoice 5512 panel repair',
                     payee_name='Sample Supplier')
        self.assertEqual(_batch_reference([p]), 'Sample Supplier 153')

    def test_a_broker_commission_still_reads_as_the_broker(self):
        p = _Payment(narration='BROKER COMMISSION SPECTRUM AUG26',
                     payee_name='Sample Broker')
        self.assertEqual(_batch_reference([p]), 'BKR COMM SPECTRUM 153')

    def test_a_multi_payment_batch_still_reads_as_a_count(self):
        pmts = [_Payment(narration='September Pettycash Reimbursement'),
                _Payment(payment_number='PAY-OUT-2026-000154')]
        self.assertEqual(_batch_reference(pmts), 'EFT 2 payments 153')
