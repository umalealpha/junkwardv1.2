"""Tests for the six Finance monitoring reports.

The point of most of these is the FIX, not the happy path. Each of the first
three fails if the specific defect that produced a wrong number on Keetile's
real file comes back.
"""

import datetime as _dt
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from realpay.recon import _norm_contract, _parse_date, reconcile
from reporting import finance_monitoring as fm


class ExcelSerialDateTests(SimpleTestCase):
    """Keetile's export is .xlsb, so every date arrives as a serial number.

    Before the fix all 59,448 of his rows parsed to None and the file could not
    be reported by period at all.
    """

    def test_excel_serial_becomes_a_real_date(self):
        # 46251.47694444445 is a real InstalmentDate cell from his file: the
        # 17 August 2026 batch. The fraction is the time of day and is dropped.
        self.assertEqual(_parse_date('46251.47694444445'), _dt.date(2026, 8, 17))

    def test_serial_as_float_not_only_string(self):
        self.assertEqual(_parse_date(46251.47694444445), _dt.date(2026, 8, 17))

    def test_the_other_end_of_his_file(self):
        """1 June 2026, the first day in the export."""
        self.assertEqual(_parse_date('46174'), _dt.date(2026, 6, 1))

    def test_text_dates_still_work(self):
        self.assertEqual(_parse_date('2026/08/01 09:00'), _dt.date(2026, 8, 1))
        self.assertEqual(_parse_date('2026-08-01'), _dt.date(2026, 8, 1))

    def test_an_amount_is_not_read_as_a_date(self):
        """A money column must never be mistaken for a serial date."""
        self.assertIsNone(_parse_date('250000.00'))
        self.assertIsNone(_parse_date(''))
        self.assertIsNone(_parse_date('not a date'))


def _row(policy, contract, collected, installment, status='SUCCESSFUL',
         when=_dt.date(2026, 6, 1)):
    return {
        'contract_number': _norm_contract(contract),
        'contract_raw': contract,
        'client_number': policy,
        'policy_number': _norm_contract(policy),
        'merchant': 'Alpha Direct Instant Insurance',
        'client_name': 'REDACTED',
        'txn_date': when,
        'current_status': status,
        'result_code': '00',
        'collected': Decimal(str(collected)),
        'installment': Decimal(str(installment)),
    }


class JoinKeyTests(SimpleTestCase):
    """The join is ClientNumber → policyNumber, NOT ContractNumber.

    Joining on ContractNumber put 95% of a real file (P5.68m) into "we debited a
    policy Graphite has never heard of". This test fails if that returns.
    """

    def test_joins_on_client_number_not_contract_number(self):
        rows = [_row('DOM2025123456', '189299/1', 500, 500)]
        graphite = {'DOM2025123456': {
            'policy_number': 'DOM2025123456', 'status': '1', 'active': True,
            'term_end_date': None, 'premium': Decimal('500.00'),
            'annual_premium': Decimal('6000.00')}}
        out = reconcile(rows, graphite=graphite, graphite_available=True)
        self.assertEqual(out['counts']['matched'], 1)
        self.assertEqual(out['counts']['only_in_realpay'], 0)

    def test_contract_number_alone_does_not_match(self):
        """Guard the inverse: keying on the RealPay contract must NOT match."""
        rows = [_row('DOM2025123456', '189299/1', 500, 500)]
        graphite = {'1892991': {
            'policy_number': '1892991', 'status': '1', 'active': True,
            'term_end_date': None, 'premium': Decimal('500.00'),
            'annual_premium': Decimal('0')}}
        out = reconcile(rows, graphite=graphite, graphite_available=True)
        self.assertEqual(out['counts']['only_in_realpay'], 1)


class PerInstalmentComparisonTests(SimpleTestCase):
    """Three months of debits must not be set against one monthly premium.

    That defect reported P4.06m collected against P1.07m expected on the real
    file — a 3.8x gap that was only ever the number of months in the export.
    """

    def test_three_months_of_debits_agree_with_a_monthly_premium(self):
        rows = [_row('DOM2025123456', '189299/1', 500, 500,
                     when=_dt.date(2026, m, 1)) for m in (6, 7, 8)]
        graphite = {'DOM2025123456': {
            'policy_number': 'DOM2025123456', 'status': '1', 'active': True,
            'term_end_date': None, 'premium': Decimal('500.00'),
            'annual_premium': Decimal('6000.00')}}
        out = reconcile(rows, graphite=graphite, graphite_available=True)
        self.assertEqual(out['counts']['matched'], 1)
        self.assertEqual(out['counts']['amount_differs'], 0)
        # The file total is still reported in full — it is just not the thing
        # compared against the premium.
        self.assertEqual(out['totals']['matched'], '1500.00')

    def test_a_real_instalment_break_is_still_caught(self):
        rows = [_row('DOM2025123456', '189299/1', 450, 450)]
        graphite = {'DOM2025123456': {
            'policy_number': 'DOM2025123456', 'status': '1', 'active': True,
            'term_end_date': None, 'premium': Decimal('500.00'),
            'annual_premium': Decimal('0')}}
        out = reconcile(rows, graphite=graphite, graphite_available=True)
        self.assertEqual(out['counts']['amount_differs'], 1)
        self.assertEqual(out['amount_differs'][0]['difference'], '-50.00')

    def test_debit_on_a_cancelled_policy_is_flagged(self):
        rows = [_row('DOM2025123456', '189299/1', 500, 500)]
        graphite = {'DOM2025123456': {
            'policy_number': 'DOM2025123456', 'status': '2', 'active': False,
            'term_end_date': None, 'premium': Decimal('500.00'),
            'annual_premium': Decimal('0')}}
        out = reconcile(rows, graphite=graphite, graphite_available=True)
        self.assertEqual(out['totals']['inactive_policies'], 1)
        self.assertTrue(out['matched'][0]['inactive_policy'])


class ReplicaDownTests(SimpleTestCase):
    """A dead connection must never render as a clean report."""

    def test_builder_raises_rather_than_returning_no_rows(self):
        with patch('integrations.graphite_ro.is_configured', return_value=False):
            with self.assertRaises(fm.ReplicaUnavailable):
                fm.build_failed_debits()

    def test_query_failure_is_not_swallowed(self):
        with patch('integrations.graphite_ro.is_configured', return_value=True), \
             patch('integrations.graphite_ro.query', side_effect=OSError('replica gone')):
            with self.assertRaises(fm.ReplicaUnavailable):
                fm.build_policy_status_integrity()


class StatusLabelTests(SimpleTestCase):
    """The codes are Graphite's. Mislabelling them mislabels real money."""

    def test_policy_status_labels(self):
        self.assertEqual(fm.POLICY_STATUS_LABEL['1'], 'Active')
        self.assertEqual(fm.POLICY_STATUS_LABEL['2'], 'Cancelled')
        self.assertEqual(fm.POLICY_STATUS_LABEL['0'], 'Lapsed / inactive')

    def test_instalment_status_labels(self):
        self.assertEqual(fm.INSTALMENT_STATUS_LABEL['S'], 'Success')
        self.assertEqual(fm.INSTALMENT_STATUS_LABEL['F'], 'Failed')
        self.assertEqual(fm.INSTALMENT_STATUS_LABEL['A'], 'Scheduled')
        self.assertEqual(fm.INSTALMENT_STATUS_LABEL['I'], 'Cancelled')

    def test_failed_statuses_include_error(self):
        self.assertIn('F', fm.FAILED_STATUSES)
        self.assertIn('E', fm.FAILED_STATUSES)
        self.assertNotIn('A', fm.FAILED_STATUSES)
        self.assertNotIn('S', fm.FAILED_STATUSES)


class BuilderShapeTests(SimpleTestCase):
    """Every builder returns the {rows, summary, meta} shape the views expect."""

    def test_all_reports_are_registered_and_callable(self):
        self.assertEqual(len(fm.REPORTS), 7)
        for slug, cfg in fm.REPORTS.items():
            self.assertTrue(callable(cfg['builder']), slug)
            self.assertTrue(cfg['title'], slug)
            self.assertTrue(cfg['cadence'], slug)

    def test_collections_builder_shape(self):
        rows = [{'policy_number': 'DOM1', 'policy_status': '2', 'premium': 500,
                 'debits': 2, 'collected': Decimal('1000.00'),
                 'last_debit': '2026-08-01'}]
        with patch.object(fm, '_q', return_value=rows):
            out = fm.build_collections_vs_graphite()
        self.assertEqual(out['summary']['cancelled_policies'], 1)
        self.assertEqual(out['summary']['cancelled_amount'], 1000.0)
        self.assertEqual(out['rows'][0]['severity'], 'critical')
        self.assertEqual(out['rows'][0]['policy_status'], 'Cancelled')

    def test_expired_term_guard_excludes_year_25(self):
        """term_end_date holds values like 0025-03-14; they are not expiries."""
        import inspect
        src = inspect.getsource(fm.build_policy_status_integrity)
        self.assertIn("term_end_date > '2000-01-01'", src)


class PaymentsVsBankTests(TestCase):
    """The bank half must be absent, not zero, when no statement is loaded."""

    def test_no_bank_data_is_not_reported_as_a_break(self):
        rows = [{'day': _dt.date(2026, 8, 3), 'receipts': 10,
                 'amount': Decimal('5000.00')}]
        with patch.object(fm, '_q', return_value=rows), \
             patch.object(fm, '_bank_totals_by_day', return_value={}):
            out = fm.build_payments_vs_bank()
        self.assertIsNone(out['rows'][0]['bank_amount'])
        self.assertIsNone(out['rows'][0]['difference'])
        self.assertEqual(out['rows'][0]['severity'], 'unknown')
        self.assertEqual(out['summary']['days_with_no_bank_data'], 1)

    def test_agreeing_day_is_marked_ok(self):
        rows = [{'day': _dt.date(2026, 8, 3), 'receipts': 10,
                 'amount': Decimal('5000.00')}]
        with patch.object(fm, '_q', return_value=rows), \
             patch.object(fm, '_bank_totals_by_day',
                          return_value={'2026-08-03': Decimal('5000.00')}):
            out = fm.build_payments_vs_bank()
        self.assertEqual(out['rows'][0]['severity'], 'ok')
        self.assertEqual(out['summary']['days_agreeing'], 1)


class RefundMatchTests(SimpleTestCase):
    def test_amount_matches_to_the_thebe(self):
        self.assertTrue(fm._matches_omni_refund([Decimal('100.00')], Decimal('100.00')))
        self.assertTrue(fm._matches_omni_refund([Decimal('100.005')], Decimal('100.00')))
        self.assertFalse(fm._matches_omni_refund([Decimal('100.50')], Decimal('100.00')))
        self.assertFalse(fm._matches_omni_refund(None, Decimal('100.00')))
        self.assertFalse(fm._matches_omni_refund([], Decimal('100.00')))


class RowCapTests(SimpleTestCase):
    """A capped read must never present its partial total as the whole answer.

    Report 1 finds 5,824 exception policies on real data against a default cap
    of 1,000, so an unflagged cap prints a confidently wrong smaller headline.
    """

    def test_hitting_the_cap_is_declared(self):
        rows = [{'policy_number': f'DOM{i}', 'policy_status': '2', 'premium': 500,
                 'debits': 1, 'collected': Decimal('100.00'), 'last_debit': ''}
                for i in range(10)]
        with patch.object(fm, '_q', return_value=rows):
            out = fm.build_collections_vs_graphite(limit=10)
        self.assertEqual(out['summary']['truncated'], 1)
        self.assertEqual(out['summary']['row_cap'], 10)
        self.assertIn('row cap', out['meta']['note'])

    def test_under_the_cap_says_nothing(self):
        rows = [{'policy_number': 'DOM1', 'policy_status': '2', 'premium': 500,
                 'debits': 1, 'collected': Decimal('100.00'), 'last_debit': ''}]
        with patch.object(fm, '_q', return_value=rows):
            out = fm.build_collections_vs_graphite(limit=10)
        self.assertNotIn('truncated', out['summary'])
        self.assertNotIn('row cap', out['meta']['note'])


class RefundConsumptionTests(SimpleTestCase):
    """One Omni refund can only account for ONE Graphite refund.

    Without consumption a customer refunded P500 twice shows both as posted on
    the strength of a single Omni record, and the second — genuinely unpaid —
    never reaches the chase list.
    """

    def test_a_single_omni_refund_covers_only_one_of_two(self):
        pot = [Decimal('500.00')]
        self.assertTrue(fm._matches_omni_refund(pot, Decimal('500.00')))
        self.assertFalse(fm._matches_omni_refund(pot, Decimal('500.00')))

    def test_two_omni_refunds_cover_two(self):
        pot = [Decimal('500.00'), Decimal('500.00')]
        self.assertTrue(fm._matches_omni_refund(pot, Decimal('500.00')))
        self.assertTrue(fm._matches_omni_refund(pot, Decimal('500.00')))
        self.assertFalse(fm._matches_omni_refund(pot, Decimal('500.00')))

    def test_a_different_amount_does_not_consume(self):
        pot = [Decimal('500.00')]
        self.assertFalse(fm._matches_omni_refund(pot, Decimal('250.00')))
        self.assertEqual(len(pot), 1)


class BankDeduplicationTests(TestCase):
    """The bank feed re-imports; identical lines must count once.

    Checked on prod 18-Aug-2026: 7,782 of 9,498 money-in lines are surplus
    copies and NOT ONE is marked excluded. Summing the raw column inflates the
    bank side several times over and fakes a break on every day of the week.

    This builds real rows in the database rather than mocking the queryset —
    mocking `.distinct()` would only assert that the mock was called, which is
    exactly the kind of test that passes while the money is wrong.
    """

    def setUp(self):
        from banking.models import BankAccount, BankStatement, BankStatementLine
        from ledger.models import Account, Currency
        self.LineModel = BankStatementLine

        currency, _ = Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        gl = Account.objects.create(
            code='2801-TEST', name='Bank control (test)', account_type='asset')
        account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Current',
            account_number='123456', currency_code=currency)
        self.statement = BankStatement.objects.create(
            statement_number='ST-TEST-1', bank_account=account,
            statement_date=_dt.date(2026, 8, 3),
            opening_balance=Decimal('0.00'), closing_balance=Decimal('0.00'),
            file_name='test.csv')

    def _line(self, n, amount, description, reference, occurrence=0):
        # `occurrence` mirrors what banking.models.compute_line_dedupe_key does
        # for content that legitimately repeats. Since the no-double-import
        # guard (2026-09-12) the database REFUSES two lines with identical
        # content and the same occurrence, so byte-identical rows can only
        # exist the way the backfill migration left them: same content,
        # ascending occurrence. That is exactly the legacy state this suite
        # exercises - reporting must still count such a group once.
        from banking.models import compute_line_dedupe_key
        tx_date = _dt.date(2026, 8, 3)
        return self.LineModel.objects.create(
            statement=self.statement, line_number=n,
            transaction_date=tx_date, description=description,
            reference=reference, amount=Decimal(amount),
            dedupe_key=compute_line_dedupe_key(
                self.statement.bank_account_id, tx_date, Decimal(amount),
                description, reference, occurrence),
            match_status=self.LineModel.MatchStatus.UNMATCHED)

    def test_a_re_imported_line_counts_once(self):
        """Three copies of one debit-order batch are one deposit, not three."""
        for i in range(3):
            self._line(i + 1, '100.00', 'DEBIT ORDER BATCH', 'REF1', occurrence=i)
        out = fm._bank_totals_by_day(_dt.date(2026, 8, 3), _dt.date(2026, 8, 3))
        self.assertEqual(out['2026-08-03'], Decimal('100.00'))

    def test_genuinely_different_lines_both_count(self):
        """De-duplication must not swallow two real, distinct deposits."""
        self._line(1, '100.00', 'DEBIT ORDER BATCH', 'REF1')
        self._line(2, '100.00', 'DEBIT ORDER BATCH', 'REF2')
        out = fm._bank_totals_by_day(_dt.date(2026, 8, 3), _dt.date(2026, 8, 3))
        self.assertEqual(out['2026-08-03'], Decimal('200.00'))

    def test_excluded_lines_are_left_out(self):
        line = self._line(1, '100.00', 'DEBIT ORDER BATCH', 'REF1')
        line.match_status = self.LineModel.MatchStatus.EXCLUDED
        line.save(update_fields=['match_status'])
        out = fm._bank_totals_by_day(_dt.date(2026, 8, 3), _dt.date(2026, 8, 3))
        self.assertEqual(out, {})
