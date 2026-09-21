"""The fee-note register import — the rules that decide what a row means."""

import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from bonu.legal_bill_import import (RowError, canonical_firm, derive_stage, normalise_firm,
                                    parse_date, parse_money, parse_row, reconcile)


def a_row(**over):
    row = {
        'source_row': 1318,
        'firm': 'Brown and Company',
        'bill_date': '16-Jul-2026',
        'reference': '000861',
        'client': 'A Member',
        'amount': 2848.86,
        'discount': 0,
        'amount_paid': 2848.86,
        'payment_date': '03-Sep-2026',
        'status': 'Paid',
    }
    row.update(over)
    return row


class NormaliseFirmTests(SimpleTestCase):

    def test_spacing_case_and_ampersand_are_the_same_firm(self):
        """The register and the panel list spell the same firms differently."""
        self.assertEqual(normalise_firm('CHIKATI & PARTNERS'),
                         normalise_firm('Chikati and Partners'))
        self.assertEqual(normalise_firm('Brown  And Company'),
                         normalise_firm('Brown and Company'))
        self.assertEqual(normalise_firm('Otto Itumeleng Law  Chambers'),
                         normalise_firm('Otto Itumeleng Law Chambers'))

    def test_different_firms_stay_different(self):
        """Normalising must not merge two firms that are genuinely two firms."""
        self.assertNotEqual(normalise_firm('Jere Attorneys'),
                            normalise_firm('Jeremiah & Co'))
        self.assertNotEqual(normalise_firm('Kole Law Practice'),
                            normalise_firm('Kagisano Attorneys'))

    def test_a_judgment_call_is_written_down_not_inferred(self):
        """'Jeremiah & Co' only reaches the panel record through an explicit alias —
        the retainer scorecard hangs off that record."""
        self.assertEqual(canonical_firm('Jeremiah & Co'),
                         normalise_firm('JEREMIAH TLADI & COMPANY'))
        self.assertEqual(canonical_firm('Tony Matilo Attorneys'),
                         normalise_firm('TONEY MATILO ATTORNEYS'))
        self.assertEqual(canonical_firm('Thobega'), normalise_firm('Thobega Law Group'))

    def test_an_unknown_firm_is_left_alone(self):
        self.assertEqual(canonical_firm('Helfer & Company'), 'HELFER AND COMPANY')


class StageFromMoneyTests(SimpleTestCase):

    def test_settled_when_the_cash_covers_the_bill(self):
        self.assertEqual(derive_stage(Decimal('100'), Decimal('0'), Decimal('100')), 'paid')

    def test_a_discount_lowers_what_must_be_paid(self):
        self.assertEqual(derive_stage(Decimal('100'), Decimal('10'), Decimal('90')), 'paid')

    def test_part_payment_is_not_settled(self):
        self.assertEqual(derive_stage(Decimal('100'), Decimal('0'), Decimal('40')), 'billed')

    def test_paid_with_no_money_recorded_is_not_settled(self):
        """Five rows say Paid and record nothing — a bulk transfer nobody split
        back out. Believing the word would overstate what has left the bank."""
        self.assertEqual(derive_stage(Decimal('491.84'), Decimal('0'), Decimal('0')), 'billed')


class ParseRowTests(SimpleTestCase):

    def test_reads_a_clean_row(self):
        p = parse_row(a_row())
        self.assertEqual(p['source_row'], 1318)
        self.assertEqual(p['amount'], Decimal('2848.86'))
        self.assertEqual(p['bill_date'], datetime.date(2026, 7, 16))
        self.assertEqual(p['paid_on'], datetime.date(2026, 9, 3))
        self.assertEqual(p['stage'], 'paid')
        self.assertEqual(p['billed_client_name'], 'A Member')

    def test_keeps_the_registers_own_status_wording_on_the_note(self):
        """The status is not believed, but it is not thrown away either."""
        p = parse_row(a_row(status='Npt yet captured', amount_paid=0, payment_date=None))
        self.assertIn('Npt yet captured', p['note'])
        self.assertEqual(p['stage'], 'billed')

    def test_a_payment_with_no_readable_date_keeps_the_money(self):
        """One row carries '03-Jul-202'. Dropping the row would lose real cash."""
        p = parse_row(a_row(payment_date='03-Jul-202'))
        self.assertIsNone(p['paid_on'])
        self.assertEqual(p['amount_paid'], Decimal('2848.86'))
        self.assertIn('cannot be placed in a month', p['note'])

    def test_an_unreadable_amount_raises_rather_than_reading_as_zero(self):
        with self.assertRaises(RowError):
            parse_row(a_row(amount='n/a'))

    def test_a_missing_invoice_date_raises(self):
        with self.assertRaises(RowError):
            parse_row(a_row(bill_date=''))

    def test_a_zero_bill_raises(self):
        with self.assertRaises(RowError):
            parse_row(a_row(amount=0))

    def test_a_missing_column_names_itself(self):
        row = a_row()
        del row['amount_paid']
        with self.assertRaises(RowError) as ctx:
            parse_row(row)
        self.assertIn('amount_paid', str(ctx.exception))

    def test_blank_money_cells_are_zero_not_an_error(self):
        p = parse_row(a_row(discount='', amount_paid=''))
        self.assertEqual(p['discount'], Decimal('0'))
        self.assertEqual(p['amount_paid'], Decimal('0'))


class MoneyAndDateTests(SimpleTestCase):

    def test_money_lands_on_the_two_decimals_the_column_stores(self):
        """The column is numeric(14,2) and some source cells carry three decimals.

        The rounding has to happen somewhere; it happens at read time so the
        parsed figure IS the stored figure and the reconciliation compares like
        with like. Left to Django's save it would happen invisibly, and half to
        even rather than the house's half up.
        """
        self.assertEqual(parse_money('4705044.864', field='x', source_row=1),
                         Decimal('4705044.86'))

    def test_money_rounds_half_up_not_half_to_even(self):
        """Rounding is a tax decision, never a language default: 0.125 is 0.13.

        Python's own default would answer 0.12 here.
        """
        self.assertEqual(parse_money('0.125', field='x', source_row=1), Decimal('0.13'))
        self.assertEqual(parse_money('0.135', field='x', source_row=1), Decimal('0.14'))

    def test_thousands_separators_are_read(self):
        self.assertEqual(parse_money('1,906.08', field='x', source_row=1),
                         Decimal('1906.08'))

    def test_unreadable_date_is_none_never_today(self):
        self.assertIsNone(parse_date('31/11/2025'))
        self.assertIsNone(parse_date('13/072025'))


class ReconcileTests(SimpleTestCase):

    def test_totals_are_re_added_from_the_rows(self):
        parsed = [parse_row(a_row(source_row=1, amount=100, discount=10, amount_paid=90)),
                  parse_row(a_row(source_row=2, amount=50, discount=0, amount_paid=0,
                                  payment_date=None, status='Unpaid'))]
        t = reconcile(parsed)
        self.assertEqual(t['rows'], 2)
        self.assertEqual(t['invoiced'], Decimal('150'))
        self.assertEqual(t['discount'], Decimal('10'))
        self.assertEqual(t['paid'], Decimal('90'))
        self.assertEqual(t['outstanding'], Decimal('50'))

    def test_counts_payments_that_cannot_be_placed_in_a_month(self):
        parsed = [parse_row(a_row(source_row=1, payment_date='03-Jul-202')),
                  parse_row(a_row(source_row=2))]
        self.assertEqual(reconcile(parsed)['paid_without_date'], 1)
