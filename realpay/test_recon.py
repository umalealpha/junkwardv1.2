"""Tests for the RealPay-vs-Graphite reconciliation.

The buckets are the whole feature — if a contract lands in the wrong one, a
person chases money that was never missing, or misses money that was. Graphite
is injected so these run without a replica: the logic is what is worth pinning,
and a live read would make the test slow and flaky.

Synthetic contract numbers only — no customer data.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from realpay.recon import _dec, _norm_contract, parse_realpay_file, reconcile


def _row(contract, collected, *, status='SUCCESSFUL', name='', installment=None):
    return {
        'contract_number': _norm_contract(contract),
        'contract_raw':    contract,
        'client_number':   '',
        'client_name':     name,
        'txn_date':        None,
        'current_status':  status,
        'result_code':     '',
        'collected':       Decimal(str(collected)),
        'installment':     Decimal(str(installment if installment is not None else collected)),
    }


def _g(premium, status='ACTIVE', policy='COMG2025189299'):
    return {'policy_number': policy, 'status': status, 'premium': Decimal(str(premium))}


class ContractNormalisation(SimpleTestCase):
    def test_spacing_and_punctuation_do_not_make_two_contracts(self):
        """RealPay writes 'COMG 2025 189299', Graphite writes 'COMG2025189299'.
        A raw compare reports a false break on every single row."""
        self.assertEqual(_norm_contract('COMG 2025 189299'), 'COMG2025189299')
        self.assertEqual(_norm_contract('comg-2025-189299'), 'COMG2025189299')
        self.assertEqual(_norm_contract('  COMG2025189299 '), 'COMG2025189299')

    def test_blank_stays_blank(self):
        self.assertEqual(_norm_contract(None), '')
        self.assertEqual(_norm_contract('   '), '')


class MoneyParsing(SimpleTestCase):
    def test_thousands_commas_and_currency_marks(self):
        self.assertEqual(_dec('1,077.62'), Decimal('1077.62'))
        self.assertEqual(_dec('P 1,077.62'), Decimal('1077.62'))

    def test_both_reversal_notations_are_negative(self):
        """A reversal read as a positive would overstate collections — the one
        error that makes the recon worse than doing nothing."""
        self.assertEqual(_dec('(500.00)'), Decimal('-500.00'))
        self.assertEqual(_dec('500.00-'), Decimal('-500.00'))

    def test_blanks_are_zero_not_a_crash(self):
        for blank in ('', '  ', None, '-', 'NULL', 'nan'):
            self.assertEqual(_dec(blank), Decimal('0.00'))


class Buckets(SimpleTestCase):
    def test_equal_amounts_match(self):
        out = reconcile([_row('C1', '500.00')], graphite={'C1': _g('500.00')})
        self.assertEqual(out['counts']['matched'], 1)
        self.assertEqual(out['counts']['amount_differs'], 0)

    def test_a_difference_over_a_thebe_is_a_break(self):
        out = reconcile([_row('C1', '450.00')], graphite={'C1': _g('500.00')})
        self.assertEqual(out['counts']['amount_differs'], 1)
        self.assertEqual(out['amount_differs'][0]['difference'], '-50.00')

    def test_a_difference_under_a_thebe_is_not_a_break(self):
        """Both systems hold 2dp. Flagging float noise trains people to ignore
        the report, which is how a real break gets missed."""
        out = reconcile([_row('C1', '500.005')], graphite={'C1': _g('500.00')})
        self.assertEqual(out['counts']['amount_differs'], 0)

    def test_collected_with_no_policy_is_the_dangerous_bucket(self):
        """Money taken for a contract the policy system does not know about —
        it may have lapsed or been cancelled. Must never be silently matched."""
        out = reconcile([_row('GHOST', '300.00')], graphite={'C1': _g('500.00')})
        self.assertEqual(out['counts']['only_in_realpay'], 1)
        self.assertEqual(out['totals']['only_realpay'], '300.00')

    def test_missed_debits_are_declared_unchecked_not_reported_as_zero(self):
        """Fable review 18-Aug-2026. The old test asserted this bucket found a
        missed debit — but it only passed because it INJECTED a Graphite row the
        production query could never return (_graphite_side filters to contracts
        that are in the upload, so a policy absent from the file is unreachable
        by construction). A bucket that always reads zero in production, with a
        green test above it, is worse than no bucket: silence reads as 'nothing
        missing'. It is now explicitly flagged as not checked."""
        out = reconcile([_row('C1', '500.00')],
                        graphite={'C1': _g('500.00'), 'C2': _g('750.00')})
        self.assertFalse(out['only_in_graphite_checked'])
        self.assertEqual(out['counts']['only_in_graphite'], 0)

    def test_several_instalments_on_one_contract_sum_before_comparing(self):
        """Two instalments of 250 against a 500 premium is agreement, not a
        break. Comparing row-by-row would flag every instalment contract."""
        out = reconcile([_row('C1', '250.00'), _row('C1', '250.00')],
                        graphite={'C1': _g('500.00')})
        self.assertEqual(out['counts']['matched'], 1)
        self.assertEqual(out['counts']['realpay_contracts'], 1)
        self.assertEqual(out['matched'][0]['lines'], 2)

    def test_a_retry_after_a_failure_counts_once_as_a_contract(self):
        out = reconcile([_row('C1', '0.00', status='FAILED'),
                         _row('C1', '500.00', status='SUCCESSFUL')],
                        graphite={'C1': _g('500.00')})
        self.assertEqual(out['counts']['matched'], 1)
        item = out['matched'][0]
        self.assertEqual(item['failed'], 1)
        self.assertEqual(item['successful'], 1)

    def test_a_reversal_reduces_the_collected_total(self):
        out = reconcile([_row('C1', '500.00'), _row('C1', '-500.00')],
                        graphite={'C1': _g('0.00')})
        self.assertEqual(out['matched'][0]['collected'], '0.00')


class GraphiteUnavailable(SimpleTestCase):
    def test_no_graphite_never_reports_everything_as_missing(self):
        """If the replica is down, calling every RealPay row 'no policy found'
        would put a fabricated crisis on the CFO's screen. It must say plainly
        that it could not compare."""
        out = reconcile([_row('C1', '500.00'), _row('C2', '100.00')],
                        graphite={}, graphite_available=False)
        self.assertFalse(out['graphite_available'])
        self.assertEqual(out['counts']['only_in_realpay'], 0)
        self.assertEqual(out['counts']['only_in_graphite'], 0)

    def test_unread_rows_are_unverified_not_agreed(self):
        """Fable review 18-Aug-2026: these were appended to `matched`, so the
        'Agrees' tile asserted an agreement that was never tested. The one thing
        worse than no answer is a confident wrong one."""
        out = reconcile([_row('C1', '500.00'), _row('C2', '100.00')],
                        graphite={}, graphite_available=False)
        self.assertEqual(out['counts']['matched'], 0)
        self.assertEqual(out['counts']['unverified'], 2)
        self.assertEqual(out['totals']['unverified'], '600.00')

    def test_an_empty_but_working_replica_is_not_the_same_as_a_down_one(self):
        """The dangerous case the red bucket exists for: the replica answered
        and has never heard of ANY contract in the file. Inferring availability
        from `bool(graphite)` rendered that crisis as a bland 'not reachable'."""
        out = reconcile([_row('GHOST1', '300.00'), _row('GHOST2', '200.00')],
                        graphite={}, graphite_available=True)
        self.assertTrue(out['graphite_available'])
        self.assertEqual(out['counts']['only_in_realpay'], 2)
        self.assertEqual(out['totals']['only_realpay'], '500.00')

    def test_the_headline_total_ties_to_the_file_in_every_state(self):
        """Whatever bucket rows land in, 'collected' must equal the export the
        reader is holding — including when the replica is down."""
        rows = [_row('C1', '500.00'), _row('C2', '100.00')]
        down = reconcile(rows, graphite={}, graphite_available=False)
        up   = reconcile(rows, graphite={'C1': _g('500.00')}, graphite_available=True)
        self.assertEqual(down['totals']['collected'], '600.00')
        self.assertEqual(up['totals']['collected'], '600.00')


class Parsing(SimpleTestCase):
    def _csv(self, body):
        return parse_realpay_file(body.encode('utf-8'), 'transaction report.csv')

    def test_reads_a_transaction_report_csv(self):
        out = self._csv(
            'Installment Date,ClientNumber,ClientName,ContractNumber,'
            'InstallmentAmount,Collected Amount,Current Status,Result\n'
            '2026/08/01 09:00,1001,A Customer,COMG 2025 189299,500.00,500.00,SUCCESSFUL,00\n')
        self.assertEqual(len(out['rows']), 1)
        r = out['rows'][0]
        self.assertEqual(r['contract_number'], 'COMG2025189299')
        self.assertEqual(r['collected'], Decimal('500.00'))
        self.assertEqual(r['current_status'], 'SUCCESSFUL')

    def test_column_spacing_variants_all_resolve(self):
        """The RealPay export has changed its header spacing at least twice."""
        out = self._csv(
            'Transaction Date,Client Number,Contract Number,Amount Collected,Status\n'
            '2026-08-01,1001,COMG2025189299,500.00,SUCCESSFUL\n')
        self.assertEqual(out['rows'][0]['collected'], Decimal('500.00'))

    def test_a_title_line_above_the_header_is_skipped(self):
        out = self._csv(
            'RealPay Transaction Report — August 2026\n'
            'ContractNumber,Collected Amount,Current Status\n'
            'COMG2025189299,500.00,SUCCESSFUL\n')
        self.assertEqual(len(out['rows']), 1)

    def test_rows_with_no_contract_are_counted_not_silently_dropped(self):
        out = self._csv(
            'ContractNumber,Collected Amount\n'
            'COMG2025189299,500.00\n'
            ',250.00\n')
        self.assertEqual(len(out['rows']), 1)
        self.assertEqual(out['skipped'], 1)

    def test_a_file_with_no_contract_column_says_why(self):
        with self.assertRaises(ValueError) as cm:
            self._csv('ClientName,Amount\nA Customer,500.00\n')
        self.assertIn('contract number', str(cm.exception).lower())

    def test_an_unsupported_extension_says_what_to_upload(self):
        with self.assertRaises(ValueError) as cm:
            parse_realpay_file(b'x', 'report.pdf')
        self.assertIn('.xlsb', str(cm.exception))
