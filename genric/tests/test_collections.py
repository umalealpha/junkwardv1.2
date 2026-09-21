"""WHY: the bank is the truth, and one ordering mistake inflates GWP by every
fee line in the month.

"PAYAT…SF" contains "PAYAT". If the PayAt inclusion rule is tested before the
PayAt service-fee exclusion, every service fee is counted as premium, the GWP
goes up, and we invoice GENRIC 90% of money that is a bank charge. That is the
single most expensive mistake available in this module, so it has its own test.

The July 2026 figures are pinned: 93 confirmed collections, R9,207.00 confirmed
GWP incl VAT, against Graphite's 81 "successful" — and the 12-policy gap is
REPORTED, not reconciled away.
"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from genric import collections as C


class _Line:
    """Shaped like banking.BankStatementLine, without a database."""

    def __init__(self, description, amount, reference='', day=1):
        self.id = f'{description}-{amount}-{day}'
        self.description = description
        self.amount = Decimal(str(amount))
        self.reference = reference
        self.transaction_date = date(2026, 7, day)


class ClassificationTests(SimpleTestCase):

    def test_realpay_credit_is_premium(self):
        self.assertEqual(C.classify_one('REREALPAY COLLECTION', 99)[0], C.INCLUDE)

    def test_realpay_debit_is_a_reversal_and_subtracts(self):
        outcome, _, _ = C.classify_one('REREALPAY REVERSAL', -99)
        self.assertEqual(outcome, C.REVERSAL)

    def test_payat_credit_is_premium(self):
        self.assertEqual(C.classify_one('PAYAT PAYMENT 12345', 99)[0], C.INCLUDE)

    def test_fnbd_direct_eft_is_premium(self):
        self.assertEqual(C.classify_one('FNBD TRF PREMIUM', 99)[0], C.INCLUDE)

    def test_service_fees_are_excluded(self):
        self.assertEqual(C.classify_one('#SERVICE FEES', -35)[0], C.EXCLUDE)

    def test_interest_on_credit_balance_is_excluded(self):
        self.assertEqual(C.classify_one('INT ON CREDIT BALANCE', 12.4)[0], C.EXCLUDE)

    def test_payat_service_fee_is_excluded_not_counted_as_premium(self):
        """THE ordering test. 'PAYAT...SF' contains 'PAYAT'."""
        outcome, rule, _ = C.classify_one('PAYAT SETTLEMENT SF', -18.5)
        self.assertEqual(outcome, C.EXCLUDE, 'a PayAt service fee was counted as premium')
        self.assertIn('SERVICE FEE', rule.upper())

    def test_payat_service_fee_is_excluded_even_when_positive(self):
        """A fee refund is still not premium."""
        self.assertEqual(C.classify_one('PAYAT SETTLEMENT SF', 18.5)[0], C.EXCLUDE)

    def test_an_unknown_line_is_unclassified_never_silently_dropped(self):
        outcome, _, _ = C.classify_one('ACB CREDIT SOME MERCHANT', 500)
        self.assertEqual(outcome, C.UNCLASSIFIED)

    def test_an_unknown_debit_is_unclassified_not_a_reversal(self):
        outcome, _, _ = C.classify_one('ATM WITHDRAWAL', -500)
        self.assertEqual(outcome, C.UNCLASSIFIED)


class JulySummaryTests(SimpleTestCase):
    """93 confirmed collections at R99 = R9,207.00 confirmed GWP incl VAT."""

    def _july(self):
        lines = [_Line('REREALPAY COLLECTION', 99, day=(i % 28) + 1) for i in range(60)]
        lines += [_Line('PAYAT PAYMENT', 99, day=(i % 28) + 1) for i in range(25)]
        lines += [_Line('FNBD TRF PREMIUM', 99, day=(i % 28) + 1) for i in range(8)]
        lines += [
            _Line('#SERVICE FEES', -350),
            _Line('INT ON CREDIT BALANCE', 12.40),
            _Line('PAYAT SETTLEMENT SF', -18.50),
        ]
        return lines

    def test_confirmed_gwp_is_the_july_worked_example(self):
        s = C.summarise(C.classify_lines(self._july()))
        self.assertEqual(s.confirmed_count, 93)
        self.assertEqual(s.confirmed_gwp_incl_vat, Decimal('9207.00'))

    def test_fees_and_interest_are_not_in_gwp(self):
        s = C.summarise(C.classify_lines(self._july()))
        self.assertEqual(s.excluded_count, 3)
        self.assertEqual(s.unclassified_count, 0)

    def test_a_reversal_reduces_both_the_count_and_the_money(self):
        lines = self._july() + [_Line('REREALPAY REVERSAL', -99)]
        s = C.summarise(C.classify_lines(lines))
        self.assertEqual(s.reversal_count, 1)
        self.assertEqual(s.net_collection_count, 92)
        self.assertEqual(s.confirmed_gwp_incl_vat, Decimal('9108.00'))

    def test_every_line_lands_in_exactly_one_bucket(self):
        lines = self._july() + [_Line('ACB CREDIT MYSTERY', 250)]
        s = C.summarise(C.classify_lines(lines))
        total = (s.confirmed_count + s.reversal_count
                 + s.excluded_count + s.unclassified_count)
        self.assertEqual(total, len(lines))
        self.assertEqual(s.unclassified_count, 1)
