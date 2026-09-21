"""
regulatory/tests/test_vat_recon.py — the VAT reconciliation (build spec B2).

The engine is pure, so almost everything here is a SimpleTestCase with
`databases = set()` — no database, no fixtures, just the arithmetic with worked
figures. The two database tests at the bottom prove the real gather-and-tie
path, not a mock of it.

Two of these tests are the ones the spec asks for, and they are lifted straight
from `billing/tests/test_reverse_charge.py`, which already proved the same two
things about the same setting:

  * the `.005` boundary rounds HALF UP, not banker's — chosen so the two
    disagree, otherwise the test proves nothing;
  * the rate is genuinely a PARAMETER — run the identical figures at 14% and at
    15% and show the answer moves.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings

from regulatory.tax_calendar import vat_due_date, vat_prepare_by_date
from regulatory.vat_recon import (
    ExceptionCode,
    LedgerBalance,
    Side,
    VatSourceLine,
    expected_vat,
    money,
    reconcile_vat,
)

RATE_14 = Decimal('0.14')
RATE_15 = Decimal('0.15')

P_START = date(2026, 6, 1)
P_END = date(2026, 6, 30)


def _line(net, vat, side=Side.OUTPUT, *, reference='INV-1', party='Acme',
          doc_date=date(2026, 6, 15), kind='customer_invoice', rate_applies=True):
    return VatSourceLine(
        reference=reference, party=party, doc_date=doc_date,
        net_amount=Decimal(net), vat_amount=Decimal(vat),
        side=side, kind=kind, rate_applies=rate_applies,
    )


def _recon(lines, *, rate=RATE_14, output_gl=None, input_gl=None,
           period_start=P_START, period_end=P_END):
    return reconcile_vat(
        period_start=period_start,
        period_end=period_end,
        vat_rate=rate,
        due_date=vat_due_date(period_end),
        prepare_by_date=vat_prepare_by_date(period_end),
        lines=lines,
        output_control_accounts=output_gl,
        input_control_accounts=input_gl,
    )


def _gl_output(amount, code='209001'):
    """Output VAT is a liability: it sits on the CREDIT side."""
    return [LedgerBalance(account_code=code, account_name='VAT',
                          credit=Decimal(amount))]


def _gl_input(amount, code='132000'):
    """Input VAT is recoverable: it sits on the DEBIT side."""
    return [LedgerBalance(account_code=code, account_name='VAT Input',
                          debit=Decimal(amount))]


# ---------------------------------------------------------------------------
# (a) Rounding — HALF UP at a .005 boundary, never banker's rounding
# ---------------------------------------------------------------------------

class RoundingIsHalfUpTest(SimpleTestCase):
    databases = set()

    def test_expected_vat_rounds_half_up_at_the_boundary(self):
        # 0.75 * 0.14 = 0.1050 exactly — a true halfway case, sitting between
        # 0.10 and 0.11. ROUND_HALF_UP takes every tie away from zero -> 0.11.
        # ROUND_HALF_EVEN (Python's round() and Decimal's own default) would
        # land on 0.10, because 0 is already even. The two disagree here on
        # purpose: that is what makes this test prove anything.
        self.assertEqual(expected_vat(Decimal('0.75'), RATE_14), Decimal('0.11'))

    def test_bankers_rounding_would_have_given_the_wrong_answer(self):
        # Guards the REASON the rule exists, not just the rule. Decimal's own
        # default rounding is ROUND_HALF_EVEN, and on this exact figure it
        # disagrees with the tax answer: 0.1050 -> 0.10, because 0 is already
        # even. If the explicit rounding= argument is ever dropped from
        # money(), this line goes red.
        #
        # Deliberately done in Decimal, not float: round(0.75 * 0.14, 2)
        # happens to give 0.11 anyway, because the float product is
        # 0.10500000000000001 — a hair ABOVE the halfway point, so it is not a
        # tie at all and would prove nothing either way.
        bankers = Decimal('0.1050').quantize(Decimal('0.01'))
        self.assertEqual(bankers, Decimal('0.10'))
        self.assertNotEqual(bankers, expected_vat(Decimal('0.75'), RATE_14))

    def test_money_rounds_half_up(self):
        self.assertEqual(money(Decimal('0.005')), Decimal('0.01'))
        self.assertEqual(money(Decimal('0.015')), Decimal('0.02'))
        self.assertEqual(money(Decimal('-0.005')), Decimal('-0.01'))

    def test_a_half_up_line_still_reconciles(self):
        result = _recon(
            [_line('0.75', '0.11')],
            output_gl=_gl_output('0.11'), input_gl=_gl_input('0'),
        )
        self.assertEqual(result.output_vat, Decimal('0.11'))
        self.assertTrue(result.reconciled, result.exceptions)


# ---------------------------------------------------------------------------
# (b) The rate is genuinely a PARAMETER — same figures, two rates, two answers
# ---------------------------------------------------------------------------

class RateIsAParameterTest(SimpleTestCase):
    databases = set()

    LINES = [
        _line('10000.00', '1400.00', Side.OUTPUT, reference='INV-100'),
        _line('4000.00', '560.00', Side.INPUT, reference='BILL-100',
              kind='vendor_bill'),
    ]

    def test_at_fourteen_percent(self):
        result = _recon(
            self.LINES,
            rate=RATE_14,
            output_gl=_gl_output('1400.00'), input_gl=_gl_input('560.00'),
        )
        self.assertEqual(result.output_vat, Decimal('1400.00'))
        self.assertEqual(result.input_vat, Decimal('560.00'))
        self.assertEqual(result.net_vat, Decimal('840.00'))
        self.assertTrue(result.reconciled, result.exceptions)

    def test_the_same_figures_at_fifteen_percent_no_longer_reconcile(self):
        # Nothing about the documents changes — only the rate. If the rate were
        # hardcoded at 0.14 anywhere in the engine this would still come back
        # clean, and that is exactly the bug this test exists to catch.
        result = _recon(
            self.LINES,
            rate=RATE_15,
            output_gl=_gl_output('1400.00'), input_gl=_gl_input('560.00'),
        )
        codes = {e.code for e in result.exceptions}
        self.assertIn(ExceptionCode.RATE, codes)
        rate_exceptions = [e for e in result.exceptions if e.code == ExceptionCode.RATE]
        self.assertEqual(len(rate_exceptions), 2)
        by_ref = {e.reference: e for e in rate_exceptions}
        # 10000 x 0.15 = 1500.00, and the document says 1400.00.
        self.assertEqual(by_ref['INV-100'].expected, Decimal('1500.00'))
        self.assertEqual(by_ref['INV-100'].actual, Decimal('1400.00'))
        # 4000 x 0.15 = 600.00, and the document says 560.00.
        self.assertEqual(by_ref['BILL-100'].expected, Decimal('600.00'))

    def test_expected_vat_moves_with_the_rate(self):
        self.assertEqual(expected_vat(Decimal('1000.00'), RATE_14), Decimal('140.00'))
        self.assertEqual(expected_vat(Decimal('1000.00'), RATE_15), Decimal('150.00'))

    def test_no_vat_rate_literal_in_the_engine_source(self):
        # The spec's actual requirement, asserted directly: "never the literal
        # 0.14 in the code". Reading the source is the only way to prove the
        # absence of a constant that no behaviour would currently exercise.
        import ast
        import inspect

        from regulatory import vat_recon

        tree = ast.parse(inspect.getsource(vat_recon))
        # Drop every docstring, then unparse. ast.unparse also drops comments,
        # so what is left is executable code and nothing else — prose about the
        # rate can neither pass nor fail this test.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, 'body', [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:] or [ast.Pass()]
        code = ast.unparse(ast.fix_missing_locations(tree))

        self.assertNotIn('0.14', code,
                         'the VAT rate must be a parameter, never a literal')
        self.assertNotIn('1.14', code)

    def test_the_literal_check_can_actually_fail(self):
        # Proves the check above is not a tautology that passes on any file.
        import ast

        self.assertIn('0.14', ast.unparse(ast.parse("RATE = Decimal('0.14')")))


# ---------------------------------------------------------------------------
# (c) The net position — payable, refundable, nil
# ---------------------------------------------------------------------------

class NetPositionTest(SimpleTestCase):
    databases = set()

    def test_output_exceeds_input_is_payable(self):
        result = _recon(
            [_line('10000.00', '1400.00'),
             _line('1000.00', '140.00', Side.INPUT, reference='B1', kind='vendor_bill')],
            output_gl=_gl_output('1400.00'), input_gl=_gl_input('140.00'),
        )
        self.assertEqual(result.net_vat, Decimal('1260.00'))
        self.assertEqual(result.position, 'payable')
        self.assertTrue(result.is_payable)
        self.assertEqual(result.amount_due, Decimal('1260.00'))

    def test_input_exceeds_output_is_refundable_and_amount_due_is_positive(self):
        result = _recon(
            [_line('1000.00', '140.00'),
             _line('10000.00', '1400.00', Side.INPUT, reference='B1',
                   kind='vendor_bill')],
            output_gl=_gl_output('140.00'), input_gl=_gl_input('1400.00'),
        )
        self.assertEqual(result.net_vat, Decimal('-1260.00'))
        self.assertEqual(result.position, 'refundable')
        self.assertTrue(result.is_refundable)
        # What is actually claimed from BURS is a positive figure — a refund of
        # minus P1,260 is not a sentence anyone can act on.
        self.assertEqual(result.amount_due, Decimal('1260.00'))

    def test_nil_return(self):
        result = _recon([], output_gl=_gl_output('0'), input_gl=_gl_input('0'))
        self.assertEqual(result.net_vat, Decimal('0.00'))
        self.assertEqual(result.position, 'nil')
        self.assertTrue(result.reconciled, result.exceptions)

    def test_credit_note_reduces_output_vat(self):
        result = _recon(
            [_line('10000.00', '1400.00'),
             _line('-2000.00', '-280.00', Side.OUTPUT, reference='CN-1',
                   kind='credit_note')],
            output_gl=_gl_output('1120.00'), input_gl=_gl_input('0'),
        )
        self.assertEqual(result.output_vat, Decimal('1120.00'))
        self.assertEqual(result.output_net, Decimal('8000.00'))
        self.assertTrue(result.reconciled, result.exceptions)


# ---------------------------------------------------------------------------
# (d) The tie-out to the ledger, and the exceptions list
# ---------------------------------------------------------------------------

class LedgerTieOutTest(SimpleTestCase):
    databases = set()

    def test_agreeing_ledger_reconciles(self):
        result = _recon(
            [_line('10000.00', '1400.00')],
            output_gl=_gl_output('1400.00'), input_gl=_gl_input('0'),
        )
        self.assertEqual(result.output_difference, Decimal('0.00'))
        self.assertTrue(result.reconciled)
        self.assertEqual(result.exceptions, [])

    def test_a_one_thebe_difference_is_named_not_absorbed(self):
        # TIE_OUT_TOLERANCE is zero on purpose: a VAT control account either
        # agrees with the documents behind it or it does not.
        result = _recon(
            [_line('10000.00', '1400.00')],
            output_gl=_gl_output('1399.99'), input_gl=_gl_input('0'),
        )
        self.assertFalse(result.reconciled)
        self.assertEqual(result.output_difference, Decimal('0.01'))
        exc = [e for e in result.exceptions if e.code == ExceptionCode.TIE_OUTPUT]
        self.assertEqual(len(exc), 1)
        self.assertEqual(exc[0].difference, Decimal('0.01'))
        self.assertIn('209001', exc[0].reference)
        self.assertIn('posts nothing', exc[0].message)

    def test_input_side_ties_on_the_debit(self):
        result = _recon(
            [_line('5000.00', '700.00', Side.INPUT, reference='B1',
                   kind='vendor_bill')],
            output_gl=_gl_output('0'), input_gl=_gl_input('700.00'),
        )
        self.assertEqual(result.input_difference, Decimal('0.00'))
        self.assertTrue(result.reconciled, result.exceptions)

    def test_input_control_with_a_credit_reduces_the_movement(self):
        # A reversal on the input control account must reduce the recoverable
        # movement, not be ignored because it is on the "wrong" side.
        gl = [LedgerBalance(account_code='132000', account_name='VAT Input',
                            debit=Decimal('700.00'), credit=Decimal('100.00'))]
        result = _recon(
            [_line('4285.71', '600.00', Side.INPUT, reference='B1',
                   kind='vendor_bill')],
            output_gl=_gl_output('0'), input_gl=gl,
        )
        self.assertEqual(result.ledger_input_vat, Decimal('600.00'))
        self.assertEqual(result.input_difference, Decimal('0.00'))

    def test_missing_control_account_is_reported_never_assumed_to_agree(self):
        # The dangerous failure: no control account found, every difference
        # computes to zero, and the return looks perfectly reconciled.
        result = _recon([_line('10000.00', '1400.00')],
                        output_gl=[], input_gl=[])
        self.assertFalse(result.reconciled)
        codes = [e.code for e in result.exceptions]
        self.assertEqual(codes.count(ExceptionCode.NO_CONTROL), 2)


class LineExceptionsTest(SimpleTestCase):
    databases = set()

    def test_vat_not_equal_to_net_times_rate_is_flagged(self):
        result = _recon([_line('1000.00', '100.00')],
                        output_gl=_gl_output('100.00'), input_gl=_gl_input('0'))
        exc = [e for e in result.exceptions if e.code == ExceptionCode.RATE]
        self.assertEqual(len(exc), 1)
        self.assertEqual(exc[0].expected, Decimal('140.00'))
        self.assertEqual(exc[0].actual, Decimal('100.00'))
        self.assertEqual(exc[0].difference, Decimal('-40.00'))
        self.assertFalse(result.reconciled)

    def test_a_flagged_line_still_counts_towards_the_total(self):
        # "Nothing may be silently absorbed" cuts both ways: an exception must
        # not quietly drop the line out of the return either.
        result = _recon([_line('1000.00', '100.00')],
                        output_gl=_gl_output('100.00'), input_gl=_gl_input('0'))
        self.assertEqual(result.output_vat, Decimal('100.00'))
        self.assertEqual(result.output_net, Decimal('1000.00'))

    def test_one_thebe_of_line_rounding_is_tolerated(self):
        # 333.33 x 0.14 = 46.6662 -> 46.67. A document showing 46.66 is the
        # ordinary residue of rounding elsewhere, not a wrong rate.
        result = _recon([_line('333.33', '46.66')],
                        output_gl=_gl_output('46.66'), input_gl=_gl_input('0'))
        self.assertEqual([e.code for e in result.exceptions], [])

    def test_two_thebe_of_line_rounding_is_not_tolerated(self):
        result = _recon([_line('333.33', '46.69')],
                        output_gl=_gl_output('46.69'), input_gl=_gl_input('0'))
        self.assertIn(ExceptionCode.RATE, {e.code for e in result.exceptions})

    def test_zero_rated_line_is_not_rate_checked_but_still_counts(self):
        result = _recon([_line('5000.00', '0.00', rate_applies=False)],
                        output_gl=_gl_output('0'), input_gl=_gl_input('0'))
        self.assertEqual([e.code for e in result.exceptions], [])
        self.assertEqual(result.output_net, Decimal('5000.00'))
        self.assertEqual(result.output_vat, Decimal('0.00'))

    def test_vat_on_a_zero_net_is_flagged(self):
        result = _recon([_line('0.00', '140.00')],
                        output_gl=_gl_output('140.00'), input_gl=_gl_input('0'))
        self.assertIn(ExceptionCode.ORPHAN, {e.code for e in result.exceptions})

    def test_document_dated_outside_the_period_is_flagged(self):
        result = _recon([_line('1000.00', '140.00', doc_date=date(2026, 5, 31))],
                        output_gl=_gl_output('140.00'), input_gl=_gl_input('0'))
        self.assertIn(ExceptionCode.PERIOD, {e.code for e in result.exceptions})

    def test_unknown_side_is_flagged_rather_than_silently_dropped(self):
        result = _recon([_line('1000.00', '140.00', side='sideways')],
                        output_gl=_gl_output('0'), input_gl=_gl_input('0'))
        self.assertFalse(result.reconciled)
        self.assertTrue(any('sideways' in e.message for e in result.exceptions))

    def test_as_dict_carries_every_exception(self):
        result = _recon([_line('1000.00', '100.00'), _line('0.00', '5.00',
                                                           reference='INV-2')],
                        output_gl=[], input_gl=[])
        payload = result.as_dict()
        self.assertEqual(len(payload['exceptions']), len(result.exceptions))
        self.assertFalse(payload['tie_out']['reconciled'])


# ---------------------------------------------------------------------------
# (e) The dates — VAT is due the 25th, prepared by the 15th
# ---------------------------------------------------------------------------

class VatDueDatesTest(SimpleTestCase):
    databases = set()

    def test_due_the_twenty_fifth_of_the_following_month(self):
        # The CFO's correction of 2026-09-11: the 25th, not the 28th the
        # advisers quote.
        self.assertEqual(vat_due_date(date(2026, 6, 30)), date(2026, 7, 25))
        self.assertEqual(vat_due_date(date(2026, 2, 28)), date(2026, 3, 25))

    def test_december_period_rolls_into_january(self):
        self.assertEqual(vat_due_date(date(2026, 12, 31)), date(2027, 1, 25))

    def test_prepare_by_the_fifteenth(self):
        self.assertEqual(vat_prepare_by_date(date(2026, 6, 30)), date(2026, 7, 15))
        self.assertEqual(vat_prepare_by_date(date(2026, 12, 31)), date(2027, 1, 15))

    def test_the_result_carries_both_dates(self):
        result = _recon([], output_gl=_gl_output('0'), input_gl=_gl_input('0'))
        self.assertEqual(result.due_date, date(2026, 7, 25))
        self.assertEqual(result.prepare_by_date, date(2026, 7, 15))


# ---------------------------------------------------------------------------
# (f) The database path — the real gather, not a mock of it
# ---------------------------------------------------------------------------

@override_settings(
    RC_VAT_RATE=RATE_14,
    VAT_OUTPUT_CONTROL_ACCOUNTS=['209001'],
    VAT_INPUT_CONTROL_ACCOUNTS=['132000'],
)
class BuildVatReconciliationTest(TestCase):
    """Exercises regulatory/vat_recon_service.py against real rows."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        from billing.models import Contact, Invoice

        cls.user = User.objects.create_user('vatprep', 'vatprep@example.com', 'x')
        cls.customer = Contact.objects.create(name='Choppies', contact_type='customer')
        cls.supplier = Contact.objects.create(name='Sefalana', contact_type='supplier')

        Invoice.objects.create(
            invoice_number='INV-0001', invoice_type='customer_invoice',
            contact=cls.customer, issue_date=date(2026, 6, 10), status='posted',
            subtotal=Decimal('10000.00'), tax_total=Decimal('1400.00'),
            total_amount=Decimal('11400.00'), created_by=cls.user,
        )
        # A draft is not yet a supply and must not reach the return.
        Invoice.objects.create(
            invoice_number='INV-0002', invoice_type='customer_invoice',
            contact=cls.customer, issue_date=date(2026, 6, 12), status='draft',
            subtotal=Decimal('9999.00'), tax_total=Decimal('1399.86'),
            total_amount=Decimal('11398.86'), created_by=cls.user,
        )
        # Outside the period.
        Invoice.objects.create(
            invoice_number='INV-0003', invoice_type='customer_invoice',
            contact=cls.customer, issue_date=date(2026, 7, 1), status='posted',
            subtotal=Decimal('500.00'), tax_total=Decimal('70.00'),
            total_amount=Decimal('570.00'), created_by=cls.user,
        )
        Invoice.objects.create(
            invoice_number='BILL-0001', invoice_type='vendor_bill',
            contact=cls.supplier, issue_date=date(2026, 6, 20), status='posted',
            subtotal=Decimal('2000.00'), tax_total=Decimal('280.00'),
            total_amount=Decimal('2280.00'), created_by=cls.user,
        )

    def test_only_counted_documents_inside_the_period_reach_the_return(self):
        from regulatory.vat_recon_service import build_vat_reconciliation

        result = build_vat_reconciliation(P_START, P_END)
        refs = {line.reference for line in result.lines}
        self.assertIn('INV-0001', refs)
        self.assertIn('BILL-0001', refs)
        self.assertNotIn('INV-0002', refs, 'a draft invoice must not be on a VAT return')
        self.assertNotIn('INV-0003', refs, 'an invoice outside the period must not be on it')
        self.assertEqual(result.output_vat, Decimal('1400.00'))
        self.assertEqual(result.input_vat, Decimal('280.00'))
        self.assertEqual(result.net_vat, Decimal('1120.00'))
        self.assertEqual(result.position, 'payable')

    def test_no_control_accounts_in_the_chart_is_reported_not_assumed_clean(self):
        # Nothing seeded 209001/132000 in this test database, so the tie-out
        # cannot be done — and the report must say so rather than showing a
        # zero difference and a green tick.
        from regulatory.vat_recon_service import build_vat_reconciliation

        result = build_vat_reconciliation(P_START, P_END)
        self.assertFalse(result.reconciled)
        self.assertIn(ExceptionCode.NO_CONTROL, {e.code for e in result.exceptions})

    def test_the_service_reads_the_rate_from_settings(self):
        from regulatory.vat_recon_service import build_vat_reconciliation

        with override_settings(RC_VAT_RATE=RATE_15):
            result = build_vat_reconciliation(P_START, P_END)
        self.assertEqual(result.vat_rate, RATE_15)
        # The same untouched invoices now fail the rate check.
        self.assertIn(ExceptionCode.RATE, {e.code for e in result.exceptions})

    def test_month_period_helper(self):
        from regulatory.vat_recon_service import month_period

        self.assertEqual(month_period(2026, 6), (date(2026, 6, 1), date(2026, 6, 30)))
        self.assertEqual(month_period(2026, 2), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(month_period(2028, 2), (date(2028, 2, 1), date(2028, 2, 29)))


@override_settings(
    RC_VAT_RATE=RATE_14,
    VAT_OUTPUT_CONTROL_ACCOUNTS=['209001'],
    VAT_INPUT_CONTROL_ACCOUNTS=['132000'],
)
class LedgerMovementTieOutTest(TestCase):
    """The full tie-out against real posted journal lines."""

    def test_posted_ledger_movement_ties_to_the_documents(self):
        from django.contrib.auth.models import User

        from billing.models import Contact, Invoice
        from core.models import Currency
        from ledger.models import Account, JournalEntry, JournalEntryLine

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        from regulatory.vat_recon_service import build_vat_reconciliation

        user = User.objects.create_user('vat1', 'vat1@example.com', 'x')
        customer = Contact.objects.create(name='Trans', contact_type='customer')
        Invoice.objects.create(
            invoice_number='INV-9001', invoice_type='customer_invoice',
            contact=customer, issue_date=date(2026, 6, 5), status='posted',
            subtotal=Decimal('1000.00'), tax_total=Decimal('140.00'),
            total_amount=Decimal('1140.00'), created_by=user,
        )

        vat_out = Account.objects.create(
            code='209001', name='VAT', account_type='liability',
            sub_type='current_liability',
        )
        Account.objects.create(
            code='132000', name='VAT Input', account_type='asset',
            sub_type='current_asset',
        )
        revenue = Account.objects.create(
            code='400000', name='Revenue', account_type='revenue',
            sub_type='revenue',
        )

        je = JournalEntry.objects.create(
            entry_date=date(2026, 6, 5), description='VAT on INV-9001',
            status=JournalEntry.Status.POSTED, created_by=user,
            currency_code_id='BWP',
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=revenue,
            debit_amount=Decimal('140.00'), debit_bwp=Decimal('140.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=vat_out,
            credit_amount=Decimal('140.00'), credit_bwp=Decimal('140.00'),
        )

        result = build_vat_reconciliation(P_START, P_END)
        self.assertEqual(result.output_vat, Decimal('140.00'))
        self.assertEqual(result.ledger_output_vat, Decimal('140.00'))
        self.assertEqual(result.output_difference, Decimal('0.00'))
        self.assertTrue(result.reconciled, result.exceptions)

    def test_an_unposted_journal_is_not_counted_and_the_difference_is_named(self):
        from django.contrib.auth.models import User

        from billing.models import Contact, Invoice
        from core.models import Currency
        from ledger.models import Account, JournalEntry, JournalEntryLine

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        from regulatory.vat_recon_service import build_vat_reconciliation

        user = User.objects.create_user('vat2', 'vat2@example.com', 'x')
        customer = Contact.objects.create(name='Yash Cell', contact_type='customer')
        Invoice.objects.create(
            invoice_number='INV-9002', invoice_type='customer_invoice',
            contact=customer, issue_date=date(2026, 6, 5), status='posted',
            subtotal=Decimal('1000.00'), tax_total=Decimal('140.00'),
            total_amount=Decimal('1140.00'), created_by=user,
        )
        vat_out = Account.objects.create(
            code='209001', name='VAT', account_type='liability',
            sub_type='current_liability',
        )
        Account.objects.create(
            code='132000', name='VAT Input', account_type='asset',
            sub_type='current_asset',
        )
        revenue = Account.objects.create(
            code='400000', name='Revenue', account_type='revenue',
            sub_type='revenue',
        )
        je = JournalEntry.objects.create(
            entry_date=date(2026, 6, 5), description='Not yet posted',
            status=JournalEntry.Status.DRAFT, created_by=user,
            currency_code_id='BWP',
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=revenue,
            debit_amount=Decimal('140.00'), debit_bwp=Decimal('140.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=vat_out,
            credit_amount=Decimal('140.00'), credit_bwp=Decimal('140.00'),
        )

        result = build_vat_reconciliation(P_START, P_END)
        self.assertEqual(result.ledger_output_vat, Decimal('0.00'))
        self.assertEqual(result.output_difference, Decimal('140.00'))
        self.assertFalse(result.reconciled)
        exc = [e for e in result.exceptions if e.code == ExceptionCode.TIE_OUTPUT]
        self.assertEqual(len(exc), 1)
        self.assertEqual(exc[0].difference, Decimal('140.00'))

    @override_settings(
        RC_VAT_RATE=RATE_14,
        VAT_OUTPUT_CONTROL_ACCOUNTS=['209001', '2180'],
        VAT_INPUT_CONTROL_ACCOUNTS=['132000'],
    )
    def test_a_configured_control_account_missing_from_the_chart_is_named(self):
        """A configured code that is not in the chart must not be skipped in silence.

        An absent account contributes zero movement, which is indistinguishable
        from a real nil month — so without this the tie-out would report a
        difference and give the preparer no way to tell that the cause was the
        configuration rather than the books.
        """
        from django.contrib.auth.models import User

        from billing.models import Contact, Invoice
        from core.models import Currency
        from ledger.models import Account, JournalEntry, JournalEntryLine

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        from regulatory.vat_recon_service import build_vat_reconciliation

        user = User.objects.create_user('vat3', 'vat3@example.com', 'x')
        customer = Contact.objects.create(name='Kgalagadi', contact_type='customer')
        Invoice.objects.create(
            invoice_number='INV-9003', invoice_type='customer_invoice',
            contact=customer, issue_date=date(2026, 6, 5), status='posted',
            subtotal=Decimal('1000.00'), tax_total=Decimal('140.00'),
            total_amount=Decimal('1140.00'), created_by=user,
        )
        # 209001 exists and carries the movement; 2180 is configured but absent.
        vat_out = Account.objects.create(
            code='209001', name='VAT', account_type='liability',
            sub_type='current_liability',
        )
        Account.objects.create(
            code='132000', name='VAT Input', account_type='asset',
            sub_type='current_asset',
        )
        revenue = Account.objects.create(
            code='400000', name='Revenue', account_type='revenue',
            sub_type='revenue',
        )
        je = JournalEntry.objects.create(
            entry_date=date(2026, 6, 5), description='VAT on INV-9003',
            status=JournalEntry.Status.POSTED, created_by=user,
            currency_code_id='BWP',
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=revenue,
            debit_amount=Decimal('140.00'), debit_bwp=Decimal('140.00'),
        )
        JournalEntryLine.objects.create(
            journal_entry=je, account=vat_out,
            credit_amount=Decimal('140.00'), credit_bwp=Decimal('140.00'),
        )

        result = build_vat_reconciliation(P_START, P_END)

        missing = [e for e in result.exceptions
                   if e.code == ExceptionCode.MISSING_CONTROL]
        self.assertEqual([e.reference for e in missing], ['2180'])
        self.assertEqual(missing[0].severity, 'warning')
        self.assertIn('not in the chart of accounts', missing[0].message)
        # The account that DOES exist still ties, and a warning is not an error.
        self.assertEqual(result.ledger_output_vat, Decimal('140.00'))
        self.assertEqual(result.output_difference, Decimal('0.00'))
        self.assertTrue(result.reconciled, result.exceptions)


class OneDefinitionOfTheControlAccountsTest(TestCase):
    """CFO 2026-09-14: the reconciliation must tie back to the SAME accounts
    the VAT return uses, and the two must never be able to disagree.

    `reporting.build_vat_return` reads no GL account at all — it is built from
    the Invoice / ReverseChargeEntry sub-ledgers. So the only code that decides
    which account VAT is IN is the posting in `billing/models.py`. There was no
    single place to reuse, so one was made: `ledger/vat_accounts.py`. These
    tests are the gate that keeps it single — they fail the moment someone
    reintroduces a second VAT constant.
    """

    def test_what_posts_is_what_the_reconciliation_reads(self):
        from django.conf import settings

        from ledger.vat_accounts import (
            VAT_INPUT_ACCOUNT, VAT_OUTPUT_ACCOUNT,
        )

        self.assertIn(VAT_OUTPUT_ACCOUNT, settings.VAT_OUTPUT_CONTROL_ACCOUNTS)
        self.assertIn(VAT_INPUT_ACCOUNT, settings.VAT_INPUT_CONTROL_ACCOUNTS)

    def test_billing_posts_through_the_shared_definition_not_a_literal(self):
        """The posting site must READ the constant, not repeat the digits.

        Asserting only that the values are equal would pass while the code
        still held two copies that a later edit could separate. This reads the
        source and proves the literal is gone.
        """
        import io
        import pathlib

        import billing.models as billing_models
        from ledger.vat_accounts import (
            VAT_INPUT_ACCOUNT, VAT_OUTPUT_ACCOUNT,
        )

        src = io.open(pathlib.Path(billing_models.__file__),
                      encoding='utf-8').read()
        for literal in (VAT_OUTPUT_ACCOUNT, VAT_INPUT_ACCOUNT):
            # BOTH quote styles — checking only one would let a double-quoted
            # literal walk straight past the gate that exists to forbid it.
            for spelling in (f"get_acct('{literal}')",
                             f'get_acct("{literal}")'):
                self.assertNotIn(
                    spelling, src,
                    f'billing/models.py still hard-codes {literal!r}; it must '
                    f'post through ledger.vat_accounts so the reconciliation '
                    f'cannot drift from it.')
        self.assertIn('from ledger.vat_accounts import', src)

    def test_a_customer_invoice_posts_VAT_to_the_account_that_is_read(self):
        """End to end, not by inspection: post a real invoice and find the VAT
        on the very account the reconciliation gathers movement from."""
        from django.conf import settings
        from django.contrib.auth.models import User

        from billing.models import Contact, Invoice, InvoiceLine
        from core.models import Currency, TaxRate
        from ledger.models import Account, FiscalPeriod, JournalEntryLine
        from ledger.vat_accounts import VAT_OUTPUT_ACCOUNT

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        # Posting checks the issue date against an OPEN fiscal period — without
        # one this fails on the period check before it ever reaches VAT.
        FiscalPeriod.objects.get_or_create(
            period_name='2026-06',
            defaults={'start_date': date(2026, 6, 1),
                      'end_date': date(2026, 6, 30),
                      'status': FiscalPeriod.Status.OPEN})
        # InvoiceLine.tax_code is NOT NULL. Take the rate from settings rather
        # than typing 14 — this repo has exactly one VAT rate constant.
        vat_code, _ = TaxRate.objects.get_or_create(
            tax_code='VAT14',
            defaults={'name': 'Botswana VAT',
                      'rate': Decimal(str(settings.RC_VAT_RATE)) * 100,
                      'effective_from': date(2026, 1, 1)})
        # Direct posting from DRAFT needs elevated authority (ledger/models.py
        # JournalEntry.post) — a superuser clears that gate; this test is
        # about where the VAT lands, not about the approval workflow.
        user = User.objects.create_user('vat4', 'vat4@example.com', 'x',
                                        is_superuser=True)
        for code, name, atype, sub in (
            ('1210', 'Premium receivable', 'asset', 'current_asset'),
            (VAT_OUTPUT_ACCOUNT, 'VAT output payable', 'liability',
             'current_liability'),
            ('400000', 'Revenue', 'revenue', 'revenue'),
        ):
            Account.objects.get_or_create(
                code=code, defaults={'name': name, 'account_type': atype,
                                     'sub_type': sub})
        revenue = Account.objects.get(code='400000')
        customer = Contact.objects.create(name='Kgalagadi',
                                          contact_type='customer')
        inv = Invoice.objects.create(
            invoice_number='INV-9100', invoice_type='customer_invoice',
            contact=customer, issue_date=date(2026, 6, 5), status='draft',
            subtotal=Decimal('1000.00'), tax_total=Decimal('140.00'),
            total_amount=Decimal('1140.00'), created_by=user,
        )
        InvoiceLine.objects.create(
            invoice=inv, description='Premium', account=revenue,
            tax_code=vat_code,
            quantity=Decimal('1'), unit_price=Decimal('1000.00'),
            line_total=Decimal('1000.00'),
        )
        inv.post(user)

        vat_line = JournalEntryLine.objects.get(
            account__code=VAT_OUTPUT_ACCOUNT)
        # 14% of 1,000.00, and it landed on a configured control account.
        self.assertEqual(vat_line.credit_amount, Decimal('140.00'))
        self.assertIn(vat_line.account.code,
                      settings.VAT_OUTPUT_CONTROL_ACCOUNTS)


class MonthlyCadenceAndTheTwentyFifthTest(SimpleTestCase):
    """It runs MONTHLY, and it must be ready in good time before the 25th."""

    databases = set()

    def test_every_month_of_a_year_is_due_on_the_25th_of_the_next(self):
        import calendar

        for month in range(1, 13):
            last = calendar.monthrange(2026, month)[1]
            period_end = date(2026, month, last)
            due = vat_due_date(period_end)
            self.assertEqual(due.day, 25, f'month {month} is not due on the 25th')
            # The month AFTER the period — December rolls into the new year.
            expected_year = 2027 if month == 12 else 2026
            expected_month = 1 if month == 12 else month + 1
            self.assertEqual((due.year, due.month),
                             (expected_year, expected_month))

    def test_the_report_is_ready_ten_days_before_it_is_due(self):
        for month in range(1, 13):
            import calendar
            last = calendar.monthrange(2026, month)[1]
            period_end = date(2026, month, last)
            prepare = vat_prepare_by_date(period_end)
            due = vat_due_date(period_end)
            self.assertEqual(prepare.day, 15)
            self.assertLess(prepare, due)
            self.assertEqual((due - prepare).days, 10)
